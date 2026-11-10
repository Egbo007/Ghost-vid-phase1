#!/usr/bin/env python3
"""
Tests for Module 2 (generate_voice.py) — parsing, validation and word-level timing.

Standard library only. No network, no ffmpeg, no edge-tts install required:
edge_tts is faked via sys.modules for the async tests.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import generate_voice as gv  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def write_script(tmpdir, body):
    """Writes a script file into tmpdir and returns its path."""
    path = Path(tmpdir) / "episode_01_script.txt"
    path.write_text(body, encoding="utf-8")
    return path


class TestParseScript(unittest.TestCase):
    """parse_script: scene splitting and stage-direction removal."""

    def test_normal_two_scene_script_parses(self):
        body = (
            "TITLE: ASHES OF THE CHOSEN | EP.1 | The Invitation\n"
            "\n"
            "[SCENE 1 - MOOD: romance_tension]\n"
            "(Visual: Ada at the market at dusk.)\n"
            "They told her the covenant died with her mother.\n"
            "\n"
            "[SCENE 2 - MOOD: revenge_power]\n"
            "(Visual: Obanla in his study.)\n"
            "The invitation arrived with no name on it.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            scenes = gv.parse_script(write_script(tmp, body))

        self.assertEqual(len(scenes), 2)
        self.assertEqual(scenes[0]["scene_number"], 1)
        self.assertEqual(scenes[0]["mood"], "romance_tension")
        self.assertEqual(
            scenes[0]["text"], "They told her the covenant died with her mother."
        )
        self.assertEqual(scenes[1]["scene_number"], 2)
        self.assertEqual(scenes[1]["mood"], "revenge_power")
        self.assertEqual(
            scenes[1]["text"], "The invitation arrived with no name on it."
        )

    def test_sample_fixture_parses_six_scenes(self):
        scenes = gv.parse_script(FIXTURES / "sample_episode_script.txt")
        self.assertEqual(len(scenes), 6)
        gv.validate_scenes(scenes)  # must not raise

        for scene in scenes:
            self.assertNotIn("visual:", scene["text"].lower())

        # The flashback scene is tagged in its header.
        flashbacks = [s for s in scenes if s["is_flashback"]]
        self.assertEqual(len(flashbacks), 1)
        self.assertEqual(flashbacks[0]["scene_number"], 4)

    def test_truncated_visual_directive_leaves_empty_scene(self):
        """A truncated '(Visual: ...' must not become narration."""
        scenes = gv.parse_script(FIXTURES / "truncated_script.txt")
        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0]["text"], "")

    def test_truncated_visual_directive_fails_validation_naming_scene_1(self):
        scenes = gv.parse_script(FIXTURES / "truncated_script.txt")
        with self.assertRaises(ValueError) as ctx:
            gv.validate_scenes(scenes)
        self.assertIn("Scene 1", str(ctx.exception))

    def test_multiline_visual_directive_is_removed(self):
        body = (
            "[SCENE 1 - MOOD: romance_tension]\n"
            "(Visual: Ada stands at the edge of the market,\n"
            "the scar above her left eyebrow catching the light.)\n"
            "She came back. And they did not know her face.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            scenes = gv.parse_script(write_script(tmp, body))

        self.assertEqual(
            scenes[0]["text"], "She came back. And they did not know her face."
        )

    def test_unclosed_directive_ends_at_blank_line(self):
        body = (
            "[SCENE 1 - MOOD: romance_tension]\n"
            "(Visual: Ada, a small scar\n"
            "above her left eyebrow\n"
            "\n"
            "She came back.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("generate_voice", level="WARNING"):
                scenes = gv.parse_script(write_script(tmp, body))

        self.assertEqual(scenes[0]["text"], "She came back.")

    def test_unclosed_directive_ends_at_next_scene_header(self):
        body = (
            "[SCENE 1 - MOOD: romance_tension]\n"
            "(Visual: Ada, a small scar\n"
            "above her left eyebrow\n"
            "[SCENE 2 - MOOD: supernatural]\n"
            "The gate opened.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("generate_voice", level="WARNING"):
                scenes = gv.parse_script(write_script(tmp, body))

        self.assertEqual(scenes[0]["text"], "")
        self.assertEqual(scenes[1]["text"], "The gate opened.")

    def test_lowercase_visual_directive_is_removed(self):
        body = (
            "[SCENE 1 - MOOD: supernatural]\n"
            "(visual: the compound gate at midnight)\n"
            "Ada did not knock.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            scenes = gv.parse_script(write_script(tmp, body))

        self.assertEqual(scenes[0]["text"], "Ada did not knock.")

    def test_whole_line_parenthesis_and_bracket_lines_skipped(self):
        body = (
            "[SCENE 1 - MOOD: romance_tension]\n"
            "(a stage note with no Visual keyword)\n"
            "[a bracketed note]\n"
            "She said nothing.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            scenes = gv.parse_script(write_script(tmp, body))

        self.assertEqual(scenes[0]["text"], "She said nothing.")

    def test_mid_sentence_parentheses_preserved(self):
        body = (
            "[SCENE 1 - MOOD: romance_tension]\n"
            "She left (quietly) at dawn.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            scenes = gv.parse_script(write_script(tmp, body))

        self.assertEqual(scenes[0]["text"], "She left (quietly) at dawn.")


class TestValidateScenes(unittest.TestCase):
    """validate_scenes: fails loudly on empty or contaminated narration."""

    def test_empty_narration_raises_naming_scene(self):
        scenes = [{"scene_number": 3, "text": "   "}]
        with self.assertRaises(ValueError) as ctx:
            gv.validate_scenes(scenes)
        self.assertIn("Scene 3", str(ctx.exception))

    def test_visual_text_raises_naming_scene(self):
        scenes = [{"scene_number": 2, "text": "Visual: Ada at the gate."}]
        with self.assertRaises(ValueError) as ctx:
            gv.validate_scenes(scenes)
        self.assertIn("Scene 2", str(ctx.exception))

    def test_no_scenes_raises(self):
        with self.assertRaises(ValueError):
            gv.validate_scenes([])

    def test_clean_scenes_pass(self):
        scenes = [{"scene_number": 1, "text": "She came back."}]
        gv.validate_scenes(scenes)  # must not raise


class TestWordCountCheck(unittest.TestCase):
    """check_word_count: silent under 5%, warns to 20%, fails beyond."""

    def test_within_five_percent_is_silent(self):
        with self.assertNoLogs("generate_voice", level="WARNING"):
            gv.check_word_count(100, 103)

    def test_between_five_and_twenty_percent_warns(self):
        with self.assertLogs("generate_voice", level="WARNING") as logs:
            gv.check_word_count(100, 112)
        self.assertIn("Word count mismatch", "\n".join(logs.output))

    def test_beyond_twenty_percent_raises(self):
        with self.assertRaises(RuntimeError):
            gv.check_word_count(100, 130)

    def test_exact_match_is_silent(self):
        with self.assertNoLogs("generate_voice", level="WARNING"):
            gv.check_word_count(180, 180)


def make_fake_edge_tts(chunk_types):
    """
    Builds a fake edge_tts module whose Communicate records its kwargs and whose
    stream() yields audio plus one chunk per entry in chunk_types.
    """
    calls = []

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            calls.append({"text": text, "voice": voice, "kwargs": kwargs})

        async def stream(self):
            yield {"type": "audio", "data": b"fake-mp3-bytes"}
            for i, ctype in enumerate(chunk_types):
                yield {
                    "type": ctype,
                    "offset": i * 1_000_000,
                    "duration": 500_000,
                    "text": f"word{i}",
                }

    module = types.ModuleType("edge_tts")
    module.Communicate = FakeCommunicate
    module._calls = calls
    return module


class TestGenerateSceneTts(unittest.IsolatedAsyncioTestCase):
    """generate_scene_tts: requests WordBoundary and refuses empty timings."""

    async def test_requests_word_boundary_and_returns_boundaries(self):
        fake = make_fake_edge_tts(["WordBoundary", "WordBoundary"])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "scene_01_audio.mp3"
            with mock.patch.dict(sys.modules, {"edge_tts": fake}):
                audio, boundaries = await gv.generate_scene_tts(
                    "She came back.", "en-NG-EzinneNeural", out
                )

            self.assertTrue(out.exists())
            self.assertEqual(bytes(audio), b"fake-mp3-bytes")

        self.assertEqual(len(boundaries), 2)
        self.assertEqual(boundaries[0]["text"], "word0")
        self.assertEqual(len(fake._calls), 1)
        self.assertEqual(fake._calls[0]["kwargs"].get("boundary"), "WordBoundary")

    async def test_sentence_boundary_only_raises_after_retries(self):
        fake = make_fake_edge_tts(["SentenceBoundary", "SentenceBoundary"])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "scene_01_audio.mp3"
            with mock.patch.dict(sys.modules, {"edge_tts": fake}):
                with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
                    with self.assertRaises(RuntimeError):
                        await gv.generate_scene_tts(
                            "She came back.", "en-NG-EzinneNeural", out
                        )

        # Retried the full budget before failing.
        self.assertEqual(len(fake._calls), 3)

    async def test_empty_text_does_not_require_boundaries(self):
        fake = make_fake_edge_tts([])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "scene_01_audio.mp3"
            with mock.patch.dict(sys.modules, {"edge_tts": fake}):
                _, boundaries = await gv.generate_scene_tts(
                    "   ", "en-NG-EzinneNeural", out
                )
        self.assertEqual(boundaries, [])


class TestGetAudioDuration(unittest.TestCase):
    """get_audio_duration: ffprobe failure must raise, not return 0.0."""

    def test_ffprobe_failure_raises(self):
        with mock.patch("subprocess.run", side_effect=OSError("ffprobe not found")):
            with self.assertRaises(RuntimeError):
                gv.get_audio_duration("missing.mp3")

    def test_ffprobe_success_returns_float(self):
        completed = mock.Mock(stdout="12.34\n")
        with mock.patch("subprocess.run", return_value=completed):
            self.assertAlmostEqual(gv.get_audio_duration("x.mp3"), 12.34)


class TestWriteSpokenText(unittest.TestCase):
    """write_spoken_text: audit trail of exactly what was spoken."""

    def test_writes_per_scene_text_and_counts(self):
        scenes = [
            {"scene_number": 1, "text": "She came back."},
            {"scene_number": 2, "text": "The gate opened before her hand."},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "episode_01_spoken.txt"
            gv.write_spoken_text(out, scenes, 1)
            content = out.read_text(encoding="utf-8")

        self.assertIn("[SCENE 1] words=3", content)
        self.assertIn("She came back.", content)
        self.assertIn("[SCENE 2] words=6", content)
        self.assertIn("TOTAL SPOKEN WORDS: 9", content)
        self.assertNotIn("Visual", content)


if __name__ == "__main__":
    unittest.main()
