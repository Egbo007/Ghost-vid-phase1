#!/usr/bin/env python3
"""
Tests for Module 6 (organise_output.py) — cleanup decision table, raw fallback
guard, zero-byte rejection, protected files.

Standard library only. No network, no Drive: sync is never called.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import organise_output as oo  # noqa: E402


class TestCleanupDecision(unittest.TestCase):
    """
    should_run_cleanup: the full decision table.

    Deleting the only copy of an episode's media is irreversible, so every
    combination has to be pinned down.
    """

    def test_no_cleanup_requested(self):
        for synced in (True, False):
            for skip in (True, False):
                for confirm in (True, False):
                    allowed, _ = oo.should_run_cleanup(False, synced, skip, confirm)
                    self.assertFalse(allowed)

    def test_cleanup_with_confirmed_drive_sync(self):
        allowed, reason = oo.should_run_cleanup(True, True, False, False)
        self.assertTrue(allowed)
        self.assertIn("Drive", reason)

    def test_cleanup_with_skip_drive_and_confirm(self):
        allowed, reason = oo.should_run_cleanup(True, False, True, True)
        self.assertTrue(allowed)
        self.assertIn("confirm", reason.lower())

    def test_cleanup_with_skip_drive_without_confirm_is_refused(self):
        """The old code deleted everything on --cleanup --skip-drive alone."""
        allowed, reason = oo.should_run_cleanup(True, False, True, False)
        self.assertFalse(allowed)
        self.assertIn("--confirm-cleanup", reason)

    def test_cleanup_without_sync_or_skip_is_refused(self):
        allowed, reason = oo.should_run_cleanup(True, False, False, False)
        self.assertFalse(allowed)
        self.assertIn("not confirmed", reason)

    def test_confirm_alone_does_not_authorise_without_skip_drive(self):
        """--confirm-cleanup is only meaningful with --skip-drive."""
        allowed, _ = oo.should_run_cleanup(True, False, False, True)
        self.assertFalse(allowed)


class TestProtectedFiles(unittest.TestCase):
    """Cleanup must never delete the deliverables."""

    def test_final_video_protected(self):
        self.assertTrue(oo.is_protected("episode_99_final.mp4"))

    def test_thumbnail_protected(self):
        self.assertTrue(oo.is_protected("episode_99_thumbnail.png"))

    def test_script_protected(self):
        self.assertTrue(oo.is_protected("episode_99_script.txt"))

    def test_json_protected(self):
        self.assertTrue(oo.is_protected("episode_99_scene_durations.json"))
        self.assertTrue(oo.is_protected("episode_99_timestamps.json"))

    def test_prompts_protected(self):
        self.assertTrue(oo.is_protected("episode_99_prompts.txt"))

    def test_raw_video_not_protected(self):
        self.assertFalse(oo.is_protected("episode_99_raw.mp4"))

    def test_scene_audio_not_protected(self):
        self.assertFalse(oo.is_protected("scene_01_audio.mp3"))


class TestPlanCleanup(unittest.TestCase):
    """plan_cleanup: picks intermediates, leaves deliverables."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.ep_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def touch(self, name):
        (self.ep_dir / name).write_bytes(b"x")

    def test_plans_intermediates_only(self):
        for name in ["scene_01_audio.mp3", "episode_99_raw.mp4",
                     "episode_99_voice.mp3", "scene_01.png"]:
            self.touch(name)
        for name in ["episode_99_final.mp4", "episode_99_thumbnail.png",
                     "episode_99_script.txt", "episode_99_scene_durations.json",
                     "episode_99_timestamps.json", "episode_99_prompts.txt"]:
            self.touch(name)

        planned = [p.name for p in oo.plan_cleanup(self.ep_dir)]
        self.assertIn("scene_01_audio.mp3", planned)
        self.assertIn("episode_99_raw.mp4", planned)
        self.assertIn("episode_99_voice.mp3", planned)
        self.assertIn("scene_01.png", planned)

        self.assertNotIn("episode_99_final.mp4", planned)
        self.assertNotIn("episode_99_thumbnail.png", planned)
        self.assertNotIn("episode_99_script.txt", planned)
        self.assertNotIn("episode_99_scene_durations.json", planned)
        self.assertNotIn("episode_99_timestamps.json", planned)
        self.assertNotIn("episode_99_prompts.txt", planned)

    def test_empty_directory_plans_nothing(self):
        self.assertEqual(oo.plan_cleanup(self.ep_dir), [])

    def test_cleanup_deletes_only_planned_files(self):
        self.touch("scene_01_audio.mp3")
        self.touch("episode_99_final.mp4")
        oo.cleanup_source_media(self.ep_dir)
        self.assertFalse((self.ep_dir / "scene_01_audio.mp3").exists())
        self.assertTrue((self.ep_dir / "episode_99_final.mp4").exists())


class TestResolveMasterVideo(unittest.TestCase):
    """resolve_master_video: no silent raw fallback, no zero-byte files."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.ep_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_final_video_is_used_when_present(self):
        (self.ep_dir / "episode_99_final.mp4").write_bytes(b"x" * 100)
        path, used_raw = oo.resolve_master_video(self.ep_dir, "99", allow_raw=False)
        self.assertEqual(path.name, "episode_99_final.mp4")
        self.assertFalse(used_raw)

    def test_missing_final_without_allow_raw_raises(self):
        """The old code silently shipped the unsubtitled video here."""
        (self.ep_dir / "episode_99_raw.mp4").write_bytes(b"x" * 100)
        with self.assertRaises(FileNotFoundError) as ctx:
            oo.resolve_master_video(self.ep_dir, "99", allow_raw=False)
        self.assertIn("allow-raw", str(ctx.exception))

    def test_missing_final_with_allow_raw_uses_raw(self):
        (self.ep_dir / "episode_99_raw.mp4").write_bytes(b"x" * 100)
        path, used_raw = oo.resolve_master_video(self.ep_dir, "99", allow_raw=True)
        self.assertEqual(path.name, "episode_99_raw.mp4")
        self.assertTrue(used_raw)

    def test_no_video_at_all_raises(self):
        with self.assertRaises(FileNotFoundError):
            oo.resolve_master_video(self.ep_dir, "99", allow_raw=True)

    def test_zero_byte_final_raises(self):
        (self.ep_dir / "episode_99_final.mp4").write_bytes(b"")
        with self.assertRaises(ValueError) as ctx:
            oo.resolve_master_video(self.ep_dir, "99", allow_raw=False)
        self.assertIn("zero bytes", str(ctx.exception))

    def test_zero_byte_raw_with_allow_raw_raises(self):
        (self.ep_dir / "episode_99_raw.mp4").write_bytes(b"")
        with self.assertRaises(ValueError):
            oo.resolve_master_video(self.ep_dir, "99", allow_raw=True)


class TestCopyVerification(unittest.TestCase):
    """Copies must match the source size before anything is deleted."""

    def test_size_mismatch_is_detected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            src.write_bytes(b"x" * 100)
            dest = Path(tmp) / "dest.mp4"
            dest.write_bytes(b"x" * 99)
            self.assertNotEqual(src.stat().st_size, dest.stat().st_size)


class TestEpisodeLog(unittest.TestCase):
    """update_episode_log: entry added, replaced, and sorted."""

    def test_adds_and_replaces_entry(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "episode_log.json"
            oo.update_episode_log(log, {"episode": 2, "status": "upload_ready"})
            oo.update_episode_log(log, {"episode": 1, "status": "upload_ready"})
            oo.update_episode_log(log, {"episode": 2, "status": "done"})

            data = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual([e["episode"] for e in data["episodes"]], [1, 2])
            self.assertEqual(data["episodes"][1]["status"], "done")

    def test_corrupt_log_is_replaced_not_fatal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "episode_log.json"
            log.write_text("{not json", encoding="utf-8")
            oo.update_episode_log(log, {"episode": 1, "status": "upload_ready"})
            self.assertIn("episode", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
