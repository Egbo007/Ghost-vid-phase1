#!/usr/bin/env python3
"""
Module 2: Voiceover Generator (restructured) — Ashes of the Chosen
Pipeline v2.0 Corrected Edition

Generates narration audio with per-scene timing and word-level timestamps using Edge-TTS.
- Primary voice: en-NG-EzinneNeural (Nigerian English female)
- Flashback voice: en-NG-AbeoNeural (Nigerian English male)
- Generates 1 audio file per scene (6 scenes total)
- Concatenates scenes with 0.2s silence buffer at cut points
- Outputs scene durations JSON (for M4 cut timing) and word timestamps JSON (for M5 ASS subtitles)
"""

import os
import re
import sys
import json
import asyncio
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
logger = logging.getLogger("generate_voice")

PRIMARY_VOICE = "en-NG-EzinneNeural"
FLASHBACK_VOICE = "en-NG-AbeoNeural"
DEFAULT_BUFFER_SECONDS = 0.2


def parse_arguments():
    parser = argparse.ArgumentParser(description="Module 2: Voiceover Generator for Ashes of the Chosen")
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory (defaults to EPISODE_OUTPUT_DIR in .env or ./ashes-of-the-chosen)")
    parser.add_argument("--force", action="store_true", help="Force regeneration of existing audio and timestamps")
    return parser.parse_args()


def load_environment():
    return os.getenv("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen")


def parse_script(script_path):
    """
    Parses episode_XX_script.txt into structured scene dicts.
    Recognizes headers like:
    [SCENE 1], [SCENE 1 - MOOD: romance_tension], [SCENE 1: THE BETRAYAL], etc.
    """
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Episode script not found: {script_path}")

    with open(script_path, "r", encoding="utf-8") as f:
        content = f.read()

    scene_pattern = re.compile(r'\[\s*SCENE\s*(\d+)(?:\s*[-:]\s*(.*?))?\s*\]', re.IGNORECASE)
    splits = scene_pattern.split(content)

    if len(splits) <= 1:
        scene_pattern = re.compile(r'(?:^|\n)SCENE\s*(\d+)(?:\s*[-:]\s*(.*?))?(?:\n|$)', re.IGNORECASE)
        splits = scene_pattern.split(content)

    scenes = []
    if len(splits) > 1:
        i = 1
        while i < len(splits):
            sc_num = int(splits[i])
            sc_meta = splits[i+1].strip() if splits[i+1] else ""
            sc_body = splits[i+2].strip() if i+2 < len(splits) else ""
            
            narration_lines = []
            for line in sc_body.splitlines():
                line = line.strip()
                if not line:
                    continue
                if (line.startswith("(") and line.endswith(")")) or (line.startswith("[") and line.endswith("]")):
                    continue
                narration_lines.append(line)
            narration_text = " ".join(narration_lines)

            is_flashback = "flashback" in sc_meta.lower() or "flashback" in narration_text.lower()
            
            mood = "romance_tension"
            if "mood:" in sc_meta.lower():
                mood_match = re.search(r'mood:\s*([a-zA-Z_]+)', sc_meta, re.IGNORECASE)
                if mood_match:
                    mood = mood_match.group(1).lower()

            scenes.append({
                "scene_number": sc_num,
                "meta": sc_meta,
                "text": narration_text,
                "mood": mood,
                "is_flashback": is_flashback
            })
            i += 3
    else:
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        for idx, p in enumerate(paragraphs, start=1):
            scenes.append({
                "scene_number": idx,
                "meta": f"Scene {idx}",
                "text": p,
                "mood": "romance_tension",
                "is_flashback": "flashback" in p.lower()
            })

    if not scenes:
        raise ValueError(f"Could not parse any scenes from {script_path}")

    logger.info(f"Parsed {len(scenes)} scenes from script.")
    return scenes


async def generate_scene_tts(text, voice, out_audio_path, max_retries=3):
    """
    Uses edge-tts to generate audio and collect word boundaries.
    Returns (audio_bytes, word_boundaries_list).
    """
    try:
        import edge_tts
    except ImportError:
        logger.error("edge-tts library not installed. Run: pip install edge-tts")
        sys.exit(1)

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Generating TTS with voice {voice} (attempt {attempt}/{max_retries})...")
            communicate = edge_tts.Communicate(text, voice)
            audio_bytes = bytearray()
            word_boundaries = []

            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_boundaries.append({
                        "offset": chunk["offset"],
                        "duration": chunk["duration"],
                        "text": chunk["text"]
                    })

            with open(out_audio_path, "wb") as f:
                f.write(audio_bytes)

            return audio_bytes, word_boundaries

        except Exception as e:
            logger.warning(f"TTS generation failed on attempt {attempt}: {e}")
            if attempt < max_retries:
                await asyncio.sleep(5 * attempt)
            else:
                raise RuntimeError(f"Failed to generate TTS after {max_retries} attempts: {e}")


def get_audio_duration(file_path):
    """Calculates exact audio duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(res.stdout.strip())
    except Exception as e:
        logger.warning(f"ffprobe failed on {file_path}, falling back: {e}")
        return 0.0


def create_silence_mp3(duration, out_path):
    """Generates a silent MP3 buffer using FFmpeg."""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(duration),
        "-q:a", "9",
        "-acodec", "libmp3lame",
        str(out_path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def concatenate_audio_files(file_list, output_path):
    """Concatenates audio files using FFmpeg concat demuxer."""
    list_file = output_path.parent / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for fpath in file_list:
            clean_path = str(fpath.resolve()).replace(os.sep, "/")
            f.write(f"file '{clean_path}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    if list_file.exists():
        list_file.unlink()


async def main():
    args = parse_arguments()
    base_out_dir = Path(args.output_dir or load_environment())
    ep_str = f"{args.episode:02d}"
    ep_dir = base_out_dir / f"episode-{ep_str}"
    ep_dir.mkdir(parents=True, exist_ok=True)

    script_path = ep_dir / f"episode_{ep_str}_script.txt"
    if not script_path.exists():
        alt_script = ep_dir / f"episode_{args.episode}_script.txt"
        if alt_script.exists():
            script_path = alt_script
        else:
            logger.error(f"Script file missing: {script_path}")
            sys.exit(1)

    scenes = parse_script(script_path)

    durations_json_path = ep_dir / f"episode_{ep_str}_scene_durations.json"
    timestamps_json_path = ep_dir / f"episode_{ep_str}_timestamps.json"
    final_voice_path = ep_dir / f"episode_{ep_str}_voice.mp3"

    if not args.force and durations_json_path.exists() and timestamps_json_path.exists() and final_voice_path.exists():
        logger.info(f"Voiceover files already exist for Episode {args.episode}. Use --force to regenerate.")
        sys.exit(0)

    silence_buf_path = ep_dir / "temp_buffer_02s.mp3"
    create_silence_mp3(DEFAULT_BUFFER_SECONDS, silence_buf_path)

    scene_audio_files = []
    scenes_duration_data = []
    all_words = []
    running_timeline_seconds = 0.0

    for sc in scenes:
        sc_num = sc["scene_number"]
        sc_audio_file = ep_dir / f"scene_{sc_num:02d}_audio.mp3"
        voice = FLASHBACK_VOICE if sc["is_flashback"] else PRIMARY_VOICE

        logger.info(f"Processing Scene {sc_num} ({len(sc['text'].split())} words) with {voice}...")
        _, boundaries = await generate_scene_tts(sc["text"], voice, sc_audio_file)

        actual_dur = get_audio_duration(sc_audio_file)
        scenes_duration_data.append({
            "scene": sc_num,
            "duration_seconds": round(actual_dur, 2),
            "audio_file": sc_audio_file.name,
            "mood": sc["mood"],
            "voice": voice
        })

        for b in boundaries:
            start_sec = (b["offset"] / 10_000_000.0) + running_timeline_seconds
            dur_sec = b["duration"] / 10_000_000.0
            end_sec = start_sec + dur_sec
            all_words.append({
                "word": b["text"],
                "start": round(start_sec, 3),
                "end": round(end_sec, 3),
                "scene": sc_num
            })

        scene_audio_files.append(sc_audio_file)
        running_timeline_seconds += actual_dur + DEFAULT_BUFFER_SECONDS

    concat_sequence = []
    for idx, f in enumerate(scene_audio_files):
        concat_sequence.append(f)
        if idx < len(scene_audio_files) - 1:
            concat_sequence.append(silence_buf_path)

    logger.info(f"Concatenating {len(scene_audio_files)} scene audio files with {DEFAULT_BUFFER_SECONDS}s buffer...")
    concatenate_audio_files(concat_sequence, final_voice_path)

    if silence_buf_path.exists():
        silence_buf_path.unlink()

    total_full_duration = get_audio_duration(final_voice_path)

    durations_payload = {
        "episode": args.episode,
        "scenes": scenes_duration_data,
        "buffer_seconds_between_scenes": DEFAULT_BUFFER_SECONDS,
        "total_duration_seconds": round(total_full_duration, 2)
    }
    with open(durations_json_path, "w", encoding="utf-8") as f:
        json.dump(durations_payload, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved scene durations to {durations_json_path}")

    timestamps_payload = {
        "episode": args.episode,
        "total_words": len(all_words),
        "words": all_words
    }
    with open(timestamps_json_path, "w", encoding="utf-8") as f:
        json.dump(timestamps_payload, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved word timestamps ({len(all_words)} words) to {timestamps_json_path}")
    logger.info(f"Module 2 completed successfully for Episode {args.episode}. Final voice: {final_voice_path}")


if __name__ == "__main__":
    asyncio.run(main())
