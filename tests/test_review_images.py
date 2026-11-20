#!/usr/bin/env python3
"""Tests for review_images.py â€” stdlib unittest, no network."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import review_images as ri


def make_image(path, size, colour="black"):
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i",
           f"color=c={colour}:s={size}:d=1", "-frames:v", "1", str(path)]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def make_episode(tmp, count=3):
    root = Path(tmp)
    base = root / "ashes-of-the-chosen"
    ep = base / "episode-01"
    ep.mkdir(parents=True)
    specs = [{"image_label": f"{i}", "file_name": f"scene_{i:02d}.png"}
             for i in range(1, count + 1)]
    (ep / "episode_01_image_prompts.json").write_text(
        json.dumps(specs), encoding="utf-8")
    (ep / "episode_01_image_manifest.json").write_text(
        json.dumps([{"label": s["image_label"], "file": s["file_name"]}
                    for s in specs]), encoding="utf-8")
    return root, base, ep


class TestChecks(unittest.TestCase):
    def test_good_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "good.png"
            make_image(p, "180x320", "0x204060")
            checks, flags = ri.check_image(p, {})
        self.assertTrue(checks["exists"])
        self.assertTrue(checks["decodes"])
        self.assertEqual(checks["pixels"], [180, 320])
        self.assertTrue(checks["aspect_ok"])
        self.assertNotIn("blank_or_flat", flags)

    def test_wrong_aspect(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "square.png"
            make_image(p, "200x200", "0x204060")
            checks, flags = ri.check_image(p, {})
        self.assertFalse(checks["aspect_ok"])
        self.assertIn("wrong_aspect", flags)

    def test_black_image_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "black.png"
            make_image(p, "180x320", "black")
            checks, flags = ri.check_image(p, {})
        self.assertIn("blank_or_flat", flags)

    def test_duplicate_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.png"
            b = Path(tmp) / "b.png"
            make_image(a, "180x320", "0x204060")
            b.write_bytes(a.read_bytes())
            digest_map = {}
            _, flags_a = ri.check_image(a, digest_map)
            _, flags_b = ri.check_image(b, digest_map)
        self.assertEqual(flags_a, [])
        self.assertTrue(any(f.startswith("duplicate_of:") for f in flags_b))

    def test_missing_image(self):
        checks, flags = ri.check_image(Path("no_such_image.png"), {})
        self.assertFalse(checks["exists"])
        self.assertIn("missing", flags)


class TestReviewFlow(unittest.TestCase):
    def test_review_writes_json_and_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            for i in range(1, 4):
                make_image(ep / f"scene_{i:02d}.png", "180x320", "0x204060")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = ri.main(["--episode", "1"], base_dir=base)
            review = json.loads(
                (ep / "episode_01_image_review.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(len(review), 3)
        self.assertTrue(all(not e["approved"] for e in review))
        self.assertIn("IMAGE REVIEW", buf.getvalue())
        self.assertIn("(1, 1) -> 1", buf.getvalue())

    def test_approve_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            for i in range(1, 4):
                make_image(ep / f"scene_{i:02d}.png", "180x320", "0x204060")
            ri.main(["--episode", "1"], base_dir=base)
            ri.main(["--episode", "1", "--approve"], base_dir=base)
            review = json.loads(
                (ep / "episode_01_image_review.json").read_text(encoding="utf-8"))
        self.assertTrue(all(e["approved"] for e in review))
        self.assertTrue(all(e["approved_at"] for e in review))

    def test_approve_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            for i in range(1, 4):
                make_image(ep / f"scene_{i:02d}.png", "180x320", "0x204060")
            ri.main(["--episode", "1"], base_dir=base)
            ri.main(["--episode", "1", "--approve", "2"], base_dir=base)
            review = json.loads(
                (ep / "episode_01_image_review.json").read_text(encoding="utf-8"))
        self.assertEqual([e["approved"] for e in review], [False, True, False])

    def test_regenerate_calls_subprocess_and_resets_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            for i in range(1, 4):
                make_image(ep / f"scene_{i:02d}.png", "180x320", "0x204060")
            ri.main(["--episode", "1", "--approve"], base_dir=base)
            with mock.patch.object(ri, "check_image",
                                   return_value=({"exists": True}, [])), \
                 mock.patch.object(ri, "build_contact_sheet", return_value=None), \
                 mock.patch.object(ri.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0)
                ri.main(["--episode", "1", "--regenerate", "2"], base_dir=base)
            cmd = run.call_args.args[0]
            self.assertIn("generate_images.py", cmd)
            self.assertIn("--force", cmd)
            self.assertIn("2", cmd)
            review = json.loads(
                (ep / "episode_01_image_review.json").read_text(encoding="utf-8"))
        self.assertEqual([e["approved"] for e in review], [True, False, True])

    def test_contact_sheet_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            for i in range(1, 4):
                make_image(ep / f"scene_{i:02d}.png", "180x320", "0x204060")
            ri.main(["--episode", "1"], base_dir=base)
            sheet = ep / "episode_01_contact_sheet.jpg"
            self.assertTrue(sheet.exists())
            size = ri.probe_stream(sheet)
        self.assertEqual(size, (ri.GRID_COLS * ri.THUMB_W, ri.THUMB_H))


if __name__ == "__main__":
    unittest.main()

