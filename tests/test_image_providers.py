#!/usr/bin/env python3
"""Tests for image_providers.FakeProvider — stdlib unittest, no network."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import image_providers as ip


def probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,codec_name", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True)
    return r.stdout.strip(), r.returncode


class TestFakeProvider(unittest.TestCase):
    def test_makes_file_of_requested_size(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "scene_01a.png"
            path = ip.FakeProvider().generate("prompt", 720, 1280, [], out)
            self.assertEqual(path, out)
            self.assertTrue(out.exists())
            info, rc = probe(out)
        self.assertEqual(rc, 0)
        self.assertIn("720,1280", info)
        self.assertIn("mjpeg", info)

    def test_colours_change_between_calls(self):
        with tempfile.TemporaryDirectory() as t:
            p = ip.FakeProvider()
            a = Path(t) / "a.png"
            b = Path(t) / "b.png"
            p.generate("x", 720, 1280, [], a)
            p.generate("y", 720, 1280, [], b)
            self.assertNotEqual(a.read_bytes(), b.read_bytes())

    def test_no_network_and_no_credentials(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "a.png"
            with mock.patch.object(ip.cf, "read_credentials",
                                   side_effect=AssertionError("must not read creds")), \
                 mock.patch("requests.post",
                            side_effect=AssertionError("must not call network")):
                ip.FakeProvider().generate("x", 720, 1280, [], out)
            self.assertTrue(out.exists())

    def test_ffmpeg_failure_raises_provider_error(self):
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(ip.subprocess, "run",
                                   return_value=mock.Mock(returncode=1, stderr="boom")):
                with self.assertRaises(ip.ProviderError):
                    ip.FakeProvider().generate("x", 720, 1280, [], Path(t) / "a.png")


class TestRegistry(unittest.TestCase):
    def test_fake_is_registered(self):
        self.assertIs(ip.get_provider("fake").__class__, ip.FakeProvider)
        self.assertIs(ip.get_provider("FAKE").__class__, ip.FakeProvider)

    def test_register_custom(self):
        class Custom(ip.BaseProvider):
            name = "custom"
        ip.register_provider("custom", Custom)
        try:
            self.assertIs(ip.get_provider("custom").__class__, Custom)
        finally:
            ip.PROVIDERS.pop("custom", None)

    def test_unknown_raises_value_error(self):
        with self.assertRaises(ValueError) as cm:
            ip.get_provider("nope")
        self.assertIn("fake", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
