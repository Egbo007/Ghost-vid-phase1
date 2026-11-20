#!/usr/bin/env python3
"""Automatic image checks + contact sheet + approval — Ashes of the Chosen.

Uses ffmpeg/ffprobe only. No network calls and no credentials. Never deletes
an image. Flags are advisory: they are highlighted but never block approval.
"""

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("review_images")

# Thresholds for the near-black / near-white check (ffmpeg signalstats, YAVG
# and YMIN/YMAX on a 0-255 scale). A frame is flagged only when its average
# luma is extreme AND its whole-frame luma range is tiny. Requiring both
# avoids flagging a legitimately flat mid-tone background.
LUMA_MIN = 20.0
LUMA_MAX = 230.0
LUMA_RANGE_MIN = 8.0
ASPECT_TOLERANCE = 0.01     # 1% around 9:16
TARGET_ASPECT = 9.0 / 16.0
THUMB_W = 270
THUMB_H = 480
GRID_COLS = 4


def probe_stream(path):
    """ffprobe width/height for an image. Returns (w, h) or None."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
           str(path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, check=True)
        w, h = res.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return None


def decode_ok(path):
    """True when ffmpeg can fully decode the file."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path),
           "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
        return res.returncode == 0
    except Exception:
        return False


def luma_stats(path):
    """Returns (yavg, ymin, ymax) from signalstats, or None."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
           "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG,"
                  "metadata=print:key=lavfi.signalstats.YMIN,"
                  "metadata=print:key=lavfi.signalstats.YMAX",
           "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    except Exception:
        return None
    if res.returncode != 0:
        return None
    text = res.stdout + res.stderr
    values = {}
    for key in ("YAVG", "YMIN", "YMAX"):
        marker = f"lavfi.signalstats.{key}="
        idx = text.rfind(marker)
        if idx == -1:
            return None
        try:
            values[key] = float(text[idx + len(marker):].split()[0])
        except (ValueError, IndexError):
            return None
    return values["YAVG"], values["YMIN"], values["YMAX"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_entries(ep_dir, ep_str):
    """Reads the prompts JSON (falling back to the manifest) for image order."""
    p_path = ep_dir / f"episode_{ep_str}_image_prompts.json"
    if p_path.exists():
        with open(p_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [{"label": e["image_label"], "file": e["file_name"]}
                    for e in data]
    m_path = ep_dir / f"episode_{ep_str}_image_manifest.json"
    if m_path.exists():
        with open(m_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [{"label": e["label"], "file": e["file"]} for e in data]
    return []


def load_review(path):
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Could not read review file: {type(e).__name__}")
        return []


def save_review(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def check_image(path, digest_map):
    """Runs every check on one image. Returns (checks, flags)."""
    checks = {}
    flags = []

    exists = path.exists()
    checks["exists"] = exists
    if not exists:
        flags.append("missing")
        return checks, flags

    decoded = decode_ok(path)
    checks["decodes"] = decoded
    if not decoded:
        flags.append("decode_failed")

    size = probe_stream(path)
    checks["pixels"] = list(size) if size else None
    if size:
        aspect = size[0] / size[1] if size[1] else 0
        checks["aspect"] = round(aspect, 5)
        ok = abs(aspect - TARGET_ASPECT) / TARGET_ASPECT <= ASPECT_TOLERANCE
        checks["aspect_ok"] = ok
        if not ok:
            flags.append("wrong_aspect")
    else:
        checks["aspect_ok"] = False
        flags.append("no_pixels")

    stats = luma_stats(path)
    checks["luma"] = list(stats) if stats else None
    if stats:
        yavg, ymin, ymax = stats
        extreme = yavg <= LUMA_MIN or yavg >= LUMA_MAX
        if extreme and (ymax - ymin) < LUMA_RANGE_MIN:
            flags.append("blank_or_flat")
    else:
        flags.append("luma_unreadable")

    digest = sha256(path)
    checks["sha256"] = digest
    if digest in digest_map:
        flags.append(f"duplicate_of:{digest_map[digest]}")
    else:
        digest_map[digest] = path.name

    return checks, flags


def build_contact_sheet(entries, ep_dir, out_path):
    """4-column grid of 270x480 thumbnails, ordered by scene then a/b."""
    available = [e for e in entries if (ep_dir / e["file"]).exists()]
    if not available:
        return None
    # One filter_complex with scale+pad per input, then xstack.
    cmd = ["ffmpeg", "-y"]
    for e in available:
        cmd += ["-i", str(ep_dir / e["file"])]
    filters = []
    for i in range(len(available)):
        filters.append(
            f"[{i}:v]scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease,"
            f"pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2,setsar=1[t{i}]"
        )
    n = len(available)
    cols = GRID_COLS
    rows = (n + cols - 1) // cols
    # xstack needs exactly cols*rows inputs; pad the last row with black.
    padded = []
    for i in range(n):
        padded.append(f"[t{i}]")
    for i in range(n, cols * rows):
        input_index = n + (i - n)
        cmd += ["-f", "lavfi", "-i", f"color=c=black:s={THUMB_W}x{THUMB_H}:d=1"]
        filters.append(f"[{input_index}:v]null[t{i}]")
        padded.append(f"[t{i}]")
    layout = "|".join(
        f"{col}_{row}" for row in range(rows) for col in range(cols)
    )
    # xstack input order is row-major; our padded list is already row-major.
    inputs = "".join(padded)
    grid_w, grid_h = cols * THUMB_W, rows * THUMB_H
    filters.append(f"{inputs}xstack=inputs={cols * rows}:layout={layout}[stacked]")
    filters.append(
        f"[stacked]scale={grid_w}:{grid_h}:flags=neighbor,setsar=1[grid]"
    )
    cmd += ["-filter_complex", ";".join(filters), "-map", "[grid]",
            "-frames:v", "1", "-q:v", "3", str(out_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
        if res.returncode == 0 and out_path.exists():
            return out_path
        logger.warning("Contact sheet ffmpeg failed: "
                       + "\n".join((res.stderr or "").strip().splitlines()[-6:]))
    except Exception as e:
        logger.warning(f"Contact sheet failed: {type(e).__name__}")
    return None


def print_table(entries, sheet_path):
    print("\n=== IMAGE REVIEW ===")
    print(f"{'label':<8}{'file':<18}{'status':<10}{'flags'}")
    print("-" * 68)
    for e in entries:
        flags = ", ".join(e.get("flags", [])) or "-"
        status = "APPROVED" if e.get("approved") else "pending"
        print(f"{e['label']:<8}{e['file']:<18}{status:<10}{flags}")
    print("-" * 68)
    if sheet_path:
        print(f"Contact sheet: {sheet_path}")
        print("Legend (row, column) -> label:")
        for i, e in enumerate(entries):
            row, col = divmod(i, GRID_COLS)
            flags = ", ".join(e.get("flags", [])) or "clean"
            print(f"  ({row + 1}, {col + 1}) -> {e['label']}  [{flags}]")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Review and approve episode images")
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--open", action="store_true",
                        help="Open the contact sheet with the Windows default viewer")
    parser.add_argument("--approve", nargs="*", metavar="LABEL", default=None,
                        help="Approve these labels; no labels = all without hard failures")
    parser.add_argument("--regenerate", nargs="+", metavar="LABEL", default=None,
                        help="Regenerate these labels with --force, then recheck")
    return parser.parse_args(argv)


def main(argv=None, base_dir=None):
    args = parse_args(argv)
    ep_str = f"{args.episode:02d}"
    base = (Path(base_dir) if base_dir else
            Path(os.environ.get("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen")))
    ep_dir = base / f"episode-{ep_str}"
    if not ep_dir.exists():
        logger.error(f"Episode directory not found: {ep_dir}")
        return 1

    review_path = ep_dir / f"episode_{ep_str}_image_review.json"
    sheet_path = ep_dir / f"episode_{ep_str}_contact_sheet.jpg"
    entries_spec = expected_entries(ep_dir, ep_str)
    if not entries_spec:
        logger.error("No image manifest or prompts JSON found for this episode.")
        return 1

    # Regeneration happens first, so the checks describe the new files.
    if args.regenerate:
        labels = args.regenerate
        cmd = [sys.executable, "generate_images.py", "--episode", str(args.episode),
               "--only", ",".join(labels), "--force"]
        logger.info(f"Regenerating: {', '.join(labels)}")
        try:
            subprocess.run(cmd, check=True)
        except Exception as e:
            logger.error(f"Regeneration failed: {type(e).__name__}")
            return 1

    previous = {e["label"]: e for e in load_review(review_path)}
    digest_map = {}
    review = []
    for spec in entries_spec:
        label, file_name = spec["label"], spec["file"]
        checks, flags = check_image(ep_dir / file_name, digest_map)
        approved = previous.get(label, {}).get("approved", False)
        approved_at = previous.get(label, {}).get("approved_at")
        if args.regenerate and label in args.regenerate:
            approved, approved_at = False, None
        review.append({
            "label": label,
            "file": file_name,
            "checks": checks,
            "flags": flags,
            "approved": approved,
            "approved_at": approved_at,
        })

    if args.approve is not None:
        targets = set(args.approve) if args.approve else None
        now = datetime.now().isoformat(timespec="seconds")
        for e in review:
            if targets is None or e["label"] in targets:
                e["approved"] = True
                e["approved_at"] = now

    save_review(review_path, review)
    sheet = build_contact_sheet(review, ep_dir, sheet_path)
    print_table(review, sheet)

    if args.open and sheet:
        try:
            os.startfile(str(sheet))
        except Exception as e:
            logger.warning(f"Could not open contact sheet: {type(e).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
