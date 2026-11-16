#!/usr/bin/env python3
"""
Module 4: Video Assembly (with loudness normalisation) — Ashes of the Chosen
Pipeline v2.0 Corrected Edition

Stitches scene images with voiceover audio and background music into a raw video file.
- Reads scene durations from episode_XX_scene_durations.json
- Automatically applies Ken Burns motion effects based on mood_templates.json
- Frame-accurate scheduling: scene i starts at sum of earlier (duration + buffer),
  every boundary converted to a frame index with round(t * fps) so nothing drifts
- Cover-fits every image to 9:16 (2x oversample, centre crop) before zoompan
- Background music at 20% volume beneath voiceover at 100%
- Two-pass FFmpeg loudnorm targeting -14 LUFS, output at 48 kHz
- Output: 1080x1920 (9:16 vertical) episode_XX_raw.mp4
"""

import os
import re
import sys
import json
import glob
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
logger = logging.getLogger("assemble_video")

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
FPS = 25
TARGET_LUFS = -14.0
TARGET_TP = -1.5
TARGET_LRA = 11
SAMPLE_RATE = 48000

# 2x oversample before zoompan so the Ken Burns move does not jitter on
# low-resolution input, then crop back to 9:16.
OVERSAMPLE_WIDTH = TARGET_WIDTH * 2
OVERSAMPLE_HEIGHT = TARGET_HEIGHT * 2

# Tolerance when comparing the muxed video length to the intended timeline.
DURATION_TOLERANCE = 0.10

DEFAULT_FADE_IN = 3.0
DEFAULT_FADE_OUT = 3.0


def parse_arguments():
    parser = argparse.ArgumentParser(description="Module 4: Video Assembly for Ashes of the Chosen")
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory")
    parser.add_argument("--music", type=str, default=None, help="Path to background music file (default: ./assets/music/background_music.mp3)")
    parser.add_argument("--mood-templates", type=str, default="./mood_templates.json", help="Path to mood_templates.json")
    parser.add_argument("--force", action="store_true", help="Force overwrite of existing raw video")
    parser.add_argument("--fade-in", type=float, default=DEFAULT_FADE_IN, help="Fade-in duration in seconds (default: 3.0)")
    parser.add_argument("--fade-out", type=float, default=DEFAULT_FADE_OUT, help="Fade-out duration in seconds (default: 3.0)")
    return parser.parse_args()


def load_environment():
    return os.getenv("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen")


def load_mood_templates(path):
    if not os.path.exists(path):
        if os.path.exists("./mood_templates.json"):
            path = "./mood_templates.json"
        else:
            logger.warning(f"mood_templates.json not found at {path}, using defaults.")
            return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("moods", {})


def run_ffmpeg(cmd, label):
    """
    Runs an ffmpeg command. On non-zero exit, logs the last 15 stderr lines and
    exits 1 — a CalledProcessError with hidden stderr tells nobody anything.
    """
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


def get_zoompan_filter(motion_effect, duration_seconds, fps=FPS, width=TARGET_WIDTH, height=TARGET_HEIGHT):
    frames = max(1, int(round(duration_seconds * fps)))

    if motion_effect == "slow_zoom_in":
        step = 0.15 / max(1, frames)
        return (
            f"zoompan=z='min(zoom+{step:.5f},1.20)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={width}x{height}:fps={fps}"
        )
    elif motion_effect == "slow_zoom_out":
        step = 0.15 / max(1, frames)
        return (
            f"zoompan=z='if(eq(on,1),1.15,max(zoom-{step:.5f},1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={width}x{height}:fps={fps}"
        )
    elif motion_effect == "pan_right_to_left":
        return (
            f"zoompan=z=1.12:x='if(eq(on,1),iw-iw/zoom,max(x-((iw-iw/zoom)/{frames}),0))':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={fps}"
        )
    elif motion_effect == "pan_left_to_right":
        return (
            f"zoompan=z=1.12:x='min(x+((iw-iw/zoom)/{frames}),iw-iw/zoom)':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={fps}"
        )
    else:
        step = 0.04 / max(1, frames)
        return (
            f"zoompan=z='min(zoom+{step:.5f},1.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={width}x{height}:fps={fps}"
        )


def compute_scene_schedule(scenes_meta, buffer_seconds, total_duration_seconds,
                           images_per_scene, fps=FPS):
    """
    Builds the frame-exact schedule for every image clip.

    Scene i spans [S_i, E_i) where S_i is the sum over earlier scenes of
    (duration + buffer). Every scene but the last ends where the next one starts;
    the last scene ends at total_duration_seconds, absorbing the rounding (and any
    trailing encoder padding in the joined audio).

    Boundaries are converted to frame indices with round(t * fps) and differenced,
    so error never accumulates. Each scene's frame span is split equally across its
    images, with the remainder going to the earlier images. Every clip gets at
    least one frame.

    Returns a list of {"scene", "image_index", "frames"} in playback order.
    """
    if not scenes_meta:
        return []

    starts = []
    elapsed = 0.0
    for sc in scenes_meta:
        starts.append(elapsed)
        elapsed += float(sc["duration_seconds"]) + float(buffer_seconds)

    bounds = [int(round(t * fps)) for t in starts]
    bounds.append(int(round(float(total_duration_seconds) * fps)))

    schedule = []
    for i, sc in enumerate(scenes_meta):
        frame_start, frame_end = bounds[i], bounds[i + 1]
        span = max(0, frame_end - frame_start)
        count = max(1, int(images_per_scene.get(sc["scene"], 1)))
        base, remainder = divmod(span, count)
        for image_index in range(count):
            frames = base + (1 if image_index < remainder else 0)
            schedule.append({
                "scene": sc["scene"],
                "image_index": image_index,
                "frames": max(1, frames),
            })
    return schedule


def render_image_clip(img_path, frames, motion_effect, out_clip_path):
    """
    Renders one image to exactly `frames` frames.

    Cover-fit first (scale up, centre crop, 2x oversample) so non-9:16 source
    images are cropped rather than stretched, then the Ken Burns move, then
    yuv420p. -frames:v pins the length exactly instead of -t, which would let the
    clip run long and desync the timeline.
    """
    frames = max(1, int(frames))
    # get_zoompan_filter derives its own frame count from a duration; feeding it
    # frames/fps makes that round-trip land back on exactly `frames`.
    zoompan = get_zoompan_filter(motion_effect, frames / float(FPS))
    vf = (
        f"scale={OVERSAMPLE_WIDTH}:{OVERSAMPLE_HEIGHT}:"
        f"force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={OVERSAMPLE_WIDTH}:{OVERSAMPLE_HEIGHT},setsar=1,"
        f"{zoompan},format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(img_path),
        "-vf", vf,
        "-frames:v", str(frames),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        str(out_clip_path)
    ]
    run_ffmpeg(cmd, f"render clip {Path(out_clip_path).name} ({frames} frames)")


def find_scene_images(ep_dir, scene_num):
    sc_str = f"{scene_num:02d}"
    patterns = [
        ep_dir / f"scene_{sc_str}[a-z].png",
        ep_dir / f"scene_{sc_str}_*.png",
        ep_dir / f"scene_{sc_str}.png",
        ep_dir / f"scene_{sc_str}*.jpg",
        ep_dir / f"scene_{sc_str}*.jpeg"
    ]
    matched = []
    for pat in patterns:
        for f in glob.glob(str(pat)):
            p = Path(f)
            if p not in matched:
                matched.append(p)
    matched.sort()
    return matched


def parse_loudnorm_json(stderr_text):
    """
    Pulls the loudnorm measurement block out of ffmpeg stderr.
    Returns the parsed dict, or None if no measurement block is present.
    """
    for match in re.finditer(r"\{[^{}]*\}", stderr_text or "", re.S):
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if "input_i" in data:
            return data
    return None


def build_loudnorm_filter(measured=None):
    """loudnorm filter string: measuring on pass 1, applying on pass 2."""
    base = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}"
    if measured is None:
        return f"{base}:print_format=json"
    return (
        f"{base}"
        f":measured_I={measured['input_i']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        f":linear=true"
    )


def build_audio_chain(voice_idx, music_idx, fade_in, fade_out_start, fade_out, measured=None):
    """
    Builds the audio half of the filter graph. Same graph on both loudnorm passes
    so the measurement applies to exactly what gets encoded.
    """
    loud = build_loudnorm_filter(measured)
    parts = []
    if music_idx is not None:
        parts.append(f"[{voice_idx}:a]volume=1.0[v_voice]")
        parts.append(
            f"[{music_idx}:a]volume=0.2,"
            f"afade=t=in:st=0:d={fade_in},"
            f"afade=t=out:st={fade_out_start:.2f}:d={fade_out}[v_music]"
        )
        parts.append(
            f"[v_voice][v_music]amix=inputs=2:duration=first:dropout_transition=2,"
            f"{loud},aresample={SAMPLE_RATE}[aout]"
        )
    else:
        parts.append(f"[{voice_idx}:a]{loud},aresample={SAMPLE_RATE}[aout]")
    return ";".join(parts)


def measure_loudness(voice_path, music_path, fade_in, fade_out_start, fade_out):
    """
    Pass 1: runs the audio graph to a null sink with loudnorm measuring, and
    returns the measurement block. Exits 1 if ffmpeg gives us nothing to parse.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(voice_path)]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", str(music_path)]
    music_idx = 1 if music_path else None
    chain = build_audio_chain(0, music_idx, fade_in, fade_out_start, fade_out, measured=None)
    cmd += ["-filter_complex", chain, "-map", "[aout]", "-f", "null", "-"]

    proc = run_ffmpeg(cmd, "loudness measurement pass (loudnorm)")
    measured = parse_loudnorm_json(proc.stderr)
    if measured is None:
        logger.error("Could not parse loudnorm measurements from FFmpeg output.")
        logger.error("Last 15 stderr lines:")
        for line in (proc.stderr or "").strip().splitlines()[-15:]:
            logger.error(f"  {line}")
        sys.exit(1)
    logger.info(
        f"Measured loudness: I={measured.get('input_i')} LUFS, "
        f"TP={measured.get('input_tp')} dBTP, LRA={measured.get('input_lra')} LU"
    )
    return measured


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


def pick_motion_effect(sc_mood, moods_config):
    if sc_mood in moods_config:
        return moods_config[sc_mood].get("motion_effect", "slow_zoom_in")
    if "supernatural" in sc_mood:
        return "static_slight_zoom"
    if "wilderness" in sc_mood:
        return "slow_zoom_out"
    if "covenant" in sc_mood or "villain" in sc_mood:
        return "pan_right_to_left"
    return "slow_zoom_in"


def find_music_file(cli_music):
    candidates = [
        cli_music,
        "./assets/music/background_music.mp3",
        "./assets/music/background.mp3",
        "../assets/music/background_music.mp3"
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return Path(c)
    music_dir = Path("./assets/music")
    if music_dir.exists():
        music_assets = list(music_dir.glob("*.mp3"))
        if music_assets:
            return music_assets[0]
    return None


def main():
    args = parse_arguments()
    base_out_dir = Path(args.output_dir or load_environment())
    ep_str = f"{args.episode:02d}"
    ep_dir = base_out_dir / f"episode-{ep_str}"

    if not ep_dir.exists():
        logger.error(f"Episode directory not found: {ep_dir}")
        sys.exit(1)

    raw_video_path = ep_dir / f"episode_{ep_str}_raw.mp4"
    if not args.force and raw_video_path.exists():
        logger.info(f"Raw video already exists at {raw_video_path}. Use --force to regenerate.")
        sys.exit(0)

    durations_json_path = ep_dir / f"episode_{ep_str}_scene_durations.json"
    if not durations_json_path.exists():
        logger.error(f"Missing scene durations JSON: {durations_json_path}. Run Module 2 first.")
        sys.exit(1)

    with open(durations_json_path, "r", encoding="utf-8") as f:
        durations_data = json.load(f)

    moods_config = load_mood_templates(args.mood_templates)
    scenes_meta = durations_data.get("scenes", [])
    total_duration = float(durations_data.get("total_duration_seconds", 0.0))
    buffer_seconds = float(durations_data.get("buffer_seconds_between_scenes", 0.2))

    if not scenes_meta:
        logger.error(f"No scenes listed in {durations_json_path}.")
        sys.exit(1)
    if total_duration <= 0:
        logger.error(f"total_duration_seconds is missing or zero in {durations_json_path}.")
        sys.exit(1)

    voice_path = ep_dir / f"episode_{ep_str}_voice.mp3"
    if not voice_path.exists():
        logger.error(f"Missing voiceover audio: {voice_path}")
        sys.exit(1)

    # Collect images for every scene up front. A scene with no images is a hard
    # failure — silently borrowing another scene's png is how the wrong picture
    # ends up in the video.
    scene_images = {}
    images_per_scene = {}
    for sc in scenes_meta:
        sc_num = sc["scene"]
        images = find_scene_images(ep_dir, sc_num)
        if not images:
            logger.error(
                f"Scene {sc_num} has no images in {ep_dir}. "
                f"Expected files matching scene_{sc_num:02d}*.png/.jpg. "
                f"Run make_placeholders.py or generate the scene images first."
            )
            sys.exit(1)
        scene_images[sc_num] = images
        images_per_scene[sc_num] = len(images)

    schedule = compute_scene_schedule(
        scenes_meta, buffer_seconds, total_duration, images_per_scene, FPS
    )
    total_frames = sum(item["frames"] for item in schedule)
    logger.info(
        f"Schedule: {len(schedule)} clip(s) across {len(scenes_meta)} scene(s), "
        f"{total_frames} frames ({total_frames / FPS:.2f}s) at {FPS} fps"
    )

    temp_clips_dir = ep_dir / "temp_clips"
    temp_clips_dir.mkdir(exist_ok=True)
    temp_video_only = ep_dir / f"temp_{ep_str}_video_only.mp4"
    clip_files = []

    try:
        for item in schedule:
            sc_num = item["scene"]
            sc = next(s for s in scenes_meta if s["scene"] == sc_num)
            sc_mood = sc.get("mood", "romance_tension")
            motion_effect = pick_motion_effect(sc_mood, moods_config)
            img = scene_images[sc_num][item["image_index"]]

            clip_out = temp_clips_dir / f"clip_{sc_num:02d}_{item['image_index'] + 1:02d}.mp4"
            logger.info(
                f"Scene {sc_num} ({sc_mood}) image {item['image_index'] + 1}/"
                f"{images_per_scene[sc_num]}: {item['frames']} frames, motion: {motion_effect}"
            )
            render_image_clip(img, item["frames"], motion_effect, clip_out)
            clip_files.append(clip_out)

        concat_list_file = temp_clips_dir / "video_concat_list.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for c in clip_files:
                clean_path = str(c.resolve()).replace(os.sep, "/")
                f.write(f"file '{clean_path}'\n")

        run_ffmpeg(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list_file), "-c", "copy", str(temp_video_only)],
            "concatenate image clips"
        )

        music_file = find_music_file(args.music)
        fade_out_start = max(0.0, total_duration - float(args.fade_out))

        if music_file:
            logger.info(
                f"Mixing voiceover with background music: {music_file} "
                f"(20% vol, {args.fade_in}s fade in, {args.fade_out}s fade out)"
            )
        else:
            logger.warning("No background music found. Assembling with voiceover only.")

        measured = measure_loudness(
            voice_path, music_file, args.fade_in, fade_out_start, args.fade_out
        )

        video_chain = (
            f"[0:v]fade=t=in:st=0:d={args.fade_in},"
            f"fade=t=out:st={fade_out_start:.2f}:d={args.fade_out}[vout]"
        )
        music_idx = 2 if music_file else None
        audio_chain = build_audio_chain(
            1, music_idx, args.fade_in, fade_out_start, args.fade_out, measured=measured
        )
        filter_complex = f"{video_chain};{audio_chain}"

        cmd_mix = ["ffmpeg", "-y", "-i", str(temp_video_only), "-i", str(voice_path)]
        if music_file:
            cmd_mix += ["-stream_loop", "-1", "-i", str(music_file)]
        cmd_mix += [
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", str(SAMPLE_RATE),
            "-movflags", "+faststart",
            "-shortest",
            str(raw_video_path)
        ]
        run_ffmpeg(cmd_mix, "final mux with two-pass loudnorm")

        out_duration = probe_duration(raw_video_path)
        if out_duration is None:
            logger.error(f"Could not probe the assembled video: {raw_video_path}")
            sys.exit(1)
        drift = abs(out_duration - total_duration)
        if drift > DURATION_TOLERANCE:
            logger.error(
                f"Assembled video is {out_duration:.3f}s but the timeline is "
                f"{total_duration:.3f}s (drift {drift:.3f}s > {DURATION_TOLERANCE}s). "
                f"Audio and picture would be out of sync."
            )
            sys.exit(1)
        logger.info(
            f"Duration check: video {out_duration:.3f}s vs timeline "
            f"{total_duration:.3f}s (drift {drift:.3f}s)"
        )

        logger.info(f"Module 4 complete! Raw video assembled at: {raw_video_path}")

    finally:
        if temp_video_only.exists():
            temp_video_only.unlink()
        if temp_clips_dir.exists():
            for f in temp_clips_dir.glob("*"):
                try:
                    f.unlink()
                except Exception:
                    pass
            try:
                temp_clips_dir.rmdir()
            except Exception:
                pass


if __name__ == "__main__":
    main()
