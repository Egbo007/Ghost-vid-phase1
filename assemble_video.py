#!/usr/bin/env python3
"""
Module 4: Video Assembly (with loudness normalisation) — Ashes of the Chosen
Pipeline v2.0 Corrected Edition

Stitches scene images with voiceover audio and background music into a raw video file.
- Reads scene durations from episode_XX_scene_durations.json
- Automatically applies Ken Burns motion effects based on mood_templates.json
- Assigns display time: total_scene_duration / images_in_scene
- Background music at 20% volume beneath voiceover at 100% with 3s fade in/out
- Applies FFmpeg loudnorm filter targeting -14 LUFS
- Output: 1080x1920 (9:16 vertical) episode_XX_raw.mp4
"""

import os
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


def parse_arguments():
    parser = argparse.ArgumentParser(description="Module 4: Video Assembly for Ashes of the Chosen")
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory")
    parser.add_argument("--music", type=str, default=None, help="Path to background music file (default: ./assets/music/background_music.mp3)")
    parser.add_argument("--mood-templates", type=str, default="./mood_templates.json", help="Path to mood_templates.json")
    parser.add_argument("--force", action="store_true", help="Force overwrite of existing raw video")
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


def render_image_clip(img_path, duration_seconds, motion_effect, out_clip_path):
    filter_expr = get_zoompan_filter(motion_effect, duration_seconds)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(img_path),
        "-vf", f"{filter_expr},format=yuv420p",
        "-t", str(duration_seconds),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        str(out_clip_path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


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
    total_audio_duration = durations_data.get("total_duration_seconds", 0.0)

    voice_path = ep_dir / f"episode_{ep_str}_voice.mp3"
    if not voice_path.exists():
        logger.error(f"Missing voiceover audio: {voice_path}")
        sys.exit(1)

    temp_clips_dir = ep_dir / "temp_clips"
    temp_clips_dir.mkdir(exist_ok=True)
    clip_files = []
    temp_video_only = ep_dir / f"temp_{ep_str}_video_only.mp4"

    try:
        clip_index = 0
        for sc in scenes_meta:
            sc_num = sc["scene"]
            sc_dur = sc["duration_seconds"]
            sc_mood = sc.get("mood", "romance_tension")

            motion_effect = "slow_zoom_in"
            if sc_mood in moods_config:
                motion_effect = moods_config[sc_mood].get("motion_effect", "slow_zoom_in")
            elif "supernatural" in sc_mood:
                motion_effect = "static_slight_zoom"
            elif "wilderness" in sc_mood:
                motion_effect = "slow_zoom_out"
            elif "covenant" in sc_mood or "villain" in sc_mood:
                motion_effect = "pan_right_to_left"

            images = find_scene_images(ep_dir, sc_num)
            if not images:
                logger.warning(f"No images found for Scene {sc_num}! Looking for fallback or placeholder...")
                fallback_imgs = list(ep_dir.glob("*.png"))
                if fallback_imgs:
                    images = [fallback_imgs[0]]
                else:
                    raise FileNotFoundError(f"No images found for Scene {sc_num} in {ep_dir}")

            img_duration = sc_dur / len(images)
            logger.info(f"Scene {sc_num} ({sc_mood}): {len(images)} image(s), {img_duration:.2f}s each, motion: {motion_effect}")

            for img in images:
                clip_index += 1
                clip_out = temp_clips_dir / f"clip_{clip_index:03d}.mp4"
                render_image_clip(img, img_duration, motion_effect, clip_out)
                clip_files.append(clip_out)

        concat_list_file = temp_clips_dir / "video_concat_list.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for c in clip_files:
                clean_path = str(c.resolve()).replace(os.sep, "/")
                f.write(f"file '{clean_path}'\n")

        cmd_concat = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c", "copy",
            str(temp_video_only)
        ]
        logger.info("Concatenating all image clips...")
        subprocess.run(cmd_concat, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        music_file = None
        candidates = [
            args.music,
            "./assets/music/background_music.mp3",
            "./assets/music/background.mp3",
            "../assets/music/background_music.mp3"
        ]
        for c in candidates:
            if c and os.path.exists(c):
                music_file = Path(c)
                break
        if not music_file:
            music_assets = list(Path("./assets/music").glob("*.mp3")) if Path("./assets/music").exists() else []
            if music_assets:
                music_file = music_assets[0]

        fade_out_start = max(0.0, total_audio_duration - 3.0)
        
        if music_file:
            logger.info(f"Mixing voiceover with background music: {music_file} (20% vol, 3s fade)")
            filter_complex = (
                f"[0:v]fade=t=in:st=0:d=3,fade=t=out:st={fade_out_start:.2f}:d=3[vout];"
                f"[1:a]volume=1.0[v_voice];"
                f"[2:a]volume=0.2,afade=t=in:st=0:d=3,afade=t=out:st={fade_out_start:.2f}:d=3[v_music];"
                f"[v_voice][v_music]amix=inputs=2:duration=first:dropout_transition=2,"
                f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11[aout]"
            )
            cmd_mix = [
                "ffmpeg", "-y",
                "-i", str(temp_video_only),
                "-i", str(voice_path),
                "-stream_loop", "-1",
                "-i", str(music_file),
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-map", "[aout]",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                str(raw_video_path)
            ]
        else:
            logger.warning("No background music found. Assembling with voiceover only and -14 LUFS normalisation.")
            filter_complex = (
                f"[0:v]fade=t=in:st=0:d=3,fade=t=out:st={fade_out_start:.2f}:d=3[vout];"
                f"[1:a]loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11[aout]"
            )
            cmd_mix = [
                "ffmpeg", "-y",
                "-i", str(temp_video_only),
                "-i", str(voice_path),
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-map", "[aout]",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                str(raw_video_path)
            ]

        logger.info("Running FFmpeg assembly and -14 LUFS loudness normalisation pass...")
        subprocess.run(cmd_mix, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
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
