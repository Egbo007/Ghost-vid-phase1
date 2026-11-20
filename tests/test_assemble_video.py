#!/usr/bin/env python3
"""
Tests for Module 4 (assemble_video.py) — frame-accurate scheduling, loudnorm
parsing, image discovery.

Standard library only. No ffmpeg, no network: ffmpeg is never invoked here.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import assemble_video as av  # noqa: E402

FPS = 25
BUFFER = 0.2

# Mirrors the real episode-99 durations, including the trailing padding that makes
# total_duration_seconds exceed sum(durations) + buffers.
SCENES = [
    {"scene": 1, "duration_seconds": 8.33, "mood": "romance_tension"},
    {"scene": 2, "duration_seconds": 11.66, "mood": "covenant_villain"},
    {"scene": 3, "duration_seconds": 10.46, "mood": "supernatural"},
    {"scene": 4, "duration_seconds": 10.49, "mood": "wilderness"},
    {"scene": 5, "duration_seconds": 11.86, "mood": "revenge_power"},
    {"scene": 6, "duration_seconds": 6.94, "mood": "romance_tension"},
]
TOTAL = 61.03


def one_image_each(scenes):
    return {sc["scene"]: 1 for sc in scenes}


class TestComputeSceneSchedule(unittest.TestCase):
    """compute_scene_schedule: frame totals, boundaries, splitting, minimums."""

    def test_total_frames_matches_rounded_total_duration(self):
        schedule = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, one_image_each(SCENES), FPS)
        total_frames = sum(item["frames"] for item in schedule)
        self.assertEqual(total_frames, int(round(TOTAL * FPS)))

    def test_one_clip_per_scene_when_one_image(self):
        schedule = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, one_image_each(SCENES), FPS)
        self.assertEqual([item["scene"] for item in schedule], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(item["image_index"] == 0 for item in schedule))

    def test_boundaries_within_one_frame_of_timeline(self):
        """Each scene's cumulative frame boundary must land within 1 frame."""
        schedule = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, one_image_each(SCENES), FPS)

        elapsed = 0.0
        cumulative_frames = 0
        for item, scene in zip(schedule, SCENES):
            expected_frame = int(round(elapsed * FPS))
            self.assertLessEqual(
                abs(cumulative_frames - expected_frame), 1,
                f"scene {scene['scene']} boundary drifted: "
                f"{cumulative_frames} frames vs expected {expected_frame}"
            )
            cumulative_frames += item["frames"]
            elapsed += scene["duration_seconds"] + BUFFER

    def test_no_cumulative_drift_across_six_scenes(self):
        """
        The old code gave every image scene_duration/images, ignoring the 0.2s
        buffers, so cuts drifted ~0.2s per scene. By scene 6 that was ~1s.

        Only interior boundaries are checked: the last scene's end is
        total_duration_seconds by design, which absorbs trailing encoder padding.
        """
        schedule = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, one_image_each(SCENES), FPS)

        cumulative_frames = 0
        elapsed = 0.0
        for item, scene in zip(schedule, SCENES):
            elapsed += scene["duration_seconds"] + BUFFER
            cumulative_frames += item["frames"]
            if scene is SCENES[-1]:
                break
            drift_seconds = abs(cumulative_frames / FPS - elapsed)
            self.assertLess(
                drift_seconds, 1.0 / FPS,
                f"drift after scene {scene['scene']} is {drift_seconds:.3f}s"
            )

    def test_scene_span_includes_its_buffer(self):
        """
        Scene i runs until scene i+1 starts, so its frame span covers the 0.2s
        silence too — the picture must not freeze or gap between scenes.
        """
        schedule = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, one_image_each(SCENES), FPS)
        scene_1_frames = schedule[0]["frames"]
        # 8.33s of audio + 0.2s buffer = 8.53s -> 213 frames at 25fps.
        self.assertEqual(scene_1_frames, int(round((8.33 + BUFFER) * FPS)))

    def test_last_scene_absorbs_rounding(self):
        """The last scene ends exactly at total_duration_seconds, not at the sum."""
        schedule = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, one_image_each(SCENES), FPS)
        self.assertEqual(sum(i["frames"] for i in schedule), int(round(TOTAL * FPS)))

        # Sum of durations + buffers is 60.74s, but the timeline is 61.03s.
        naive = sum(sc["duration_seconds"] for sc in SCENES) + BUFFER * (len(SCENES) - 1)
        self.assertGreater(TOTAL, naive)
        self.assertEqual(
            sum(i["frames"] for i in schedule) - int(round(naive * FPS)),
            int(round(TOTAL * FPS)) - int(round(naive * FPS))
        )

    def test_images_split_a_scene_equally(self):
        per_scene = one_image_each(SCENES)
        per_scene[1] = 2
        schedule = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, per_scene, FPS)

        scene_1 = [i for i in schedule if i["scene"] == 1]
        self.assertEqual(len(scene_1), 2)
        self.assertEqual(scene_1[0]["image_index"], 0)
        self.assertEqual(scene_1[1]["image_index"], 1)
        # Scene 1 spans 8.33s + 0.2s buffer = 8.53s -> 213 frames; split 107/106.
        self.assertEqual(sum(i["frames"] for i in scene_1), 213)
        self.assertLessEqual(abs(scene_1[0]["frames"] - scene_1[1]["frames"]), 1)

    def test_uneven_split_puts_remainder_first(self):
        per_scene = one_image_each(SCENES)
        per_scene[1] = 3
        schedule = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, per_scene, FPS)
        scene_1 = [i["frames"] for i in schedule if i["scene"] == 1]
        self.assertEqual(sum(scene_1), 213)
        self.assertEqual(len(scene_1), 3)
        # 213 = 71 + 71 + 71, or 71/71/71 with remainder distributed first.
        self.assertEqual(scene_1, sorted(scene_1, reverse=True))
        self.assertLessEqual(max(scene_1) - min(scene_1), 1)

    def test_every_clip_gets_at_least_one_frame(self):
        """A scene far shorter than one frame must still yield a renderable clip."""
        scenes = [
            {"scene": 1, "duration_seconds": 0.01},
            {"scene": 2, "duration_seconds": 0.01},
        ]
        per_scene = {1: 4, 2: 4}
        schedule = av.compute_scene_schedule(scenes, 0.0, 0.04, per_scene, FPS)
        self.assertTrue(all(item["frames"] >= 1 for item in schedule))

    def test_empty_scene_list_returns_empty_schedule(self):
        self.assertEqual(av.compute_scene_schedule([], BUFFER, 0.0, {}, FPS), [])

    def test_schedule_is_deterministic(self):
        first = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, one_image_each(SCENES), FPS)
        second = av.compute_scene_schedule(SCENES, BUFFER, TOTAL, one_image_each(SCENES), FPS)
        self.assertEqual(first, second)


class TestLoudnormParsing(unittest.TestCase):
    """parse_loudnorm_json: pull the measurement block out of ffmpeg stderr."""

    SAMPLE = """\
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
[out#0/null @ 000002324ceb3780] video:0KiB audio:750KiB subtitle:0KiB
size=N/A time=00:00:02.00 bitrate=N/A speed=16.3x elapsed=0:00:00.12
"""

    def test_parses_measurement_block(self):
        measured = av.parse_loudnorm_json(self.SAMPLE)
        self.assertIsNotNone(measured)
        self.assertEqual(measured["input_i"], "-20.16")
        self.assertEqual(measured["input_tp"], "-5.55")
        self.assertEqual(measured["input_lra"], "0.00")
        self.assertEqual(measured["input_thresh"], "-30.16")
        self.assertEqual(measured["target_offset"], "2.11")

    def test_returns_none_when_no_block(self):
        self.assertIsNone(av.parse_loudnorm_json("ffmpeg version 8.1.2\nnothing here"))

    def test_returns_none_for_empty_input(self):
        self.assertIsNone(av.parse_loudnorm_json(""))
        self.assertIsNone(av.parse_loudnorm_json(None))

    def test_ignores_unrelated_json_blocks(self):
        stderr = '{"something": 1}\n{"input_i": "-18.0", "input_tp": "-2.0"}'
        measured = av.parse_loudnorm_json(stderr)
        self.assertEqual(measured["input_i"], "-18.0")

    def test_build_loudnorm_filter_measure_pass(self):
        filt = av.build_loudnorm_filter(None)
        self.assertIn("print_format=json", filt)
        self.assertNotIn("measured_I", filt)

    def test_build_loudnorm_filter_apply_pass(self):
        measured = av.parse_loudnorm_json(self.SAMPLE)
        filt = av.build_loudnorm_filter(measured)
        self.assertIn("measured_I=-20.16", filt)
        self.assertIn("measured_LRA=0.00", filt)
        self.assertIn("measured_TP=-5.55", filt)
        self.assertIn("measured_thresh=-30.16", filt)
        self.assertIn("offset=2.11", filt)
        self.assertIn("linear=true", filt)
        self.assertNotIn("print_format=json", filt)


class TestAudioChain(unittest.TestCase):
    """build_audio_chain: 48 kHz resample and the right graph for each case."""

    def test_voice_only_chain_resamples_to_48k(self):
        chain = av.build_audio_chain(1, None, 3.0, 58.03, 3.0)
        self.assertIn("aresample=48000", chain)
        self.assertIn("[1:a]", chain)
        self.assertNotIn("amix", chain)

    def test_music_chain_mixes_and_resamples(self):
        chain = av.build_audio_chain(1, 2, 3.0, 58.03, 3.0)
        self.assertIn("[2:a]volume=0.2", chain)
        self.assertIn("amix=inputs=2", chain)
        self.assertIn("aresample=48000", chain)

    def test_apply_pass_uses_measurements(self):
        measured = {"input_i": "-20.16", "input_lra": "0.00", "input_tp": "-5.55",
                    "input_thresh": "-30.16", "target_offset": "2.11"}
        chain = av.build_audio_chain(1, None, 3.0, 58.03, 3.0, measured=measured)
        self.assertIn("measured_I=-20.16", chain)
        self.assertIn("linear=true", chain)


class TestFindSceneImages(unittest.TestCase):
    """find_scene_images: pattern matching, ordering, and the no-image case."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.ep_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def touch(self, name):
        path = self.ep_dir / name
        path.write_bytes(b"not a real png")
        return path

    def test_finds_single_letter_suffixed_images(self):
        self.touch("scene_01a.png")
        self.touch("scene_01b.png")
        found = av.find_scene_images(self.ep_dir, 1)
        self.assertEqual([p.name for p in found], ["scene_01a.png", "scene_01b.png"])

    def test_finds_underscore_suffixed_images(self):
        self.touch("scene_02_wide.png")
        found = av.find_scene_images(self.ep_dir, 2)
        self.assertEqual([p.name for p in found], ["scene_02_wide.png"])

    def test_finds_plain_png(self):
        self.touch("scene_03.png")
        found = av.find_scene_images(self.ep_dir, 3)
        self.assertEqual([p.name for p in found], ["scene_03.png"])

    def test_finds_jpg(self):
        self.touch("scene_04.jpg")
        found = av.find_scene_images(self.ep_dir, 4)
        self.assertEqual([p.name for p in found], ["scene_04.jpg"])

    def test_results_are_sorted_and_deduplicated(self):
        self.touch("scene_01b.png")
        self.touch("scene_01a.png")
        found = av.find_scene_images(self.ep_dir, 1)
        self.assertEqual([p.name for p in found], ["scene_01a.png", "scene_01b.png"])

    def test_does_not_match_other_scenes(self):
        self.touch("scene_02.png")
        self.assertEqual(av.find_scene_images(self.ep_dir, 1), [])

    def test_returns_empty_when_scene_has_no_images(self):
        """The old code borrowed any png in the folder; this must be empty."""
        self.touch("scene_02.png")
        self.touch("unrelated.png")
        self.assertEqual(av.find_scene_images(self.ep_dir, 5), [])


class TestNoImagesIsFatal(unittest.TestCase):
    """A scene with no images must exit 1, not borrow another scene's picture."""

    def test_missing_scene_images_exits_1(self):
        import json
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ep_dir = base / "episode-99"
            ep_dir.mkdir()
            (ep_dir / "episode_99_scene_durations.json").write_text(
                json.dumps({
                    "episode": 99,
                    "scenes": [{"scene": 1, "duration_seconds": 5.0, "mood": "wilderness"}],
                    "buffer_seconds_between_scenes": 0.2,
                    "total_duration_seconds": 5.0,
                }), encoding="utf-8")
            (ep_dir / "episode_99_voice.mp3").write_bytes(b"fake")
            # An unrelated png that the old fallback would have grabbed.
            (ep_dir / "some_other_picture.png").write_bytes(b"fake")

            argv = ["assemble_video.py", "--episode", "99", "--output-dir", tmp, "--force"]
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(av, "run_ffmpeg") as fake_ffmpeg:
                    with self.assertRaises(SystemExit) as ctx:
                        av.main()
            self.assertEqual(ctx.exception.code, 1)
            fake_ffmpeg.assert_not_called()


class TestMotionEffectSelection(unittest.TestCase):
    """pick_motion_effect: mood_templates wins, then keyword fallbacks."""

    MOODS = {
        "romance_tension": {"motion_effect": "slow_zoom_in"},
        "wilderness": {"motion_effect": "slow_zoom_out"},
        "covenant_villain": {"motion_effect": "pan_right_to_left"},
        "supernatural": {"motion_effect": "static_slight_zoom"},
    }

    def test_uses_mood_template(self):
        self.assertEqual(av.pick_motion_effect("wilderness", self.MOODS), "slow_zoom_out")

    def test_keyword_fallback_when_mood_unknown(self):
        self.assertEqual(av.pick_motion_effect("supernatural_vision", {}), "static_slight_zoom")
        self.assertEqual(av.pick_motion_effect("covenant_meeting", {}), "pan_right_to_left")

    def test_default_when_nothing_matches(self):
        self.assertEqual(av.pick_motion_effect("unlisted_mood", {}), "slow_zoom_in")


class TestRenderClipFilter(unittest.TestCase):
    """render_image_clip: cover-fit before zoompan, exact frame count."""

    def test_filter_covers_fits_and_pins_frames(self):
        from unittest import mock

        with mock.patch.object(av, "run_ffmpeg") as fake:
            av.render_image_clip("in.png", 104, "slow_zoom_in", "out.mp4")

        cmd = fake.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]

        self.assertIn("scale=2160:3840", vf)
        self.assertIn("force_original_aspect_ratio=increase", vf)
        self.assertIn("crop=2160:3840", vf)
        self.assertIn("setsar=1", vf)
        self.assertIn("zoompan=", vf)
        self.assertIn("format=yuv420p", vf)
        # scale/crop must come before zoompan.
        self.assertLess(vf.index("scale="), vf.index("zoompan="))
        self.assertLess(vf.index("crop="), vf.index("zoompan="))

        self.assertEqual(cmd[cmd.index("-frames:v") + 1], "104")
        self.assertNotIn("-t", cmd)

    def test_zoompan_frame_count_matches_requested_frames(self):
        """The duration->frames round trip inside get_zoompan_filter must be exact."""
        for frames in (1, 7, 104, 208, 296):
            filt = av.get_zoompan_filter("slow_zoom_in", frames / float(av.FPS))
            self.assertIn(f"d={frames}:", filt, f"expected d={frames} for {frames} frames")


class TestFinalMuxPixelFormat(unittest.TestCase):
    """The final mux must force limited-range yuv420p, or JPEG sources stay yuvj420p."""

    def test_final_mux_sets_limited_range_yuv420p(self):
        source = Path(av.__file__).read_text(encoding="utf-8")
        # Inspect the final mux command block (the one that writes the raw mp4).
        marker = "cmd_mix = ["
        self.assertIn(marker, source)
        block = source[source.index(marker):]
        self.assertIn('"-pix_fmt", "yuv420p"', block)
        self.assertIn('"-color_range", "tv"', block)


class TestProbeDuration(unittest.TestCase):
    def test_missing_file_returns_none(self):
        self.assertIsNone(av.probe_duration("definitely_not_here.mp4"))


if __name__ == "__main__":
    unittest.main()
