#!/usr/bin/env python3
"""
Module 6: Output Organiser (per-platform) — Ashes of the Chosen
Pipeline v2.0 Corrected Edition

Organises all episode files into clean per-platform upload folders:
- /upload-ready/tiktok/ep05.mp4
- /upload-ready/facebook/ep05.mp4
- /upload-ready/youtube/ep05.mp4
- Copies thumbnail if present
- Updates episode_log.json
- Automatically syncs to Google Drive (/AshesBackup/Season1/) if credentials configured
- Handles optional cleanup of intermediate working media

The subtitled master is the only thing that ships. Falling back to the raw video
requires --allow-raw, and deleting source media requires a confirmed Drive sync
or --skip-drive together with --confirm-cleanup.
"""

import os
import sys
import json
import shutil
import logging
import argparse
from datetime import datetime
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
logger = logging.getLogger("organise_output")

PLATFORMS = ["tiktok", "facebook", "youtube"]

# Intermediate media that cleanup may remove. Anything not listed here — the
# final video, thumbnail, script, durations/timestamps JSON, prompts — is kept.
CLEANUP_PATTERNS = [
    "scene_*_audio.mp3",
    "episode_*_raw.mp4",
    "episode_*_voice.mp3",
    "temp_*.*",
    "concat_list.txt",
    "scene_*.png",
    "scene_*.jpg",
]

# Files cleanup must never delete, even if a pattern would match them.
PROTECTED_SUFFIXES = [
    "_final.mp4",
    "_thumbnail.png",
    "_script.txt",
    "_scene_durations.json",
    "_timestamps.json",
    "_prompts.txt",
    "_spoken.txt",
    "_subtitles.ass",
]


def parse_arguments():
    parser = argparse.ArgumentParser(description="Module 6: Output Organiser for Ashes of the Chosen")
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory")
    parser.add_argument("--upload-dir", type=str, default="./upload-ready", help="Upload directory root")
    parser.add_argument("--skip-drive", action="store_true", help="Skip Google Drive sync")
    parser.add_argument("--cleanup", action="store_true", help="Delete source media after confirmed sync/staging")
    parser.add_argument("--allow-raw", action="store_true",
                        help="Ship the raw video if the subtitled master is missing (loud warning)")
    parser.add_argument("--confirm-cleanup", action="store_true",
                        help="Required with --cleanup --skip-drive: confirms deleting source media")
    return parser.parse_args()


def load_environment():
    return {
        "output_dir": os.getenv("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen"),
        "drive_creds": os.getenv("GOOGLE_DRIVE_CREDENTIALS", "")
    }


def should_run_cleanup(cleanup_requested, drive_synced, skip_drive, confirm_cleanup):
    """
    Decides whether intermediate media may be deleted.

    Returns (allowed, reason). Cleanup needs a backup: either a confirmed Drive
    sync, or an explicit --skip-drive plus --confirm-cleanup. Deleting the only
    copy of an episode's media because a flag was passed is not acceptable.
    """
    if not cleanup_requested:
        return False, "cleanup not requested"

    if drive_synced:
        return True, "Drive sync confirmed"

    if skip_drive:
        if confirm_cleanup:
            return True, "--skip-drive with --confirm-cleanup"
        return False, ("--cleanup with --skip-drive also needs --confirm-cleanup: "
                       "there is no backup of this episode's media")

    return False, "Drive sync was not confirmed"


def is_protected(path):
    """True for files cleanup must never delete."""
    name = Path(path).name
    return any(name.endswith(suffix) for suffix in PROTECTED_SUFFIXES)


def plan_cleanup(ep_dir):
    """
    Returns the list of files cleanup would delete, excluding protected ones.
    Pure apart from reading the directory listing.
    """
    ep_dir = Path(ep_dir)
    candidates = []
    for pattern in CLEANUP_PATTERNS:
        for f in ep_dir.glob(pattern):
            if f.is_file() and not is_protected(f) and f not in candidates:
                candidates.append(f)
    return sorted(candidates)


def cleanup_source_media(ep_dir, files=None):
    """Deletes the planned intermediate files and reports what went."""
    targets = files if files is not None else plan_cleanup(ep_dir)
    deleted_count = 0
    for f in targets:
        try:
            f.unlink()
            deleted_count += 1
        except Exception as e:
            logger.warning(f"Could not delete {f}: {e}")

    logger.info(f"Disk cleanup complete: removed {deleted_count} intermediate working file(s).")
    return deleted_count


def update_episode_log(log_path, episode_entry):
    data = {"last_updated": datetime.now().isoformat(), "episodes": []}
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"Could not parse existing log at {log_path}: {e}")

    episodes = data.get("episodes", [])
    existing_idx = next((i for i, item in enumerate(episodes) if item.get("episode") == episode_entry["episode"]), None)
    if existing_idx is not None:
        episodes[existing_idx] = episode_entry
    else:
        episodes.append(episode_entry)

    episodes.sort(key=lambda x: x.get("episode", 0))
    data["episodes"] = episodes
    data["last_updated"] = datetime.now().isoformat()

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"Updated episode log at {log_path}")


def sync_to_google_drive(creds_path, files_to_upload, folder_name="AshesBackup/Season1"):
    if not creds_path or not os.path.exists(creds_path):
        logger.info("Google Drive credentials not found or unconfigured. Skipping Drive sync.")
        return False

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        SCOPES = ["https://www.googleapis.com/auth/drive"]
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        service = build("drive", "v3", credentials=creds)

        parent_id = "root"
        for part in folder_name.strip("/").split("/"):
            q = f"mimeType = 'application/vnd.google-apps.folder' and name = '{part}' and '{parent_id}' in parents and trashed = false"
            results = service.files().list(q=q, fields="files(id, name)").execute()
            items = results.get("files", [])
            if items:
                parent_id = items[0]["id"]
            else:
                folder_metadata = {
                    "name": part,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id]
                }
                folder = service.files().create(body=folder_metadata, fields="id").execute()
                parent_id = folder.get("id")

        for fpath in files_to_upload:
            if not fpath or not os.path.exists(fpath):
                continue
            fname = Path(fpath).name
            media = MediaFileUpload(str(fpath), resumable=True)
            file_metadata = {
                "name": fname,
                "parents": [parent_id]
            }
            logger.info(f"Uploading {fname} to Google Drive ({folder_name})...")
            service.files().create(body=file_metadata, media_body=media, fields="id").execute()

        logger.info("Google Drive sync completed successfully.")
        return True

    except Exception as e:
        logger.warning(f"Google Drive sync encountered an error: {e}")
        return False


def resolve_master_video(ep_dir, ep_str, allow_raw):
    """
    Picks the video to ship.

    Returns (path, used_raw). The subtitled master is required unless allow_raw
    is set; a zero-byte file is never acceptable either way.
    """
    final_mp4 = ep_dir / f"episode_{ep_str}_final.mp4"
    raw_mp4 = ep_dir / f"episode_{ep_str}_raw.mp4"

    if final_mp4.exists():
        if final_mp4.stat().st_size == 0:
            raise ValueError(f"Final video is zero bytes: {final_mp4}")
        return final_mp4, False

    if not allow_raw:
        raise FileNotFoundError(
            f"Subtitled master missing: {final_mp4}. Run Module 5 first, "
            f"or pass --allow-raw to ship the unsubtitled video."
        )

    if not raw_mp4.exists():
        raise FileNotFoundError(f"No video file found to organise in {ep_dir}")
    if raw_mp4.stat().st_size == 0:
        raise ValueError(f"Raw video is zero bytes: {raw_mp4}")

    return raw_mp4, True


def main():
    args = parse_arguments()
    env = load_environment()
    base_out_dir = Path(args.output_dir or env["output_dir"])
    upload_root = Path(args.upload_dir)
    ep_str = f"{args.episode:02d}"
    ep_dir = base_out_dir / f"episode-{ep_str}"

    if not ep_dir.exists():
        logger.error(f"Episode directory not found: {ep_dir}")
        sys.exit(1)

    try:
        master_mp4, used_raw = resolve_master_video(ep_dir, ep_str, args.allow_raw)
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        sys.exit(1)

    if used_raw:
        logger.warning("=" * 70)
        logger.warning("SHIPPING THE RAW VIDEO — IT HAS NO BURNED-IN SUBTITLES.")
        logger.warning(f"Source: {master_mp4}")
        logger.warning("=" * 70)

    thumbnail_path = ep_dir / f"episode_{ep_str}_thumbnail.png"
    if not thumbnail_path.exists():
        alt_thumb = ep_dir / "thumbnail.png"
        if alt_thumb.exists():
            thumbnail_path = alt_thumb
        else:
            thumbnail_path = None

    platform_copies = {}
    for plat in PLATFORMS:
        plat_dir = upload_root / plat
        plat_dir.mkdir(parents=True, exist_ok=True)
        dest_video = plat_dir / f"ep{ep_str}.mp4"
        shutil.copy2(master_mp4, dest_video)

        source_size = master_mp4.stat().st_size
        dest_size = dest_video.stat().st_size
        if dest_size != source_size:
            logger.error(
                f"Copy to {dest_video} is {dest_size} bytes but the source is "
                f"{source_size} bytes. Aborting before anything is deleted."
            )
            sys.exit(1)

        platform_copies[plat] = str(dest_video.resolve())

        if thumbnail_path and thumbnail_path.exists():
            dest_thumb = plat_dir / f"ep{ep_str}_thumbnail.png"
            shutil.copy2(thumbnail_path, dest_thumb)

    logger.info(f"Copied master video to upload-ready platform folders: {list(platform_copies.keys())}")

    drive_synced = False
    if not args.skip_drive:
        files_for_drive = [master_mp4]
        if thumbnail_path and thumbnail_path.exists():
            files_for_drive.append(thumbnail_path)
        drive_synced = sync_to_google_drive(env["drive_creds"], files_for_drive)

    log_file = base_out_dir / "episode_log.json"
    episode_entry = {
        "episode": args.episode,
        "status": "upload_ready",
        "completed_at": datetime.now().isoformat(),
        "final_mp4": str(master_mp4.resolve()),
        "shipped_raw_video": used_raw,
        "thumbnail": str(thumbnail_path.resolve()) if thumbnail_path and thumbnail_path.exists() else None,
        "upload_ready": platform_copies,
        "drive_synced": drive_synced,
        "source_media_deleted": False
    }

    if args.cleanup:
        allowed, reason = should_run_cleanup(
            args.cleanup, drive_synced, args.skip_drive, args.confirm_cleanup
        )
        if allowed:
            logger.info(f"Cleanup approved ({reason}).")
            cleanup_source_media(ep_dir)
            episode_entry["source_media_deleted"] = True
        else:
            logger.warning(f"Skipping cleanup: {reason}")

    update_episode_log(log_file, episode_entry)
    logger.info(f"Module 6 complete for Episode {args.episode}! Files ready for publishing.")


if __name__ == "__main__":
    main()
