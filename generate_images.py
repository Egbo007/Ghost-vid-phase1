#!/usr/bin/env python3
"""Batch image generation — Ashes of the Chosen (Phase B, Brief 008).

Reads episode_NN_image_prompts.json, renders each missing image through the
chosen provider, and records progress in episode_NN_image_manifest.json.
No network calls in --dry-run. Credentials are never printed or logged.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import generate_prompts as gp
from image_providers import get_provider, QuotaError, ProviderError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("generate_images")

MAX_RETRIES = 2          # retries after the first failure
DEFAULT_MAX_IMAGES = 14
USAGE_WARN_THRESHOLD = 60


def probe_pixels(path):
    """Pixel size via ffprobe as (width, height), or None."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
           str(path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, check=True)
        parts = res.stdout.strip().split("x")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


def prompts_path(ep_dir, ep_str):
    return ep_dir / f"episode_{ep_str}_image_prompts.json"


def manifest_path(ep_dir, ep_str):
    return ep_dir / f"episode_{ep_str}_image_manifest.json"


def load_manifest(path):
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Could not read manifest {path.name}: {type(e).__name__}. Starting fresh.")
        return []


def save_manifest(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def record(manifest, label, entry, provider_name, status, attempts, error=None,
           pixels=None):
    """Upserts one label in the manifest."""
    item = {
        "label": label,
        "file": entry["file_name"],
        "prompt_used": entry["prompt"],
        "provider": provider_name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "attempts": attempts,
        "status": status,
        "pixels": list(pixels) if pixels else None,
    }
    if error:
        item["error"] = error
    for index, existing in enumerate(manifest):
        if existing.get("label") == label:
            manifest[index] = item
            return
    manifest.append(item)


def today_usage_count(ep_dir):
    """Counts today's entries in the shared phase-b-tests usage_log.jsonl."""
    log_path = ep_dir.parent / "phase-b-tests" / "usage_log.jsonl"
    if not log_path.exists():
        return 0
    today = date.today().isoformat()
    count = 0
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if json.loads(line).get("timestamp", "").startswith(today):
                        count += 1
                except Exception:
                    continue
    except Exception:
        return 0
    return count


def select_entries(entries, only, max_images):
    if only:
        wanted = {x.strip() for x in only.split(",") if x.strip()}
        entries = [e for e in entries if e["image_label"] in wanted]
        missing = wanted - {e["image_label"] for e in entries}
        if missing:
            logger.warning(f"Unknown image label(s): {', '.join(sorted(missing))}")
    return entries[:max_images]


def print_summary(entries, ep_dir, provider_name, rendered, skipped, failed,
                  remaining):
    print("\n=== IMAGE GENERATION SUMMARY ===")
    print(f"{'label':<8}{'file':<18}{'scene':<7}{'refs':<6}{'status'}")
    print("-" * 60)
    for e in entries:
        path = ep_dir / e["file_name"]
        status = "exists" if path.exists() else "missing"
        print(f"{e['image_label']:<8}{e['file_name']:<18}{e['scene']:<7}"
              f"{len(e['reference_paths']):<6}{status}")
    print("-" * 60)
    print(f"provider : {provider_name}")
    print(f"rendered : {rendered}")
    print(f"skipped  : {skipped}")
    print(f"failed   : {failed}")
    print(f"remaining: {remaining}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Render episode images through a provider")
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--provider", default="cloudflare")
    parser.add_argument("--only", default=None, help="Comma-separated labels, e.g. 3a,5b")
    parser.add_argument("--force", action="store_true", help="Overwrite existing images")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-images", type=int, default=DEFAULT_MAX_IMAGES)
    parser.add_argument("--width", type=int, default=720)
    parser.add_argument("--height", type=int, default=1280)
    return parser.parse_args(argv)


def main(argv=None, base_dir=None, generate_fn=None, provider=None):
    args = parse_args(argv)
    ep_str = f"{args.episode:02d}"
    base = (Path(base_dir) if base_dir else
            Path(os.environ.get("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen")))
    ep_dir = base / f"episode-{ep_str}"
    p_path = prompts_path(ep_dir, ep_str)
    m_path = manifest_path(ep_dir, ep_str)
    build_prompts = generate_fn or gp.generate_prompts

    if not p_path.exists():
        logger.info("Prompts JSON missing; generating it from the script.")
        try:
            build_prompts(args.episode)
        except Exception as e:
            logger.error(f"Could not generate prompts: {type(e).__name__}: {e}")
            return 1
    with open(p_path, "r", encoding="utf-8") as f:
        all_entries = json.load(f)

    entries = select_entries(all_entries, args.only, args.max_images)
    manifest = load_manifest(m_path)

    if args.dry_run:
        print("=== DRY RUN — no network request, no images written ===")
        for e in entries:
            path = ep_dir / e["file_name"]
            print(f"{e['image_label']:<6} {e['file_name']:<18} "
                  f"refs={len(e['reference_paths'])} "
                  f"exists={path.exists()}")
        print(f"Total: {len(entries)} image(s), provider={args.provider}")
        return 0

    if provider is not None:
        provider_obj = provider
    else:
        try:
            provider_obj = get_provider(args.provider)
        except ValueError as e:
            logger.error(str(e))
            return 1

    usage = today_usage_count(ep_dir)
    if usage > USAGE_WARN_THRESHOLD:
        logger.warning(
            f"usage_log.jsonl already has {usage} requests today "
            f"(threshold {USAGE_WARN_THRESHOLD}). The free allowance may run out."
        )

    rendered = skipped = failed = 0
    quota_hit = False
    for e in entries:
        label = e["image_label"]
        out_path = ep_dir / e["file_name"]
        if out_path.exists() and not args.force:
            skipped += 1
            continue

        attempts = 0
        while attempts <= MAX_RETRIES:
            attempts += 1
            try:
                provider_obj.generate(
                    e["prompt"], args.width or e.get("width", 720),
                    args.height or e.get("height", 1280),
                    e.get("reference_paths", []), out_path
                )
                pixels = probe_pixels(out_path)
                expected = (args.width or e.get("width", 720),
                            args.height or e.get("height", 1280))
                if pixels != expected:
                    raise ProviderError(
                        f"Pixel size {pixels} does not match requested {expected}"
                    )
                record(manifest, label, e, args.provider, "ok", attempts, pixels=pixels)
                save_manifest(m_path, manifest)
                rendered += 1
                logger.info(f"{label}: saved {out_path.name} ({pixels[0]}x{pixels[1]})")
                break
            except QuotaError as qe:
                record(manifest, label, e, args.provider, "quota", attempts,
                       error=str(qe))
                save_manifest(m_path, manifest)
                logger.error(f"{label}: quota reached. Stopping cleanly.")
                quota_hit = True
                break
            except Exception as ex:
                if attempts <= MAX_RETRIES:
                    logger.warning(f"{label}: attempt {attempts} failed "
                                   f"({type(ex).__name__}); retrying")
                    continue
                record(manifest, label, e, args.provider, "failed", attempts,
                       error=f"{type(ex).__name__}: {ex}")
                save_manifest(m_path, manifest)
                failed += 1
                logger.error(f"{label}: failed after {attempts} attempts "
                             f"({type(ex).__name__}). Continuing.")
                break

        if quota_hit:
            break

    remaining = sum(
        1 for e in entries if not (ep_dir / e["file_name"]).exists()
    )
    print_summary(entries, ep_dir, args.provider, rendered, skipped, failed, remaining)
    if quota_hit:
        print(f"\nQuota reached. {remaining} image(s) remain. "
              "Run the same command again later to resume.")
        return 2
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
