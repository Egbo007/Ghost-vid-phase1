#!/usr/bin/env python3
"""
Phase B: first real Cloudflare image — Ashes of the Chosen

Sends ONE text-to-image request to Cloudflare Workers AI
(@cf/black-forest-labs/flux-2-klein-4b) and saves the returned image, so the
image quality and the token can be judged before this is wired into the pipeline.

This is a probe, not a module. It does not touch episode folders or any JSON the
pipeline depends on.

Credentials come from .env via python-dotenv:
    CLOUDFLARE_ACCOUNT_ID
    CLOUDFLARE_API_TOKEN
They are never printed, logged, or included in any error message. The endpoint
URL is always shown with the account ID masked.

Usage:
    python try_cloudflare_image.py --scene "close-up portrait, candle-lit hall" --dry-run
    python try_cloudflare_image.py --scene "close-up portrait, candle-lit hall"
"""

import os
import sys
import json
import time
import base64
import argparse
import logging
import subprocess
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logger_boot = logging.getLogger("try_cloudflare_image")
    logger_boot.warning("python-dotenv not installed; relying on the process environment only")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("try_cloudflare_image")

MODEL_ID = "@cf/black-forest-labs/flux-2-klein-4b"
ENDPOINT_TEMPLATE = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/" + MODEL_ID
)
DEFAULT_CHARACTER = "ADA"
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 1280
DEFAULT_OUT = Path("ashes-of-the-chosen") / "phase-b-tests"
MAX_ATTEMPTS = 3          # one try plus up to 2 retries
RETRY_BACKOFF_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 120

# Reference images: Cloudflare accepts up to 4, each smaller than 512x512, as
# multipart fields input_image_0..input_image_3. We prepare each one as a
# 448x448 PNG so it is always inside the limit whatever the source aspect.
MAX_REFERENCES = 4
REFERENCE_SIZE = 448

# The card fields that make up the character's visual identity, in the order
# they are joined. `image_prompt_card` is the physical description;
# `mandatory_always` carries the visual fingerprint that must never be dropped.
CARD_FIELDS = ("image_prompt_card", "mandatory_always")

REPO_ROOT = Path(__file__).resolve().parent
CHARACTER_CARDS_PATH = REPO_ROOT / "character_cards.json"
SERIES_CONFIG_PATH = REPO_ROOT / "series_config.json"


# ------------------------------------------------------------------ prompt

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_character_card(cards_data, character_id):
    """Returns the card dict for character_id, or raises KeyError with the valid ids."""
    cards = cards_data.get("character_cards", {})
    card = cards.get(character_id)
    if not isinstance(card, dict):
        valid = sorted(
            k for k, v in cards.items() if isinstance(v, dict) and not k.startswith("_")
        )
        raise KeyError(
            f"Character '{character_id}' not found in character_cards.json. "
            f"Valid ids: {', '.join(valid)}"
        )
    return card


def get_style_line(config_data):
    """
    The series-wide visual style line. `overall_aesthetic` is the field that
    describes how the show should look; returns None if it is absent.
    """
    series = config_data.get("series", {})
    style = series.get("overall_aesthetic")
    if isinstance(style, str) and style.strip():
        return style.strip()
    return None


def build_prompt(card, scene_text, style_line=None):
    """
    Character description (verbatim from the card) + scene + style line.

    The card text is used exactly as stored, never retyped or paraphrased: it is
    the mechanism that keeps the character looking the same across episodes.
    Segments are joined on a single space when the previous one already ends in
    punctuation, so the card's own full stop is not doubled.
    """
    parts = []
    for field in CARD_FIELDS:
        value = card.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    if scene_text and scene_text.strip():
        parts.append(scene_text.strip())
    if style_line:
        parts.append(style_line)

    prompt = ""
    for part in parts:
        if not prompt:
            prompt = part
        elif prompt.endswith((".", "!", "?", ";", ":")):
            prompt = f"{prompt} {part}"
        else:
            prompt = f"{prompt}. {part}"
    return prompt


def describe_prompt_sources(card, style_line):
    """Which card/config keys fed the prompt, for the report."""
    used = [f for f in CARD_FIELDS if isinstance(card.get(f), str) and card.get(f).strip()]
    return used, ("series.overall_aesthetic" if style_line else None)


# ------------------------------------------------------------------ references

def validate_reference_paths(paths):
    """
    Checks the reference list before anything is prepared or sent.

    Returns (ok, error_message). Errors name the offending path so a typo is
    obvious rather than surfacing as an ffmpeg failure later.
    """
    if not paths:
        return True, None
    if len(paths) > MAX_REFERENCES:
        return False, (
            f"Too many reference images: {len(paths)} given, "
            f"Cloudflare accepts at most {MAX_REFERENCES}."
        )
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            return False, f"Reference image not found: {raw}"
        if not path.is_file():
            return False, f"Reference path is not a file: {raw}"
    return True, None


def prepare_reference(source_path, out_dir, index):
    """
    Centre-crops a reference to a square and scales it to REFERENCE_SIZE.

    The original file is only read, never modified. Returns the prepared path.
    Raises RuntimeError with ffmpeg's own last stderr lines on failure.
    """
    source_path = Path(source_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared = out_dir / f"ref_prepared_{index}.png"

    # min(iw,ih) square from the centre, then scale. force_original_aspect_ratio
    # is not needed after a square crop, but setsar=1 keeps the PNG clean.
    vf = (
        f"crop='min(iw,ih)':'min(iw,ih)',"
        f"scale={REFERENCE_SIZE}:{REFERENCE_SIZE}:flags=lanczos,"
        f"setsar=1"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(source_path),
        "-vf", vf,
        "-frames:v", "1",
        str(prepared),
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, check=True)
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg was not found on PATH; it is required to prepare reference images."
        )
    except subprocess.CalledProcessError as e:
        tail = "\n".join((e.stderr or "").strip().splitlines()[-5:])
        raise RuntimeError(
            f"ffmpeg could not prepare reference image {source_path.name}. "
            f"Last output:\n{tail}"
        )
    except Exception as e:
        raise RuntimeError(
            f"ffmpeg could not prepare reference image {source_path.name}: "
            f"{type(e).__name__}"
        )

    if not prepared.exists() or prepared.stat().st_size == 0:
        raise RuntimeError(
            f"ffmpeg reported success but {prepared.name} is missing or empty."
        )
    return prepared


def prepare_references(paths, out_dir):
    """Prepares every reference in the order given. Returns a list of prepared paths."""
    prepared = []
    for index, raw in enumerate(paths):
        prepared.append(prepare_reference(raw, out_dir, index))
    return prepared


def read_prompt_file(path):
    """
    Reads a full prompt from a text file. The file's content IS the prompt, so
    nothing is prepended or appended. Raises RuntimeError on an unreadable or
    empty file.
    """
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Prompt file not found: {path}")
    if not path.is_file():
        raise RuntimeError(f"Prompt file path is not a file: {path}")
    try:
        # utf-8-sig strips a byte-order mark if present. Windows editors
        # (Notepad, PowerShell's Out-File) commonly write one, and a leading
        # U+FEFF would otherwise be sent to the model as part of the prompt.
        text = path.read_text(encoding="utf-8-sig").strip()
    except Exception as e:
        raise RuntimeError(f"Could not read prompt file {path.name}: {type(e).__name__}")
    if not text:
        raise RuntimeError(f"Prompt file is empty: {path.name}")
    return text


# ------------------------------------------------------------------ request

def mask_account_id(account_id):
    """Never show the account ID. Keep only enough to tell two IDs apart."""
    if not account_id:
        return "<missing>"
    if len(account_id) <= 6:
        return "*" * len(account_id)
    return f"{account_id[:3]}...{account_id[-2:]}"


def build_endpoint(account_id):
    return ENDPOINT_TEMPLATE.format(account_id=account_id)


def read_credentials():
    """Reads credentials from the environment. Never logs or returns them to output."""
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    missing = [
        name for name, value in (
            ("CLOUDFLARE_ACCOUNT_ID", account_id),
            ("CLOUDFLARE_API_TOKEN", token),
        ) if not value
    ]
    return account_id, token, missing


def detect_image_format(data):
    """PNG vs JPEG from the magic bytes. Returns the extension, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    return None


def extract_image_bytes(payload):
    """
    Pulls the base64 image out of the Workers AI JSON response.

    The documented shape is {"result": {"image": "<base64>"}}. Anything else is
    reported by top-level key names only, so an unexpected response cannot leak
    a credential or dump a huge blob into the terminal.
    """
    if not isinstance(payload, dict):
        return None, f"response was {type(payload).__name__}, expected a JSON object"

    result = payload.get("result")
    if isinstance(result, dict):
        image = result.get("image")
        if isinstance(image, str) and image.strip():
            try:
                return base64.b64decode(image), None
            except Exception as e:
                return None, f"result.image was not valid base64 ({type(e).__name__})"
        return None, (
            f"result has no 'image' string; its keys are: "
            f"{', '.join(sorted(result.keys())) or '(none)'}"
        )

    if isinstance(result, str) and result.strip():
        # Some Workers AI models return the base64 string directly under result.
        try:
            return base64.b64decode(result), None
        except Exception:
            pass

    return None, (
        f"unexpected response shape; top-level keys are: "
        f"{', '.join(sorted(payload.keys())) or '(none)'}"
    )


def cloudflare_error_message(payload):
    """Cloudflare's own error text, without any credential material."""
    if not isinstance(payload, dict):
        return "no JSON error body"
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        messages = []
        for item in errors:
            if isinstance(item, dict):
                code = item.get("code")
                message = item.get("message")
                messages.append(f"{code}: {message}" if code is not None else str(message))
            else:
                messages.append(str(item))
        return "; ".join(messages)
    return "no error message in response"


def post_image(endpoint, token, prompt, width, height, session=None, references=None):
    """
    One multipart request. Returns (status_code, payload_or_None, raw_bytes_or_None).

    The token is passed as a header and never appears in a log line or exception.
    `references` is a list of prepared image paths, sent as input_image_0, _1, ...
    in the order given.
    """
    if session is None:
        import requests as session

    files = {
        "prompt": (None, prompt),
        "width": (None, str(width)),
        "height": (None, str(height)),
    }
    for index, ref_path in enumerate(references or []):
        ref_path = Path(ref_path)
        files[f"input_image_{index}"] = (
            ref_path.name,
            ref_path.read_bytes(),
            "image/png",
        )

    headers = {"Authorization": f"Bearer {token}"}
    response = session.post(
        endpoint, headers=headers, files=files, timeout=REQUEST_TIMEOUT_SECONDS
    )
    try:
        payload = response.json()
    except Exception:
        payload = None
    return response.status_code, payload, getattr(response, "content", None)


def request_with_retries(endpoint, token, prompt, width, height, session=None,
                         sleep=time.sleep, references=None):
    """
    Sends the request, retrying up to MAX_ATTEMPTS on 429 and 5xx.

    Returns (status_code, payload, attempts). The caller decides what to print.
    """
    last_status, last_payload = None, None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            status, payload, _ = post_image(
                endpoint, token, prompt, width, height, session=session,
                references=references
            )
        except Exception as e:
            # Network-level failure: report the exception type only. A requests
            # exception can carry the URL, so its message is not printed.
            logger.error(f"Request failed before a response: {type(e).__name__}")
            return None, None, attempt

        last_status, last_payload = status, payload
        if status == 429 or 500 <= status < 600:
            if attempt < MAX_ATTEMPTS:
                logger.warning(
                    f"HTTP {status} on attempt {attempt}/{MAX_ATTEMPTS}; "
                    f"retrying in {RETRY_BACKOFF_SECONDS}s"
                )
                sleep(RETRY_BACKOFF_SECONDS)
                continue
        return status, payload, attempt
    return last_status, last_payload, MAX_ATTEMPTS


# ------------------------------------------------------------------ output

def probe_pixels(path):
    """Pixel dimensions via ffprobe. Returns a string or None."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        str(path),
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, check=True)
        return res.stdout.strip() or None
    except Exception:
        return None


def append_usage_log(out_dir, width, height, status, model=MODEL_ID, reference_count=0):
    """One JSONL line per request, for tracking the free daily allowance."""
    out_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "width": width,
        "height": height,
        "http_status": status,
        "reference_count": reference_count,
    }
    with open(out_dir / "usage_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def save_image(out_dir, data, ext):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"cf_{stamp}.{ext}"
    path.write_bytes(data)
    return path


# ------------------------------------------------------------------ main

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Send one text-to-image request to Cloudflare Workers AI "
                    "(flux-2-klein-4b) and save the result."
    )
    parser.add_argument("--character", default=DEFAULT_CHARACTER,
                        help=f"Character id from character_cards.json (default: {DEFAULT_CHARACTER})")
    parser.add_argument("--scene",
                        help="Scene description appended to the character card "
                             "(required unless --prompt-file is given)")
    parser.add_argument("--prompt-file", default=None,
                        help="Read the ENTIRE prompt from this UTF-8 text file, "
                             "replacing the character card / scene / style builder")
    parser.add_argument("--reference", action="append", default=None,
                        metavar="PATH",
                        help=f"Reference image for identity (repeatable, max "
                             f"{MAX_REFERENCES}). Each is centre-cropped to a square "
                             f"and scaled to {REFERENCE_SIZE}x{REFERENCE_SIZE} PNG")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                        help=f"Image width in pixels (default: {DEFAULT_WIDTH})")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT,
                        help=f"Image height in pixels (default: {DEFAULT_HEIGHT})")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"Output folder (default: {DEFAULT_OUT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the prompt and planned request, then exit without network")
    return parser.parse_args(argv)


def main(argv=None):
    """Returns a process exit code. Never raises SystemExit, so it is testable."""
    args = parse_args(argv)

    # Prompts are UTF-8 and may contain non-ASCII characters (accented names,
    # em dashes). A Windows console defaults to cp1252, which raises on those,
    # so stdout is set to replace unencodable characters instead of crashing.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # --- prompt source: a file replaces the builder entirely -------------
    if args.prompt_file and args.scene:
        logger.error(
            "Use either --scene or --prompt-file, not both. "
            "--prompt-file supplies the whole prompt; --scene is appended to the card."
        )
        return 1
    if not args.prompt_file and not args.scene:
        logger.error("One of --scene or --prompt-file is required.")
        return 1

    references = args.reference or []
    ok, ref_error = validate_reference_paths(references)
    if not ok:
        logger.error(ref_error)
        return 1

    out_dir = Path(args.out)

    if args.prompt_file:
        try:
            prompt = read_prompt_file(args.prompt_file)
        except RuntimeError as e:
            logger.error(str(e))
            return 1
        prompt_source = f"file: {args.prompt_file}"
        card = {}
        card_fields, style_source = [], None
    else:
        try:
            cards_data = load_json(CHARACTER_CARDS_PATH)
            config_data = load_json(SERIES_CONFIG_PATH)
        except Exception as e:
            logger.error(f"Could not read character_cards.json or series_config.json: {e}")
            return 1

        try:
            card = get_character_card(cards_data, args.character)
        except KeyError as e:
            logger.error(str(e).strip("'\""))
            return 1

        style_line = get_style_line(config_data)
        prompt = build_prompt(card, args.scene, style_line)
        card_fields, style_source = describe_prompt_sources(card, style_line)
        prompt_source = "character card + scene + style"

    # --- prepare references (ffmpeg) -------------------------------------
    prepared_refs = []
    if references:
        try:
            prepared_refs = prepare_references(references, out_dir)
        except RuntimeError as e:
            logger.error(str(e))
            return 1

    if args.dry_run:
        print("=== DRY RUN — no network request ===")
        print(f"prompt source    : {prompt_source}")
        if not args.prompt_file:
            print(f"character        : {args.character} ({card.get('full_name', '?')})")
            print(f"card fields used : {', '.join(card_fields)}")
            print(f"style source     : {style_source or '(none found)'}")
        print(f"model            : {MODEL_ID}")
        print(f"endpoint         : {ENDPOINT_TEMPLATE.format(account_id='<ACCOUNT_ID masked>')}")
        print(f"width x height   : {args.width} x {args.height}")
        print(f"output folder    : {args.out}")
        print(f"references       : {len(prepared_refs)} of max {MAX_REFERENCES}")
        for index, (raw, prepared) in enumerate(zip(references, prepared_refs)):
            pixels = probe_pixels(prepared) or "unknown"
            print(f"  input_image_{index}:")
            print(f"    original : {raw}")
            print(f"    prepared : {prepared}")
            print(f"    pixels   : {pixels}")
            print(f"    bytes    : {prepared.stat().st_size}")
        print(f"prompt length    : {len(prompt)} characters")
        print("")
        print("--- PROMPT ---")
        print(prompt)
        print("--- END PROMPT ---")
        return 0

    account_id, token, missing = read_credentials()
    if missing:
        logger.error(
            f"Missing credentials in .env: {', '.join(missing)}. "
            "Add them to .env and try again."
        )
        return 1

    endpoint = build_endpoint(account_id)
    logger.info(f"Requesting {args.width}x{args.height} from {MODEL_ID} "
                f"with {len(prepared_refs)} reference image(s)")
    logger.info(f"Endpoint: {ENDPOINT_TEMPLATE.format(account_id=mask_account_id(account_id))}")

    status, payload, attempts = request_with_retries(
        endpoint, token, prompt, args.width, args.height,
        references=prepared_refs
    )

    if status is not None:
        append_usage_log(out_dir, args.width, args.height, status,
                         reference_count=len(prepared_refs))

    if status is None:
        logger.error("No response from Cloudflare; nothing saved.")
        return 1

    if status != 200:
        message = cloudflare_error_message(payload)
        logger.error(f"Cloudflare returned HTTP {status} after {attempts} attempt(s): {message}")
        if status == 429:
            logger.error(
                "HTTP 429: the free daily allowance for this model may be used up. "
                "It resets on Cloudflare's schedule; try again later."
            )
        return 1

    image_bytes, problem = extract_image_bytes(payload)
    if image_bytes is None:
        logger.error(f"Could not read an image from the response: {problem}")
        return 1

    ext = detect_image_format(image_bytes)
    if ext is None:
        logger.error(
            f"Response decoded but the bytes are neither PNG nor JPEG "
            f"(first bytes: {image_bytes[:8].hex()})"
        )
        return 1

    path = save_image(out_dir, image_bytes, ext)
    size_kb = path.stat().st_size / 1024
    pixels = probe_pixels(path)

    print("")
    print(f"Saved   : {path}")
    print(f"Size    : {size_kb:.1f} KB")
    print(f"Pixels  : {pixels or 'could not read with ffprobe'}")
    print(f"Format  : {ext.upper()}")
    print(f"Attempts: {attempts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
