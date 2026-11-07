#!/usr/bin/env python3
"""
Module 5: Subtitle Burner (ASS format) — Ashes of the Chosen
Pipeline v2.0 Corrected Edition

Burns styled, word-synced subtitles permanently into the video using FFmpeg libass.
- Converts episode_XX_timestamps.json into Advanced SubStation Alpha (.ass) format
- Font: Impact (fallback Arial Black), 52pt
- Colour: White (&H00FFFFFF), 2px Black outline (&H00000000)
- Position: Centre frame horizontally, raised 15% from bottom (MarginV=288)
- 3 to 5 words per caption block
- Output: episode_XX_final.mp4 (master video)
"""

import os
import sys
import json
import logging
import argparse
import subprocess
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_p = Path(".env")
    if env_p.exists():
        for line in env_p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("burn_subtitles")

FONT_NAME = "Impact"
FALLBACK_FONT = "Arial Black"
FONT_SIZE = 52
MARGIN_V = 288  # 1920 * 0.15 = 288px (15% raised from bottom)
WORDS_PER_BLOCK = 4


def parse_arguments():
    parser = argparse.ArgumentParser(description="Module 5: Subtitle Burner for Ashes of the Chosen")
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory")
    parser.add_argument("--font", type=str, default=FONT_NAME, help="Font family (default: Impact)")
    parser.add_argument("--force", action="store_true", help="Force overwrite of existing final video")
    return parser.parse_args()


def load_environment():
    return os.getenv("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen")


def seconds_to_ass_time(sec):
    """Converts seconds float to ASS timestamp format: H:MM:SS.cs"""
    hours = int(sec // 3600)
    minutes = int((sec % 3600) // 60)
    seconds = int(sec % 60)
    centiseconds = int(round((sec - int(sec)) * 100))
    if centiseconds >= 100:
        seconds += 1
        centiseconds = 0
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def build_ass_subtitles(timestamps_data, font_name=FONT_NAME):
    words = timestamps_data.get("words", [])
    if not words:
        raise ValueError("No word timestamps found in timestamps JSON.")

    blocks = []
    i = 0
    while i < len(words):
        chunk = words[i:i + WORDS_PER_BLOCK]
        start_time = chunk[0]["start"]
        end_time = chunk[-1]["end"]
        end_time = max(start_time + 0.4, end_time + 0.1)
        
        text = " ".join([w["word"] for w in chunk]).strip()
        blocks.append({
            "start": start_time,
            "end": end_time,
            "text": text
        })
        i += WORDS_PER_BLOCK

    ass_header = f"""[Script Info]
Title: Ashes of the Chosen Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.0,0,2,40,40,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogue_lines = []
    for b in blocks:
        start_str = seconds_to_ass_time(b["start"])
        end_str = seconds_to_ass_time(b["end"])
        safe_text = b["text"].replace("{", "\\{").replace("}", "\\}")
        dialogue_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{safe_text}")

    return ass_header + "\n".join(dialogue_lines) + "\n"


def main():
    args = parse_arguments()
    base_out_dir = Path(args.output_dir or load_environment())
    ep_str = f"{args.episode:02d}"
    ep_dir = base_out_dir / f"episode-{ep_str}"

    if not ep_dir.exists():
        logger.error(f"Episode directory not found: {ep_dir}")
        sys.exit(1)

    raw_video = ep_dir / f"episode_{ep_str}_raw.mp4"
    if not raw_video.exists():
        logger.error(f"Raw video missing: {raw_video}. Run Module 4 first.")
        sys.exit(1)

    final_video = ep_dir / f"episode_{ep_str}_final.mp4"
    if not args.force and final_video.exists():
        logger.info(f"Final video already exists: {final_video}. Use --force to burn again.")
        sys.exit(0)

    timestamps_file = ep_dir / f"episode_{ep_str}_timestamps.json"
    if not timestamps_file.exists():
        logger.error(f"Timestamps file missing: {timestamps_file}. Run Module 2 first.")
        sys.exit(1)

    with open(timestamps_file, "r", encoding="utf-8") as f:
        timestamps_data = json.load(f)

    ass_content = build_ass_subtitles(timestamps_data, font_name=args.font)
    ass_path = ep_dir / f"episode_{ep_str}_subtitles.ass"
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    logger.info(f"Generated ASS subtitle file: {ass_path}")

    clean_ass_path = str(ass_path.resolve()).replace(os.sep, "/")
    if ":" in clean_ass_path:
        drive_letter, rest = clean_ass_path.split(":", 1)
        escaped_ass_path = f"{drive_letter}\\:{rest}"
    else:
        escaped_ass_path = clean_ass_path

    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_video),
        "-vf", f"ass='{escaped_ass_path}'",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "copy",
        str(final_video)
    ]

    logger.info(f"Burning ASS subtitles into {final_video.name} using FFmpeg libass...")
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError:
        logger.warning(f"libass failed with font {args.font}, attempting fallback with {FALLBACK_FONT}...")
        ass_content_fallback = build_ass_subtitles(timestamps_data, font_name=FALLBACK_FONT)
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content_fallback)
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    logger.info(f"Module 5 complete! Final subtitled video produced at: {final_video}")


if __name__ == "__main__":
    main()
