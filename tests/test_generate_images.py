#!/usr/bin/env python3
"""Tests for generate_images.py and image_providers.py — mocked, no network."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import generate_images as gi
import image_providers as ip

FAKE_TOKEN = "test-token-value-should-never-be-printed"
FAKE_ACCOUNT = "0123456789abcdef0123456789abcdef"

ENTRIES = [
    {"scene": 1, "image_label": "1a", "file_name": "scene_01a.png",
     "width": 720, "height": 1280, "prompt": "prompt a",
     "reference_paths": ["r1.jpg"], "characters": ["ADA"], "source_visual_note": "v"},
    {"scene": 1, "image_label": "1b", "file_name": "scene_01b.png",
     "width": 720, "height": 1280, "prompt": "prompt b",
     "reference_paths": [], "characters": [], "source_visual_note": "v"},
    {"scene": 2, "image_label": "2", "file_name": "scene_02.png",
     "width": 720, "height": 1280, "prompt": "prompt c",
     "reference_paths": [], "characters": [], "source_visual_note": "v"},
]


def make_episode(tmp):
    root = Path(tmp)
    base = root / "ashes-of-the-chosen"
    ep = base / "episode-01"
    ep.mkdir(parents=True)
    (ep / "episode_01_image_prompts.json").write_text(
        json.dumps(ENTRIES), encoding="utf-8")
    return root, base, ep


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.calls = []

    def generate(self, prompt, width, height, reference_paths, out_path):
        self.calls.append((prompt, width, height, reference_paths, out_path))
        Path(out_path).write_bytes(b"image")
        return out_path


class TestProviders(unittest.TestCase):
    def test_gemini_raises_clear_error(self):
        p = ip.GeminiProvider()
        with self.assertRaises(NotImplementedError) as ctx:
            p.generate("p", 720, 1280, [], Path("x.png"))
        self.assertIn("not built yet", str(ctx.exception))

    def test_registry_lookup(self):
        self.assertIsInstance(ip.get_provider("cloudflare"), ip.CloudflareProvider)
        self.assertIsInstance(ip.get_provider("GEMINI"), ip.GeminiProvider)
        with self.assertRaises(ValueError) as ctx:
            ip.get_provider("nope")
        self.assertIn("Available", str(ctx.exception))

    def test_register_provider(self):
        ip.register_provider("unittest_fake", FakeProvider)
        self.assertIsInstance(ip.get_provider("unittest_fake"), FakeProvider)

    def test_cloudflare_reuses_try_cloudflare_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "scene_01a.png"
            with mock.patch.object(ip.cf, "read_credentials",
                                   return_value=(FAKE_ACCOUNT, FAKE_TOKEN, [])), \
                 mock.patch.object(ip.cf, "request_with_retries",
                                   return_value=(200, {"result": {"image": (
                                       "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                                       "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
                                   )}}, 1)):
                p = ip.CloudflareProvider(repo_root=tmp)
                result = p.generate("p", 720, 1280, [], out)
            self.assertEqual(result, out)
            self.assertTrue(out.exists())

    def test_cloudflare_quota_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ip.cf, "read_credentials",
                                   return_value=(FAKE_ACCOUNT, FAKE_TOKEN, [])), \
                 mock.patch.object(ip.cf, "request_with_retries",
                                   return_value=(429, None, 1)):
                p = ip.CloudflareProvider(repo_root=tmp)
                with self.assertRaises(ip.QuotaError):
                    p.generate("p", 720, 1280, [], Path(tmp) / "x.png")

    def test_no_credentials_no_token_in_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ip.cf, "read_credentials",
                                   return_value=("", "", ["CLOUDFLARE_API_TOKEN"])):
                p = ip.CloudflareProvider(repo_root=tmp)
                with self.assertRaises(ip.ProviderError) as ctx:
                    p.generate("p", 720, 1280, [], Path(tmp) / "x.png")
            self.assertNotIn(FAKE_TOKEN, str(ctx.exception))


class TestGenerateImages(unittest.TestCase):
    def test_dry_run_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            with mock.patch.object(gi, "get_provider") as get:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = gi.main(["--episode", "1", "--dry-run"], base_dir=base)
            get.assert_not_called()
        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertIn("scene_01a.png", text)
        self.assertIn("refs=1", text)
        self.assertIn("no network request", text)

    def test_resume_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            (ep / "scene_01a.png").write_bytes(b"already")
            provider = FakeProvider()
            with mock.patch.object(gi, "probe_pixels", return_value=(720, 1280)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = gi.main(["--episode", "1"], base_dir=base, provider=provider)
        self.assertEqual(code, 0)
        self.assertEqual(len(provider.calls), 2)
        self.assertIn("skipped  : 1", buf.getvalue())

    def test_force_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            (ep / "scene_01a.png").write_bytes(b"old")
            provider = FakeProvider()
            with mock.patch.object(gi, "probe_pixels", return_value=(720, 1280)):
                gi.main(["--episode", "1", "--force"], base_dir=base, provider=provider)
        self.assertEqual(len(provider.calls), 3)

    def test_manifest_written_after_each_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            provider = FakeProvider()
            with mock.patch.object(gi, "probe_pixels", return_value=(720, 1280)):
                gi.main(["--episode", "1"], base_dir=base, provider=provider)
            manifest = json.loads(
                (ep / "episode_01_image_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 3)
        self.assertEqual(manifest[0]["label"], "1a")
        self.assertEqual(manifest[0]["status"], "ok")
        self.assertEqual(manifest[0]["provider"], "cloudflare")
        self.assertIn("timestamp", manifest[0])

    def test_429_stops_cleanly_manifest_intact(self):
        class QuotaProvider(FakeProvider):
            def generate(self, *args):
                raise ip.QuotaError("HTTP 429")

        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = gi.main(["--episode", "1"], base_dir=base,
                               provider=QuotaProvider())
            manifest = json.loads(
                (ep / "episode_01_image_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["status"], "quota")
        self.assertIn("later to resume", buf.getvalue())
        self.assertIn("remaining", buf.getvalue())

    def test_retry_then_record_failure_and_continue(self):
        class FlakyProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.attempts = {}

            def generate(self, prompt, width, height, refs, out_path):
                key = str(out_path)
                self.attempts[key] = self.attempts.get(key, 0) + 1
                if "scene_01a" in key and self.attempts[key] <= 2:
                    raise RuntimeError("transient")
                Path(out_path).write_bytes(b"image")
                return out_path

        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            provider = FlakyProvider()
            with mock.patch.object(gi, "probe_pixels", return_value=(720, 1280)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = gi.main(["--episode", "1"], base_dir=base,
                                   provider=provider)
            manifest = json.loads(
                (ep / "episode_01_image_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(provider.attempts[str(ep / "scene_01a.png")], 3)
        self.assertEqual(manifest[0]["status"], "ok")
        self.assertEqual(manifest[0]["attempts"], 3)
        self.assertEqual(len(manifest), 3)

    def test_hard_failure_recorded_and_continues(self):
        class BrokenProvider(FakeProvider):
            def generate(self, *args):
                raise RuntimeError("always broken")

        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = gi.main(["--episode", "1"], base_dir=base,
                               provider=BrokenProvider())
            manifest = json.loads(
                (ep / "episode_01_image_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(len(manifest), 3)
        self.assertTrue(all(m["status"] == "failed" for m in manifest))
        self.assertIn("failed   : 3", buf.getvalue())

    def test_pixel_mismatch_recorded_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            with mock.patch.object(gi, "probe_pixels", return_value=(100, 100)):
                gi.main(["--episode", "1", "--only", "1a"], base_dir=base,
                        provider=FakeProvider())
            manifest = json.loads(
                (ep / "episode_01_image_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest[0]["status"], "failed")
        self.assertIn("Pixel size", manifest[0]["error"])

    def test_usage_warning_above_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            log_dir = base / "phase-b-tests"
            log_dir.mkdir()
            today = gi.date.today().isoformat()
            with open(log_dir / "usage_log.jsonl", "w", encoding="utf-8") as f:
                for _ in range(61):
                    f.write(json.dumps({"timestamp": today}) + "\n")
            with mock.patch.object(gi, "probe_pixels", return_value=(720, 1280)):
                with self.assertLogs("generate_images", level="WARNING") as logs:
                    gi.main(["--episode", "1"], base_dir=base, provider=FakeProvider())
        self.assertIn("61 requests today", "\n".join(logs.output))

    def test_no_credentials_in_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, ep = make_episode(tmp)
            with mock.patch.object(gi, "probe_pixels", return_value=(720, 1280)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    gi.main(["--episode", "1"], base_dir=base, provider=FakeProvider())
        self.assertNotIn(FAKE_TOKEN, buf.getvalue())
        self.assertNotIn(FAKE_ACCOUNT, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
