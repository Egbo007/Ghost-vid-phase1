#!/usr/bin/env python3
"""
Module 5: Subtitle Burner (ASS format) — Ashes of the Chosen
Pipeline v2.0 Corrected Edition

Burns styled, word-synced subtitles permanently into the video using FFmpeg libass.
- Converts episode_XX_timestamps.json into Advanced SubStation Alpha (.ass) format
- Caption blocks never overlap: a block ends no later than the next one starts
- Font: Impact (fallback Arial Black), checked on disk because libass substitutes
  a missing font silently
- Colour: White (&H00FFFFFF), 2px Black outline (&H00000000)
- Position: Centre frame horizontally, raised 15% from bottom (MarginV=288)
- 4 words per caption block
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

# A caption must stay visible at least this long, unless the next block starts
# sooner — overlapping captions stack on screen and read as a glitch.
MIN_BLOCK_DURATION = 0.10

DURATION_TOLERANCE = 0.10

# Windows font files for the families we care about.
WINDOWS_FONT_FILES = {
    "Impact": "impact.ttf",
    "Arial Black": "ariblk.ttf",
}
WINDOWS_FONT_DIR = r"C:\Windows\Fonts"


def parse_arguments():
    parser = argparse.ArgumentParser(description="Module 5: Subtitle Burner for Ashes of the Chosen")
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory")
    parser.add_argument("--font", type=str, default=FONT_NAME, help="Font family (default: Impact)")
    parser.add_argument("--font-size", type=int, default=FONT_SIZE, help="Subtitle font size (default: 52)")
    parser.add_argument("--force", action="store_true", help="Force overwrite of existing final video")
    return parser.parse_args()


def load_environment():
    return os.getenv("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen")


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


def probe_duration(path):
    """Returns the container duration in seconds, or None if ffprobe fails."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace"
    )
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def choose_font(requested, os_name, exists_fn):
    """
    Picks a font family that is actually installed.

    libass substitutes a missing font without failing, so the only way to know
    what was rendered is to check the font files ourselves.

    Returns (font_name, warning_or_None).
    """
    def font_path(family):
        filename = WINDOWS_FONT_FILES.get(family)
        if not filename:
            return None
        return os.path.join(WINDOWS_FONT_DIR, filename)

    if os_name != "nt":
        # We only know the Windows font layout; trust the request elsewhere.
        return requested, None

    requested_path = font_path(requested)
    if requested_path and exists_fn(requested_path):
        return requested, None

    fallback_path = font_path(FALLBACK_FONT)
    if fallback_path and exists_fn(fallback_path):
        return FALLBACK_FONT, (
            f"Font {requested!r} not found at {requested_path}; "
            f"using {FALLBACK_FONT!r} instead."
        )

    return requested, (
        f"Neither {requested!r} nor {FALLBACK_FONT!r} was found in {WINDOWS_FONT_DIR}; "
        f"keeping {requested!r} and hoping libass substitutes something sensible."
    )


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


def build_caption_blocks(words, words_per_block=WORDS_PER_BLOCK,
                         min_duration=MIN_BLOCK_DURATION):
    """
    Groups words into caption blocks whose times never overlap.

    Each block runs from its first word's start to its last word's end, but is
    clamped so it ends no later than the next block starts. A block that would be
    shorter than min_duration is extended only if the next block leaves room.
    """
    blocks = []
    i = 0
    while i < len(words):
        chunk = words[i:i + words_per_block]
        start_time = float(chunk[0]["start"])
        end_time = float(chunk[-1]["end"])

        next_start = None
        if i + words_per_block < len(words):
            next_start = float(words[i + words_per_block]["start"])

        # Keep the caption up long enough to read, but never past the next block.
        if end_time - start_time < min_duration:
            end_time = start_time + min_duration
        if next_start is not None and end_time > next_start:
            end_time = next_start
        if end_time <= start_time:
            end_time = start_time + min_duration

        blocks.append({
            "start": start_time,
            "end": end_time,
            "text": " ".join(w["word"] for w in chunk).strip(),
        })
        i += words_per_block
    return blocks


def build_ass_subtitles(timestamps_data, font_name=FONT_NAME, font_size=FONT_SIZE):
    words = timestamps_data.get("words", [])
    if not words:
        raise ValueError("No word timestamps found in timestamps JSON.")

    blocks = build_caption_blocks(words)

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
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.0,0,2,40,40,{MARGIN_V},1

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

    font_name, font_warning = choose_font(args.font, os.name, os.path.exists)
    if font_warning:
        logger.warning(font_warning)
    logger.info(f"Using subtitle font: {font_name} at {args.font_size}pt")

    ass_content = build_ass_subtitles(
        timestamps_data, font_name=font_name, font_size=args.font_size
    )
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
        "-movflags", "+faststart",
        str(final_video)
    ]

    run_ffmpeg(cmd, f"burn subtitles into {final_video.name}")

    raw_duration = probe_duration(raw_video)
    final_duration = probe_duration(final_video)
    if raw_duration is None or final_duration is None:
        logger.error("Could not probe the video durations after burning subtitles.")
        sys.exit(1)
    drift = abs(final_duration - raw_duration)
    if drift > DURATION_TOLERANCE:
        logger.error(
            f"Subtitled video is {final_duration:.3f}s but the raw video is "
            f"{raw_duration:.3f}s (drift {drift:.3f}s > {DURATION_TOLERANCE}s)."
        )
        sys.exit(1)
    logger.info(
        f"Duration check: final {final_duration:.3f}s vs raw {raw_duration:.3f}s "
        f"(drift {drift:.3f}s)"
    )

    logger.info(f"Module 5 complete! Final subtitled video produced at: {final_video}")


if __name__ == "__main__":
    main()
