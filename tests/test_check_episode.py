#!/usr/bin/env python3
"""
Tests for check_episode.py — parsing helpers and stage checks.

Standard library only. No ffmpeg: probing is mocked, and the ebur128 / loudnorm
samples are real ffmpeg 8.1.2 output.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

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

import check_episode as ce  # noqa: E402

# Real ffmpeg 8.1.2 ebur128=peak=true summary block.
EBUR128_SAMPLE = """\
[Parsed_ebur128_0 @ 000001] t: 2.0  TARGET:-23 LUFS    M:-20.1 S:-20.1     I: -20.1 LUFS       LRA:   0.0 LU
[Parsed_ebur128_0 @ 000001]
Summary:

  Integrated loudness:
    I:         -20.1 LUFS
    Threshold: -30.1 LUFS

  Loudness range:
    LRA:         0.0 LU
    Threshold:   0.0 LUFS
    LRA low:     0.0 LUFS
    LRA high:    0.0 LUFS

  True peak:
    Peak:       -5.6 dBFS
[out#0/null @ 000002] video:0KiB audio:94KiB subtitle:0KiB
size=N/A time=00:00:02.00 bitrate=N/A speed=3.62x
"""

# Real ffmpeg 8.1.2 loudnorm print_format=json block.
LOUDNORM_SAMPLE = """\
[Parsed_loudnorm_0 @ 000002324ceb3ec0]
{
\t"input_i" : "-20.16",
\t"input_tp" : "-5.55",
\t"input_lra" : "0.00",
\t"input_thresh" : "-30.16",
\t"output_i" : "-16.11",
\t"output_tp" : "-1.50",
\t"output_lra" : "0.00",
\t"output_thresh" : "-26.11",
\t"normalization_type" : "linear",
\t"target_offset" : "2.11"
}
"""


def make_episode(tmp, ep_str="99", scenes=None, total=None, words=None,
                 buffer_seconds=0.2):
    """Builds a temp episode directory with the two M2 JSONs."""
    base = Path(tmp)
    ep_dir = base / f"episode-{ep_str}"
    ep_dir.mkdir(parents=True, exist_ok=True)

    scenes = scenes if scenes is not None else [
        {"scene": 1, "duration_seconds": 8.33, "audio_file": "scene_01_audio.mp3",
         "mood": "romance_tension", "voice": "v"},
        {"scene": 2, "duration_seconds": 11.66, "audio_file": "scene_02_audio.mp3",
         "mood": "covenant_villain", "voice": "v"},
    ]
    if total is None:
        total = sum(s["duration_seconds"] for s in scenes) + buffer_seconds * (len(scenes) - 1)

    (ep_dir / f"episode_{ep_str}_scene_durations.json").write_text(json.dumps({
        "episode": int(ep_str),
        "scenes": scenes,
        "buffer_seconds_between_scenes": buffer_seconds,
        "total_duration_seconds": total,
    }), encoding="utf-8")

    if words is None:
        words = [
            {"word": "They", "start": 0.1, "end": 0.3, "scene": 1},
            {"word": "came", "start": 0.35, "end": 0.6, "scene": 1},
            {"word": "back", "start": 8.6, "end": 8.9, "scene": 2},
        ]
    (ep_dir / f"episode_{ep_str}_timestamps.json").write_text(json.dumps({
        "episode": int(ep_str),
        "total_words": len(words),
        "words": words,
    }), encoding="utf-8")

    (ep_dir / f"episode_{ep_str}_spoken.txt").write_text(
        f"# Episode {int(ep_str)}\n\nTOTAL SPOKEN WORDS: {len(words)}\n", encoding="utf-8")

    return base, ep_dir


class TestEbur128Parsing(unittest.TestCase):
    """measure_loudness: parse the ebur128 summary, never skip silently."""

    def test_parses_integrated_and_true_peak(self):
        proc = mock.Mock(returncode=0, stdout="", stderr=EBUR128_SAMPLE)
        with mock.patch.object(ce.subprocess, "run", return_value=proc):
            result = ce.measure_loudness("whatever.mp4")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["integrated"], -20.1)
        self.assertAlmostEqual(result["true_peak"], -5.6)

    def test_uses_summary_block_not_running_lines(self):
        """The running 't: 2.0 ... I: -20.1' lines must not be mistaken for the summary."""
        stderr = (
            "t: 1.0  TARGET:-23 LUFS    M:-30.0 S:-30.0     I: -99.9 LUFS       LRA:   0.0 LU\n"
            + EBUR128_SAMPLE
        )
        proc = mock.Mock(returncode=0, stdout="", stderr=stderr)
        with mock.patch.object(ce.subprocess, "run", return_value=proc):
            result = ce.measure_loudness("whatever.mp4")
        self.assertAlmostEqual(result["integrated"], -20.1)

    def test_returns_none_when_summary_absent(self):
        proc = mock.Mock(returncode=0, stdout="", stderr="ffmpeg version 8.1.2\nno summary")
        with mock.patch.object(ce.subprocess, "run", return_value=proc):
            self.assertIsNone(ce.measure_loudness("whatever.mp4"))

    def test_returns_none_on_ffmpeg_failure(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch.object(ce.subprocess, "run", return_value=proc):
            self.assertIsNone(ce.measure_loudness("whatever.mp4"))

    def test_unparseable_loudness_is_a_fail_not_a_skip(self):
        """A missing measurement must produce FAIL, never a silent pass."""
        with tempfile.TemporaryDirectory() as tmp:
            _, ep_dir = make_episode(tmp)
            raw = ep_dir / "episode_99_raw.mp4"
            raw.write_bytes(b"fake")

            report = ce.Report()
            with mock.patch.object(ce, "measure_loudness", return_value=None):
                ce.check_loudness(report, raw, "raw")
            self.assertEqual(report.failures, 1)


class TestLoudnessBounds(unittest.TestCase):
    def test_in_range_passes(self):
        report = ce.Report()
        with mock.patch.object(ce, "measure_loudness",
                               return_value={"integrated": -14.0, "true_peak": -1.5}):
            ce.check_loudness(report, "x.mp4", "raw")
        self.assertEqual(report.failures, 0)

    def test_too_quiet_fails(self):
        report = ce.Report()
        with mock.patch.object(ce, "measure_loudness",
                               return_value={"integrated": -20.1, "true_peak": -5.6}):
            ce.check_loudness(report, "x.mp4", "raw")
        self.assertEqual(report.failures, 1)

    def test_too_loud_fails(self):
        report = ce.Report()
        with mock.patch.object(ce, "measure_loudness",
                               return_value={"integrated": -11.0, "true_peak": -1.5}):
            ce.check_loudness(report, "x.mp4", "raw")
        self.assertEqual(report.failures, 1)

    def test_true_peak_over_limit_fails(self):
        report = ce.Report()
        with mock.patch.object(ce, "measure_loudness",
                               return_value={"integrated": -14.0, "true_peak": -0.3}):
            ce.check_loudness(report, "x.mp4", "raw")
        self.assertEqual(report.failures, 1)


class TestAssParsing(unittest.TestCase):
    """parse_ass_events: Dialogue events and their timings."""

    SAMPLE = """\
[Script Info]
Title: test

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.10,0:00:01.50,Default,,0,0,0,,They came back
Dialogue: 0,0:00:01.50,0:00:02.90,Default,,0,0,0,,and nobody knew
"""

    def test_parses_two_events(self):
        events = ce.parse_ass_events(self.SAMPLE)
        self.assertEqual(len(events), 2)
        self.assertAlmostEqual(events[0]["start"], 0.10)
        self.assertAlmostEqual(events[0]["end"], 1.50)
        self.assertAlmostEqual(events[1]["start"], 1.50)

    def test_ignores_non_dialogue_lines(self):
        events = ce.parse_ass_events("[Script Info]\nTitle: x\n")
        self.assertEqual(events, [])

    def test_ass_time_conversion(self):
        self.assertAlmostEqual(ce.ass_time_to_seconds("0:00:00.10"), 0.10)
        self.assertAlmostEqual(ce.ass_time_to_seconds("0:01:01.50"), 61.50)
        self.assertAlmostEqual(ce.ass_time_to_seconds("1:00:00.00"), 3600.0)

    def test_bad_timestamp_raises(self):
        with self.assertRaises(ValueError):
            ce.ass_time_to_seconds("nonsense")


class TestVoiceStage(unittest.TestCase):
    """check_voice: timeline, word ordering, and spoken-word agreement."""

    def run_voice(self, tmp, **kwargs):
        base, ep_dir = make_episode(tmp, **kwargs)
        report = ce.Report()
        with redirect_stdout(io.StringIO()):
            ce.check_voice(report, ep_dir, "99")
        return report, ep_dir

    def test_clean_episode_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = self.run_voice(tmp)
        self.assertEqual(report.failures, 0, report.lines)

    def test_timeline_shortfall_fails(self):
        """The joined audio can be longer than the scene sum, never shorter."""
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = self.run_voice(tmp, total=55.0)
        self.assertEqual(report.failures, 1)

    def test_timeline_excess_beyond_padding_fails(self):
        """A large excess means the scene durations and the audio disagree."""
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = self.run_voice(tmp, total=70.0)
        self.assertEqual(report.failures, 1)

    def test_old_0_29s_drift_now_fails(self):
        """
        The real B-18 case: the joined audio ran 0.29s long because the concat
        step padded each MP3 join. Brief 004 allowed that as "per-scene padding";
        it must now fail.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            durations = ep_dir / "episode_99_scene_durations.json"
            data = json.loads(durations.read_text(encoding="utf-8"))
            predicted = (sum(s["duration_seconds"] for s in data["scenes"])
                         + data["buffer_seconds_between_scenes"] * (len(data["scenes"]) - 1))
            data["total_duration_seconds"] = round(predicted + 0.29, 3)
            durations.write_text(json.dumps(data), encoding="utf-8")
            (ep_dir / "episode_99_voice.mp3").write_bytes(b"fake")

            report = ce.Report()
            with mock.patch.object(ce, "probe_duration",
                                   return_value=data["total_duration_seconds"]):
                with redirect_stdout(io.StringIO()):
                    ce.check_voice(report, ep_dir, "99")
        self.assertEqual(report.failures, 1, report.lines)

    def test_consistent_timeline_passes(self):
        """Measured buffer in the JSON means the prediction is exact."""
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            durations = ep_dir / "episode_99_scene_durations.json"
            data = json.loads(durations.read_text(encoding="utf-8"))
            data["buffer_seconds_between_scenes"] = 0.264
            data["total_duration_seconds"] = round(
                sum(s["duration_seconds"] for s in data["scenes"])
                + 0.264 * (len(data["scenes"]) - 1), 3)
            durations.write_text(json.dumps(data), encoding="utf-8")
            (ep_dir / "episode_99_voice.mp3").write_bytes(b"fake")

            report = ce.Report()
            with mock.patch.object(ce, "probe_duration",
                                   return_value=data["total_duration_seconds"]):
                with redirect_stdout(io.StringIO()):
                    ce.check_voice(report, ep_dir, "99")
        self.assertEqual(report.failures, 0, report.lines)

    def test_small_excess_within_tolerance_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            durations = ep_dir / "episode_99_scene_durations.json"
            data = json.loads(durations.read_text(encoding="utf-8"))
            predicted = (sum(s["duration_seconds"] for s in data["scenes"])
                         + data["buffer_seconds_between_scenes"] * (len(data["scenes"]) - 1))
            data["total_duration_seconds"] = round(predicted + 0.04, 3)
            durations.write_text(json.dumps(data), encoding="utf-8")
            (ep_dir / "episode_99_voice.mp3").write_bytes(b"fake")

            report = ce.Report()
            with mock.patch.object(ce, "probe_duration",
                                   return_value=data["total_duration_seconds"]):
                with redirect_stdout(io.StringIO()):
                    ce.check_voice(report, ep_dir, "99")
        self.assertEqual(report.failures, 0, report.lines)

    def test_total_duration_must_match_voice_file(self):
        """total_duration_seconds is only trustworthy if the mp3 agrees."""
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            (ep_dir / "episode_99_voice.mp3").write_bytes(b"fake")
            report = ce.Report()
            with mock.patch.object(ce, "probe_duration", return_value=25.0):
                with redirect_stdout(io.StringIO()):
                    ce.check_voice(report, ep_dir, "99")
        self.assertEqual(report.failures, 1)

    def test_first_word_late_fails(self):
        words = [
            {"word": "They", "start": 1.2, "end": 1.5, "scene": 1},
            {"word": "came", "start": 1.6, "end": 1.9, "scene": 1},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = self.run_voice(tmp, words=words)
        self.assertEqual(report.failures, 1)

    def test_non_increasing_starts_fail(self):
        words = [
            {"word": "They", "start": 0.1, "end": 0.3, "scene": 1},
            {"word": "came", "start": 0.1, "end": 0.3, "scene": 1},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = self.run_voice(tmp, words=words)
        self.assertEqual(report.failures, 1)

    def test_last_word_past_end_fails(self):
        words = [
            {"word": "They", "start": 0.1, "end": 0.3, "scene": 1},
            {"word": "came", "start": 99.0, "end": 99.5, "scene": 1},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = self.run_voice(tmp, words=words)
        # Fails both the last-word bound and the scene window; at least one.
        self.assertGreaterEqual(report.failures, 1)

    def test_word_count_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            (ep_dir / "episode_99_spoken.txt").write_text(
                "TOTAL SPOKEN WORDS: 999\n", encoding="utf-8")
            report = ce.Report()
            with redirect_stdout(io.StringIO()):
                ce.check_voice(report, ep_dir, "99")
        self.assertEqual(report.failures, 1)

    def test_missing_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ep_dir = base / "episode-99"
            ep_dir.mkdir()
            report = ce.Report()
            with redirect_stdout(io.StringIO()):
                ce.check_voice(report, ep_dir, "99")
        self.assertEqual(report.failures, 1)


class TestSceneWordWindows(unittest.TestCase):
    """
    check_voice: every word must sit inside its own scene's window.

    This is the check that catches a drift the totals hide — the joined file can
    be the right length while scene 6's words sit 0.3s early.
    """

    def run_with_words(self, tmp, words):
        base, ep_dir = make_episode(tmp, words=words)
        (ep_dir / "episode_99_voice.mp3").write_bytes(b"fake")
        durations = ep_dir / "episode_99_scene_durations.json"
        data = json.loads(durations.read_text(encoding="utf-8"))
        total = data["total_duration_seconds"]
        report = ce.Report()
        with mock.patch.object(ce, "probe_duration", return_value=total):
            with redirect_stdout(io.StringIO()):
                ce.check_voice(report, ep_dir, "99")
        return report

    def test_words_in_correct_windows_pass(self):
        # Scene 1: 0.000-8.330, scene 2 starts at 8.530.
        words = [
            {"word": "They", "start": 0.10, "end": 0.30, "scene": 1},
            {"word": "came", "start": 8.60, "end": 8.90, "scene": 2},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_with_words(tmp, words)
        self.assertEqual(report.failures, 0, report.lines)

    def test_word_placed_in_wrong_scene_window_fails(self):
        """A scene-2 word timestamped inside scene 1 must be caught."""
        words = [
            {"word": "They", "start": 0.10, "end": 0.30, "scene": 1},
            {"word": "came", "start": 2.00, "end": 2.30, "scene": 2},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_with_words(tmp, words)
        self.assertEqual(report.failures, 1, report.lines)
        self.assertTrue(
            any("scene 2" in line and "outside window" in line for line in report.lines),
            report.lines
        )

    def test_late_scene_words_caught(self):
        """Scene 2's words pushed past its window end must be caught."""
        words = [
            {"word": "They", "start": 0.10, "end": 0.30, "scene": 1},
            {"word": "came", "start": 20.50, "end": 20.80, "scene": 2},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_with_words(tmp, words)
        self.assertGreaterEqual(report.failures, 1, report.lines)

    def test_slack_allows_small_overshoot(self):
        """A word just inside the 0.10s slack must not fail."""
        # Scene 1 window ends 8.330 + 0.10 slack = 8.430.
        words = [
            {"word": "They", "start": 0.10, "end": 8.40, "scene": 1},
            {"word": "came", "start": 8.60, "end": 8.90, "scene": 2},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_with_words(tmp, words)
        self.assertEqual(report.failures, 0, report.lines)

    def test_scene_with_no_words_warns(self):
        words = [{"word": "They", "start": 0.10, "end": 0.30, "scene": 1}]
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_with_words(tmp, words)
        self.assertTrue(any("no words timestamped" in line for line in report.lines),
                        report.lines)


class TestRawStage(unittest.TestCase):
    """check_raw: format, duration agreement, loudness."""

    def make_raw(self, tmp, duration=20.0):
        base, ep_dir = make_episode(tmp, total=duration)
        raw = ep_dir / "episode_99_raw.mp4"
        raw.write_bytes(b"fake")
        (ep_dir / "episode_99_voice.mp3").write_bytes(b"fake")
        return base, ep_dir, raw

    def good_probes(self):
        return {
            "video": {"codec_name": "h264", "width": 1080, "height": 1920,
                      "pix_fmt": "yuv420p", "avg_frame_rate": "25/1", "r_frame_rate": "25/1"},
            "audio": {"codec_name": "aac", "sample_rate": "48000", "channels": 2},
        }

    def test_clean_raw_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir, raw = self.make_raw(tmp)
            probes = self.good_probes()
            report = ce.Report()
            with mock.patch.object(ce, "probe_video_stream", return_value=probes["video"]):
                with mock.patch.object(ce, "probe_audio_stream", return_value=probes["audio"]):
                    with mock.patch.object(ce, "probe_duration", return_value=20.0):
                        with mock.patch.object(ce, "measure_loudness",
                                               return_value={"integrated": -14.0,
                                                             "true_peak": -1.5}):
                            with redirect_stdout(io.StringIO()):
                                ce.check_raw(report, ep_dir, "99")
        self.assertEqual(report.failures, 0, report.lines)

    def test_wrong_resolution_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir, raw = self.make_raw(tmp)
            probes = self.good_probes()
            probes["video"]["width"] = 720
            probes["video"]["height"] = 1280
            report = ce.Report()
            with mock.patch.object(ce, "probe_video_stream", return_value=probes["video"]):
                with mock.patch.object(ce, "probe_audio_stream", return_value=probes["audio"]):
                    with mock.patch.object(ce, "probe_duration", return_value=20.0):
                        with mock.patch.object(ce, "measure_loudness",
                                               return_value={"integrated": -14.0,
                                                             "true_peak": -1.5}):
                            with redirect_stdout(io.StringIO()):
                                ce.check_raw(report, ep_dir, "99")
        self.assertEqual(report.failures, 1)

    def test_wrong_sample_rate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir, raw = self.make_raw(tmp)
            probes = self.good_probes()
            probes["audio"]["sample_rate"] = "96000"
            report = ce.Report()
            with mock.patch.object(ce, "probe_video_stream", return_value=probes["video"]):
                with mock.patch.object(ce, "probe_audio_stream", return_value=probes["audio"]):
                    with mock.patch.object(ce, "probe_duration", return_value=20.0):
                        with mock.patch.object(ce, "measure_loudness",
                                               return_value={"integrated": -14.0,
                                                             "true_peak": -1.5}):
                            with redirect_stdout(io.StringIO()):
                                ce.check_raw(report, ep_dir, "99")
        self.assertEqual(report.failures, 1)

    def test_duration_drift_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir, raw = self.make_raw(tmp, duration=20.0)
            probes = self.good_probes()
            durations = {"raw": 20.0, "voice": 21.5}
            report = ce.Report()
            with mock.patch.object(ce, "probe_video_stream", return_value=probes["video"]):
                with mock.patch.object(ce, "probe_audio_stream", return_value=probes["audio"]):
                    with mock.patch.object(ce, "probe_duration",
                                           side_effect=lambda p: durations["voice"]
                                           if str(p).endswith(".mp3") else durations["raw"]):
                        with mock.patch.object(ce, "measure_loudness",
                                               return_value={"integrated": -14.0,
                                                             "true_peak": -1.5}):
                            with redirect_stdout(io.StringIO()):
                                ce.check_raw(report, ep_dir, "99")
        self.assertEqual(report.failures, 1)

    def test_missing_raw_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            report = ce.Report()
            with redirect_stdout(io.StringIO()):
                ce.check_raw(report, ep_dir, "99")
        self.assertEqual(report.failures, 1)


class TestFinalStage(unittest.TestCase):
    """check_final: subtitle overlap and ordering."""

    def build(self, tmp, ass_text, final_duration=20.0, raw_duration=20.0):
        base, ep_dir = make_episode(tmp, total=raw_duration)
        (ep_dir / "episode_99_final.mp4").write_bytes(b"fake")
        (ep_dir / "episode_99_raw.mp4").write_bytes(b"fake")
        (ep_dir / "episode_99_subtitles.ass").write_text(ass_text, encoding="utf-8")
        return ep_dir

    def run_final(self, ep_dir, final_duration=20.0, raw_duration=20.0):
        probes = {"codec_name": "h264", "width": 1080, "height": 1920,
                  "pix_fmt": "yuv420p", "avg_frame_rate": "25/1", "r_frame_rate": "25/1"}
        audio = {"codec_name": "aac", "sample_rate": "48000", "channels": 2}
        report = ce.Report()
        with mock.patch.object(ce, "probe_video_stream", return_value=probes):
            with mock.patch.object(ce, "probe_audio_stream", return_value=audio):
                with mock.patch.object(ce, "probe_duration",
                                       side_effect=lambda p: raw_duration
                                       if str(p).endswith("_raw.mp4") else final_duration):
                    with mock.patch.object(ce, "measure_loudness",
                                           return_value={"integrated": -14.0,
                                                         "true_peak": -1.5}):
                        with redirect_stdout(io.StringIO()):
                            ce.check_final(report, ep_dir, "99")
        return report

    GOOD_ASS = """\
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.10,0:00:01.50,Default,,0,0,0,,They came back
Dialogue: 0,0:00:01.50,0:00:02.90,Default,,0,0,0,,and nobody knew
"""

    OVERLAP_ASS = """\
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.10,0:00:02.50,Default,,0,0,0,,They came back
Dialogue: 0,0:00:01.50,0:00:02.90,Default,,0,0,0,,and nobody knew
"""

    def test_clean_final_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ep_dir = self.build(tmp, self.GOOD_ASS)
            report = self.run_final(ep_dir)
        self.assertEqual(report.failures, 0, report.lines)

    def test_overlapping_events_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            ep_dir = self.build(tmp, self.OVERLAP_ASS)
            report = self.run_final(ep_dir)
        self.assertEqual(report.failures, 1)

    def test_no_dialogue_events_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            ep_dir = self.build(tmp, "[Events]\nFormat: Layer\n")
            report = self.run_final(ep_dir)
        self.assertEqual(report.failures, 1)

    def test_subtitle_past_video_end_fails(self):
        ass = """\
[Events]
Dialogue: 0,0:00:00.10,0:00:25.00,Default,,0,0,0,,too long
"""
        with tempfile.TemporaryDirectory() as tmp:
            ep_dir = self.build(tmp, ass, final_duration=20.0)
            report = self.run_final(ep_dir, final_duration=20.0)
        self.assertEqual(report.failures, 1)


class TestPackageStage(unittest.TestCase):
    """check_package: upload copies and the episode log entry."""

    def test_clean_package_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            final = ep_dir / "episode_99_final.mp4"
            final.write_bytes(b"x" * 1000)

            upload_root = Path(tmp) / "upload-ready"
            for plat in ce.PLATFORMS:
                d = upload_root / plat
                d.mkdir(parents=True)
                (d / "ep99.mp4").write_bytes(b"x" * 1000)

            (base / "episode_log.json").write_text(json.dumps({
                "episodes": [{"episode": 99, "status": "upload_ready"}]
            }), encoding="utf-8")

            report = ce.Report()
            with redirect_stdout(io.StringIO()):
                ce.check_package(report, ep_dir, "99", upload_root, base)
        self.assertEqual(report.failures, 0, report.lines)

    def test_size_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            (ep_dir / "episode_99_final.mp4").write_bytes(b"x" * 1000)

            upload_root = Path(tmp) / "upload-ready"
            for plat in ce.PLATFORMS:
                d = upload_root / plat
                d.mkdir(parents=True)
                (d / "ep99.mp4").write_bytes(b"x" * 999)

            (base / "episode_log.json").write_text(json.dumps({
                "episodes": [{"episode": 99, "status": "upload_ready"}]
            }), encoding="utf-8")

            report = ce.Report()
            with redirect_stdout(io.StringIO()):
                ce.check_package(report, ep_dir, "99", upload_root, base)
        self.assertEqual(report.failures, 3)

    def test_missing_platform_copy_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            (ep_dir / "episode_99_final.mp4").write_bytes(b"x" * 1000)
            upload_root = Path(tmp) / "upload-ready"
            (base / "episode_log.json").write_text(json.dumps({
                "episodes": [{"episode": 99, "status": "upload_ready"}]
            }), encoding="utf-8")

            report = ce.Report()
            with redirect_stdout(io.StringIO()):
                ce.check_package(report, ep_dir, "99", upload_root, base)
        self.assertEqual(report.failures, 3)

    def test_wrong_status_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            (ep_dir / "episode_99_final.mp4").write_bytes(b"x" * 1000)
            upload_root = Path(tmp) / "upload-ready"
            for plat in ce.PLATFORMS:
                d = upload_root / plat
                d.mkdir(parents=True)
                (d / "ep99.mp4").write_bytes(b"x" * 1000)
            (base / "episode_log.json").write_text(json.dumps({
                "episodes": [{"episode": 99, "status": "draft"}]
            }), encoding="utf-8")

            report = ce.Report()
            with redirect_stdout(io.StringIO()):
                ce.check_package(report, ep_dir, "99", upload_root, base)
        self.assertEqual(report.failures, 1)

    def test_missing_log_entry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ep_dir = make_episode(tmp)
            (ep_dir / "episode_99_final.mp4").write_bytes(b"x" * 1000)
            upload_root = Path(tmp) / "upload-ready"
            for plat in ce.PLATFORMS:
                d = upload_root / plat
                d.mkdir(parents=True)
                (d / "ep99.mp4").write_bytes(b"x" * 1000)
            (base / "episode_log.json").write_text(json.dumps({"episodes": []}),
                                                   encoding="utf-8")

            report = ce.Report()
            with redirect_stdout(io.StringIO()):
                ce.check_package(report, ep_dir, "99", upload_root, base)
        self.assertEqual(report.failures, 1)


class TestReport(unittest.TestCase):
    """Report: exit code follows failures only."""

    def test_no_failures_exits_zero(self):
        report = ce.Report()
        with redirect_stdout(io.StringIO()):
            report.passed("ok")
            report.warned("hmm")
            code = report.summary()
        self.assertEqual(code, 0)

    def test_failure_exits_one(self):
        report = ce.Report()
        with redirect_stdout(io.StringIO()):
            report.passed("ok")
            report.failed("bad")
            code = report.summary()
        self.assertEqual(code, 1)

    def test_check_returns_condition(self):
        report = ce.Report()
        with redirect_stdout(io.StringIO()):
            self.assertTrue(report.check(True, "ok", "bad"))
            self.assertFalse(report.check(False, "ok", "bad"))


if __name__ == "__main__":
    unittest.main()
