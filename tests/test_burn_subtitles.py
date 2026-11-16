#!/usr/bin/env python3
"""
Tests for Module 5 (burn_subtitles.py) — caption overlap, minimum duration,
font selection.

Standard library only. No ffmpeg, no network.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import burn_subtitles as bs  # noqa: E402


def words_from(spec):
    """spec: list of (word, start, end) -> timestamps JSON words list."""
    return [{"word": w, "start": s, "end": e, "scene": 1} for w, s, e in spec]


class TestCaptionBlocks(unittest.TestCase):
    """build_caption_blocks: no overlap, minimum duration, 4 words per block."""

    def test_four_words_per_block(self):
        words = words_from([(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(9)])
        blocks = bs.build_caption_blocks(words)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0]["text"], "w0 w1 w2 w3")
        self.assertEqual(blocks[2]["text"], "w8")

    def test_blocks_never_overlap(self):
        """
        The old code used max(start+0.4, end+0.1), which could push a block's end
        past the next block's start and stack two captions on screen.
        """
        # Words spaced tightly: each block's natural end runs past the next start.
        spec = [
            ("They", 0.10, 0.20),
            ("came", 0.21, 0.30),
            ("back", 0.31, 0.40),
            ("and", 0.41, 0.50),
            ("nobody", 0.51, 0.60),
            ("knew", 0.61, 0.70),
            ("her", 0.71, 0.80),
            ("face", 0.81, 0.90),
        ]
        blocks = bs.build_caption_blocks(words_from(spec))
        for i in range(len(blocks) - 1):
            self.assertLessEqual(
                blocks[i]["end"], blocks[i + 1]["start"] + 1e-9,
                f"block {i} ends at {blocks[i]['end']} but block {i+1} "
                f"starts at {blocks[i+1]['start']}"
            )

    def test_minimum_visible_duration_applied(self):
        """A very short block is extended to the minimum when there is room."""
        spec = [
            ("Hi", 1.00, 1.02),
            ("there", 1.02, 1.04),
            ("you", 1.04, 1.06),
            ("now", 1.06, 1.08),
            ("next", 5.00, 5.20),
        ]
        blocks = bs.build_caption_blocks(words_from(spec))
        self.assertGreaterEqual(blocks[0]["end"] - blocks[0]["start"],
                                bs.MIN_BLOCK_DURATION - 1e-9)

    def test_minimum_does_not_break_overlap_rule(self):
        """When the next block starts immediately, the overlap rule wins."""
        spec = [
            ("Hi", 1.00, 1.01),
            ("there", 1.01, 1.02),
            ("you", 1.02, 1.03),
            ("now", 1.03, 1.04),
            ("next", 1.05, 1.20),
        ]
        blocks = bs.build_caption_blocks(words_from(spec))
        self.assertLessEqual(blocks[0]["end"], blocks[1]["start"] + 1e-9)

    def test_end_never_before_start(self):
        spec = [
            ("Hi", 1.00, 1.00),
            ("there", 1.00, 1.00),
            ("you", 1.00, 1.00),
            ("now", 1.00, 1.00),
        ]
        blocks = bs.build_caption_blocks(words_from(spec))
        for b in blocks:
            self.assertGreater(b["end"], b["start"])

    def test_last_block_can_run_to_its_natural_end(self):
        spec = [("last", 10.0, 10.5)]
        blocks = bs.build_caption_blocks(words_from(spec))
        self.assertAlmostEqual(blocks[0]["end"], 10.5)

    def test_empty_words_gives_no_blocks(self):
        self.assertEqual(bs.build_caption_blocks([]), [])


class TestAssOutput(unittest.TestCase):
    """build_ass_subtitles: header, styling, and dialogue lines."""

    def data(self):
        return {"words": words_from([(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(8)])}

    def test_contains_header_and_style(self):
        ass = bs.build_ass_subtitles(self.data(), font_name="Impact", font_size=52)
        self.assertIn("[Script Info]", ass)
        self.assertIn("[V4+ Styles]", ass)
        self.assertIn("[Events]", ass)
        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("PlayResY: 1920", ass)
        self.assertIn("Style: Default,Impact,52,", ass)
        self.assertIn("MarginV", ass)

    def test_font_size_flag_is_used(self):
        ass = bs.build_ass_subtitles(self.data(), font_name="Impact", font_size=72)
        self.assertIn("Style: Default,Impact,72,", ass)

    def test_dialogue_lines_present(self):
        ass = bs.build_ass_subtitles(self.data())
        self.assertEqual(ass.count("Dialogue:"), 2)

    def test_braces_are_escaped(self):
        data = {"words": words_from([("{curly}", 0.0, 0.5)])}
        ass = bs.build_ass_subtitles(data)
        self.assertIn("\\{curly\\}", ass)

    def test_empty_words_raises(self):
        with self.assertRaises(ValueError):
            bs.build_ass_subtitles({"words": []})

    def test_no_overlap_in_generated_ass(self):
        """End-to-end: the emitted Dialogue lines must not overlap."""
        ass = bs.build_ass_subtitles(self.data())
        events = []
        for line in ass.splitlines():
            if line.startswith("Dialogue:"):
                parts = line.split(",")
                events.append((_ass_time_to_seconds(parts[1]),
                               _ass_time_to_seconds(parts[2])))
        for i in range(len(events) - 1):
            self.assertLessEqual(events[i][1], events[i + 1][0] + 1e-9)


def _ass_time_to_seconds(text):
    h, m, rest = text.split(":")
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0


class TestChooseFont(unittest.TestCase):
    """choose_font: check the font file, not just the ffmpeg exit code."""

    def test_impact_present_is_used(self):
        font, warning = bs.choose_font("Impact", "nt", lambda p: True)
        self.assertEqual(font, "Impact")
        self.assertIsNone(warning)

    def test_missing_impact_falls_back_to_arial_black(self):
        def exists(path):
            return path.lower().endswith("ariblk.ttf")

        font, warning = bs.choose_font("Impact", "nt", exists)
        self.assertEqual(font, "Arial Black")
        self.assertIsNotNone(warning)
        self.assertIn("Impact", warning)

    def test_neither_font_present_keeps_request_and_warns(self):
        font, warning = bs.choose_font("Impact", "nt", lambda p: False)
        self.assertEqual(font, "Impact")
        self.assertIsNotNone(warning)
        self.assertIn("Neither", warning)

    def test_arial_black_requested_and_present(self):
        font, warning = bs.choose_font("Arial Black", "nt", lambda p: True)
        self.assertEqual(font, "Arial Black")
        self.assertIsNone(warning)

    def test_non_windows_trusts_the_request(self):
        font, warning = bs.choose_font("Impact", "posix", lambda p: False)
        self.assertEqual(font, "Impact")
        self.assertIsNone(warning)

    def test_unknown_family_on_windows_warns(self):
        font, warning = bs.choose_font("Comic Sans", "nt", lambda p: True)
        self.assertIsNotNone(warning)

    def test_checks_the_impact_path_specifically(self):
        seen = []

        def exists(path):
            seen.append(path)
            return True

        bs.choose_font("Impact", "nt", exists)
        self.assertTrue(any("impact.ttf" in p.lower() for p in seen))


class TestAssTime(unittest.TestCase):
    def test_formats_centiseconds(self):
        self.assertEqual(bs.seconds_to_ass_time(0.10), "0:00:00.10")
        self.assertEqual(bs.seconds_to_ass_time(61.5), "0:01:01.50")
        self.assertEqual(bs.seconds_to_ass_time(3600.0), "1:00:00.00")

    def test_rounds_up_cleanly_at_100_centiseconds(self):
        self.assertEqual(bs.seconds_to_ass_time(1.999), "0:00:02.00")


class TestRunFfmpeg(unittest.TestCase):
    """run_ffmpeg: failures surface stderr and exit 1."""

    def test_success_returns_process(self):
        from unittest import mock
        proc = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(bs.subprocess, "run", return_value=proc):
            result = bs.run_ffmpeg(["ffmpeg"], "test")
        self.assertIs(result, proc)

    def test_failure_exits_1(self):
        from unittest import mock
        proc = mock.Mock(returncode=1, stderr="line1\nline2\nboom")
        with mock.patch.object(bs.subprocess, "run", return_value=proc):
            with self.assertRaises(SystemExit) as ctx:
                bs.run_ffmpeg(["ffmpeg"], "test")
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
