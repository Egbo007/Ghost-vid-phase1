#!/usr/bin/env python3
"""
Placeholder image generator — Ashes of the Chosen

Creates solid-colour PNGs for every scene so Modules 4-6 can be exercised before
real scene images exist. No fonts required: colours and an inset border are drawn
with ffmpeg's lavfi sources, so crop behaviour is visible by eye.

- Scene 1 gets two images with different colours (exercises multi-image splitting)
- Scene 2 is 768x768 and scene 3 is 864x1536 (exercise cover-fit cropping)
- All other scenes are 1080x1920
- Existing files are kept unless --force

Output: episode-XX/scene_NNa.png (and scene_01b.png for scene 1)
"""

import os
import sys
import json
import logging
import argparse
import subprocess
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("make_placeholders")

DEFAULT_OUTPUT_DIR = "./ashes-of-the-chosen"

# Per-scene colours, cycled if there are more scenes than colours.
SCENE_COLOURS = [
    "0x1B2A4A",  # deep navy
    "0x4A1B2A",  # deep wine
    "0x1B4A2A",  # deep green
    "0x4A3A1B",  # deep ochre
    "0x2A1B4A",  # deep violet
    "0x1B4A4A",  # deep teal
    "0x4A1B1B",  # deep red
    "0x2A4A1B",  # deep olive
]

# Second colour for scene 1's second image, so the split is obvious.
SECOND_IMAGE_COLOUR = "0x8A6A2A"

BORDER_COLOUR = "0xE8D8A8"
BORDER_INSET = 60

# (width, height) overrides by scene number.
SCENE_SIZES = {
    2: (768, 768),
    3: (864, 1536),
}
DEFAULT_SIZE = (1080, 1920)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Generate placeholder scene images for Ashes of the Chosen"
    )
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory")
    parser.add_argument("--force", action="store_true", help="Overwrite existing placeholder images")
    return parser.parse_args()


def load_environment():
    return os.getenv("EPISODE_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)


def run_ffmpeg(cmd, label):
    """Runs ffmpeg; on failure logs the last 15 stderr lines and exits 1."""
    logger.info(f"FFmpeg: {label}")
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace"
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-15:]
        logger.error(f"FFmpeg failed during {label} (exit {proc.returncode}). Last {len(tail)} stderr lines:")
        for line in tail:
            logger.error(f"  {line}")
        sys.exit(1)
    return proc


def plan_placeholders(scene_numbers):
    """
    Returns a list of {"scene", "suffix", "width", "height", "colour"} describing
    every placeholder to create. Scene 1 gets two images.
    """
    plan = []
    for index, scene in enumerate(scene_numbers):
        colour = SCENE_COLOURS[index % len(SCENE_COLOURS)]
        width, height = SCENE_SIZES.get(scene, DEFAULT_SIZE)
        plan.append({
            "scene": scene,
            "suffix": "a" if scene == 1 else "",
            "width": width,
            "height": height,
            "colour": colour,
        })
        if scene == 1:
            plan.append({
                "scene": scene,
                "suffix": "b",
                "width": DEFAULT_SIZE[0],
                "height": DEFAULT_SIZE[1],
                "colour": SECOND_IMAGE_COLOUR,
            })
    return plan


def placeholder_filename(scene, suffix):
    return f"scene_{scene:02d}{suffix}.png"


def build_ffmpeg_command(item, out_path):
    """Solid colour plus a lighter inset border, drawn with lavfi only."""
    w, h, colour = item["width"], item["height"], item["colour"]
    inner_w = max(2, w - 2 * BORDER_INSET)
    inner_h = max(2, h - 2 * BORDER_INSET)
    filter_expr = (
        f"drawbox=x=0:y=0:w={w}:h={h}:color={BORDER_COLOUR}:t=fill,"
        f"drawbox=x={BORDER_INSET}:y={BORDER_INSET}:w={inner_w}:h={inner_h}:color={colour}:t=fill"
    )
    return [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={BORDER_COLOUR}:s={w}x{h}:d=1",
        "-vf", filter_expr,
        "-frames:v", "1",
        str(out_path)
    ]


def main():
    args = parse_arguments()
    base_out_dir = Path(args.output_dir or load_environment())
    ep_str = f"{args.episode:02d}"
    ep_dir = base_out_dir / f"episode-{ep_str}"

    if not ep_dir.exists():
        logger.error(f"Episode directory not found: {ep_dir}. Run Module 2 first.")
        sys.exit(1)

    durations_json = ep_dir / f"episode_{ep_str}_scene_durations.json"
    if not durations_json.exists():
        logger.error(f"Missing scene durations JSON: {durations_json}. Run Module 2 first.")
        sys.exit(1)

    with open(durations_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    scene_numbers = [sc["scene"] for sc in data.get("scenes", [])]
    if not scene_numbers:
        logger.error(f"No scenes listed in {durations_json}.")
        sys.exit(1)

    plan = plan_placeholders(scene_numbers)
    created = 0
    skipped = 0

    for item in plan:
        out_path = ep_dir / placeholder_filename(item["scene"], item["suffix"])
        if out_path.exists() and not args.force:
            logger.info(f"Keeping existing {out_path.name} (use --force to overwrite)")
            skipped += 1
            continue
        logger.info(
            f"Scene {item['scene']}{item['suffix']}: {item['width']}x{item['height']} "
            f"colour {item['colour']} -> {out_path.name}"
        )
        run_ffmpeg(build_ffmpeg_command(item, out_path), f"placeholder {out_path.name}")
        created += 1

    logger.info(
        f"Placeholders ready for Episode {args.episode}: {created} created, {skipped} kept."
    )


if __name__ == "__main__":
    main()
