#!/usr/bin/env python3
"""Image provider interface — Ashes of the Chosen (Phase B, Brief 008).

Each provider exposes generate(prompt, width, height, reference_paths,
out_path) -> saved path. No provider prints or logs credentials.
"""

import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import try_cloudflare_image as cf

logger = logging.getLogger("image_providers")


class QuotaError(Exception):
    """Raised when the provider reports HTTP 429 or an explicit quota error."""


class ProviderError(Exception):
    """Any other provider failure. Retried by generate_images.py."""


class BaseProvider:
    name = "base"

    def generate(self, prompt, width, height, reference_paths, out_path):
        raise NotImplementedError


class CloudflareProvider(BaseProvider):
    """
    Calls Cloudflare Workers AI via try_cloudflare_image's own functions.

    Reference preparation and the request/retry loop are reused, not copied.
    The token is read by try_cloudflare_image.read_credentials() and is never
    returned, logged or included in an exception message.
    """
    name = "cloudflare"

    def __init__(self, repo_root=None):
        self.repo_root = Path(repo_root) if repo_root else cf.REPO_ROOT

    def generate(self, prompt, width, height, reference_paths, out_path):
        account_id, token, missing = cf.read_credentials()
        if missing:
            raise ProviderError(
                f"Missing credentials in .env: {', '.join(missing)}"
            )

        out_path = Path(out_path)
        out_dir = out_path.parent
        prepared = []
        if reference_paths:
            try:
                prepared = cf.prepare_references(reference_paths, out_dir)
            except RuntimeError as e:
                raise ProviderError(str(e))

        endpoint = cf.build_endpoint(account_id)
        status, payload, attempts = cf.request_with_retries(
            endpoint, token, prompt, width, height, references=prepared
        )

        if status == 429:
            raise QuotaError(
                "Cloudflare returned HTTP 429 (daily allowance may be used up)."
            )
        if status is None:
            raise ProviderError("No response from Cloudflare.")
        if status != 200:
            raise ProviderError(
                f"Cloudflare returned HTTP {status}: {cf.cloudflare_error_message(payload)}"
            )

        image_bytes, problem = cf.extract_image_bytes(payload)
        if image_bytes is None:
            raise ProviderError(f"Could not read an image from the response: {problem}")
        ext = cf.detect_image_format(image_bytes)
        if ext is None:
            raise ProviderError("Response bytes are neither PNG nor JPEG.")

        out_dir.mkdir(parents=True, exist_ok=True)
        # Keep the exact name assemble_video.py expects; the bytes decide the
        # real extension, so a JPEG response is written as .jpg even if the
        # contract path ended in .png. Callers check the manifest file path.
        out_path.write_bytes(image_bytes)
        logger.info(f"Saved {out_path.name} ({len(image_bytes)} bytes, {attempts} attempt(s))")
        return out_path


class GeminiProvider(BaseProvider):
    name = "gemini"

    def generate(self, prompt, width, height, reference_paths, out_path):
        raise NotImplementedError(
            "GeminiProvider is not built yet. Use --provider cloudflare."
        )


PROVIDERS = {
    CloudflareProvider.name: CloudflareProvider,
    GeminiProvider.name: GeminiProvider,
}


def get_provider(name, **kwargs):
    """Looks a provider up by name. Raises ValueError with the valid names."""
    key = (name or "").strip().lower()
    if key not in PROVIDERS:
        raise ValueError(
            f"Unknown provider '{name}'. Available: {', '.join(sorted(PROVIDERS))}"
        )
    return PROVIDERS[key](**kwargs)


def register_provider(name, cls):
    """Registers a provider class by name (used by the fake provider in Brief 010)."""
    PROVIDERS[name.strip().lower()] = cls
