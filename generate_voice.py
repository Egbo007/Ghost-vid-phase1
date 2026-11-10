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
- Also writes episode_XX_spoken.txt: the exact text sent to TTS, per scene, with word counts
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

# Word-count reconciliation thresholds between the text we sent to TTS and the
# WordBoundary events we got back. A small gap is normal (edge-tts splits some
# tokens differently); a large one means the timings are unusable for subtitles.
WORD_COUNT_WARN_RATIO = 0.05
WORD_COUNT_FAIL_RATIO = 0.20

# Matches the opening of a stage direction, e.g. "(Visual: ..." or "(visual: ...".
# Deliberately anchored on the parenthesis so mid-sentence parentheses survive.
_DIRECTIVE_START_RE = re.compile(r"\(\s*visual\b", re.IGNORECASE)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Module 2: Voiceover Generator for Ashes of the Chosen")
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory (defaults to EPISODE_OUTPUT_DIR in .env or ./ashes-of-the-chosen)")
    parser.add_argument("--force", action="store_true", help="Force regeneration of existing audio and timestamps")
    return parser.parse_args()


def load_environment():
    return os.getenv("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen")


def strip_visual_directives(scene_text, scene_number):
    """
    Removes "(Visual: ...)" stage directions from a scene body, including ones that
    wrap across several lines, and returns the narration with everything else intact.

    Rules:
    - A directive starts at "(" followed by the word "visual" (case-insensitive) and
      runs to the next ")". Text before and after it on the same line is kept.
    - An unclosed directive ends at the next blank line, the next scene header, or end
      of input, and logs a WARNING.
    - Parentheses that are not directives ("She left (quietly) at dawn.") are untouched.
    - Whole-line "(...)" and "[...]" lines are skipped, as before.
    """
    kept = []
    in_directive = False
    opened_at = None

    for lineno, raw_line in enumerate(scene_text.splitlines(), start=1):
        line = raw_line.strip()

        if in_directive:
            close = line.find(")")
            if close == -1:
                if not line:
                    logger.warning(
                        f"Scene {scene_number}: unclosed '(Visual:' directive opened at "
                        f"body line {opened_at} terminated by a blank line; dropped."
                    )
                    in_directive = False
                continue
            in_directive = False
            line = line[close + 1:].strip()
            if not line:
                continue

        if not line:
            continue

        # Preserve the original behaviour for whole-line bracketed/parenthesised lines.
        if (line.startswith("(") and line.endswith(")")) or (
            line.startswith("[") and line.endswith("]")
        ):
            continue

        # Strip inline and multi-line directives, keeping surrounding narration.
        remaining = line
        while True:
            if in_directive:
                close = remaining.find(")")
                if close == -1:
                    remaining = ""
                    break
                in_directive = False
                remaining = remaining[close + 1:].strip()
                continue

            match = _DIRECTIVE_START_RE.search(remaining)
            if not match:
                break

            close = remaining.find(")", match.end())
            if close == -1:
                in_directive = True
                opened_at = lineno
                remaining = remaining[:match.start()].strip()
                break

            remaining = (remaining[:match.start()] + " " + remaining[close + 1:]).strip()

        if remaining:
            kept.append(remaining)

    if in_directive:
        logger.warning(
            f"Scene {scene_number}: unclosed '(Visual:' directive opened at body line "
            f"{opened_at} ran to end of scene; dropped."
        )

    return " ".join(kept)


def validate_scenes(scenes):
    """
    Fails loudly if parsing left a scene with no narration or with a stage direction
    still embedded in it. Called before any TTS request or file output.
    """
    if not scenes:
        raise ValueError("No scenes were parsed from the script.")

    for scene in scenes:
        number = scene.get("scene_number")
        text = (scene.get("text") or "").strip()

        if not text:
            raise ValueError(
                f"Scene {number} has no narration text after parsing. "
                "Check the script for a scene that contains only stage directions."
            )

        if "visual:" in text.lower():
            raise ValueError(
                f"Scene {number} still contains a stage direction in its narration: "
                f"{text[:160]!r}"
            )


def check_word_count(spoken_words, timestamp_words):
    """
    Reconciles the words we sent to TTS against the WordBoundary events we got back.
    Warns past 5% drift, fails past 20% — at that point the timings cannot drive subtitles.
    """
    if spoken_words <= 0:
        return

    difference = abs(timestamp_words - spoken_words)
    ratio = difference / spoken_words
    detail = (
        f"spoken words={spoken_words}, timestamped words={timestamp_words}, "
        f"difference={difference} ({ratio:.1%})"
    )

    if ratio > WORD_COUNT_FAIL_RATIO:
        raise RuntimeError(
            f"Word count mismatch beyond {WORD_COUNT_FAIL_RATIO:.0%}: {detail}. "
            "Word timings are unusable; refusing to write subtitle timings."
        )

    if ratio > WORD_COUNT_WARN_RATIO:
        logger.warning(f"Word count mismatch above {WORD_COUNT_WARN_RATIO:.0%}: {detail}")


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
            
            narration_text = strip_visual_directives(sc_body, sc_num)

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
            communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
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

            if text.strip() and not word_boundaries:
                raise RuntimeError(
                    "Edge-TTS returned audio but no WordBoundary events; word timings "
                    "would be empty. Check the installed edge-tts version (7.x requires "
                    "boundary='WordBoundary')."
                )

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
    """Calculates exact audio duration in seconds using ffprobe. Fails loudly."""
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
        raise RuntimeError(
            f"ffprobe could not read a duration from {file_path}: {e}. "
            "Scene timings depend on this; refusing to continue with a guessed value."
        ) from e


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


def write_spoken_text(out_path, scenes, episode):
    """
    Writes the exact text sent to TTS, per scene, with its word count. This is the
    audit trail for 'what did we actually speak' — if a stage direction ever leaks
    into narration, it shows up here.
    """
    lines = [f"# Episode {episode} — text sent to TTS", ""]
    total = 0
    for sc in scenes:
        word_count = len(sc["text"].split())
        total += word_count
        lines.append(f"[SCENE {sc['scene_number']}] words={word_count}")
        lines.append(sc["text"])
        lines.append("")
    lines.append(f"TOTAL SPOKEN WORDS: {total}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


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

    # Validate before any TTS request or file output: a bad parse must not produce
    # audio, JSON, or a half-written episode folder.
    try:
        validate_scenes(scenes)
    except ValueError as e:
        logger.error(f"Script validation failed for {script_path}: {e}")
        sys.exit(1)

    durations_json_path = ep_dir / f"episode_{ep_str}_scene_durations.json"
    timestamps_json_path = ep_dir / f"episode_{ep_str}_timestamps.json"
    final_voice_path = ep_dir / f"episode_{ep_str}_voice.mp3"
    spoken_text_path = ep_dir / f"episode_{ep_str}_spoken.txt"

    if not args.force and durations_json_path.exists() and timestamps_json_path.exists() and final_voice_path.exists():
        logger.info(f"Voiceover files already exist for Episode {args.episode}. Use --force to regenerate.")
        sys.exit(0)

    silence_buf_path = ep_dir / "temp_buffer_02s.mp3"

    try:
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

        total_spoken_words = sum(len(sc["text"].split()) for sc in scenes)
        check_word_count(total_spoken_words, len(all_words))

        write_spoken_text(spoken_text_path, scenes, args.episode)
        logger.info(f"Saved spoken text to {spoken_text_path}")

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

    except RuntimeError as e:
        # Generation, ffprobe, or word-count failure: report it plainly, exit 1,
        # and do not leave a half-written episode behind.
        logger.error(f"Module 2 failed for Episode {args.episode}: {e}")
        if silence_buf_path.exists():
            silence_buf_path.unlink()
        sys.exit(1)
    except Exception as e:
        logger.error(f"Module 2 failed for Episode {args.episode}: {type(e).__name__}: {e}")
        if silence_buf_path.exists():
            silence_buf_path.unlink()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
