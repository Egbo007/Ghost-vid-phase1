#!/usr/bin/env python3
"""
Tests for Module 1 (generate_script.py) — validation, normalisation, retry flow.

Standard library only. No network, no API key: the Gemini SDK is faked.
Scripts are built inside the tests.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import generate_script as gs  # noqa: E402

MOODS = ["romance_tension", "supernatural", "revenge_power", "wilderness", "covenant_villain"]

# Narration per scene, tuned so a full six-scene script lands near 180 spoken words.
SCENE_LINES = [
    "They told her the covenant died with her mother and that the compound had been "
    "sold to strangers years before she learned to read. Ada had stopped believing "
    "people like that by the time she turned fourteen.",
    "The invitation arrived without a name on it, only the seal, and Chief Obanla "
    "had seen that seal once before on the night he signed his own name and another "
    "woman's death.",
    "Ada did not knock at the gate. It opened before her hand could touch the rust, "
    "and the air inside smelled of rain that had not fallen in eleven years.",
    "Her mother had taught her the first word of the covenant in this exact place. "
    "Say it once and the ground remembers you. Say it twice and it comes looking.",
    "Every name in the ledger had eaten from her family's table. She read them slowly, "
    "the way you read something you intend to answer for, and she did not stop.",
    "The last name in the ledger was hers, written in her own hand and dated tomorrow, "
    "and behind her the door was already closing.",
]


def build_script(scene_lines=None, moods=None, header_fmt=None, visual=True,
                 title="TITLE: ASHES OF THE CHOSEN | EP.1 | The Invitation",
                 ending="."):
    """Builds a six-scene script in the pipeline's format."""
    scene_lines = scene_lines if scene_lines is not None else SCENE_LINES
    moods = moods if moods is not None else MOODS

    parts = []
    if title is not None:
        parts.append(title)
        parts.append("")

    for i, line in enumerate(scene_lines, start=1):
        mood = moods[(i - 1) % len(moods)]
        header = header_fmt.format(n=i, mood=mood) if header_fmt else f"[SCENE {i} - MOOD: {mood}]"
        parts.append(header)
        if visual:
            parts.append(f"(Visual: scene {i} establishing shot, Ada in frame.)")
        parts.append(line.rstrip(".") + ending)
        parts.append("")

    return "\n".join(parts)


def write_and_validate(text, tmpdir):
    """Writes text to a draft file and runs validate_script against it."""
    path = Path(tmpdir) / "episode_01_script_draft.txt"
    path.write_text(text + "\n", encoding="utf-8")
    return gs.validate_script(text, path)


class TestValidateScript(unittest.TestCase):
    """validate_script: the rules that decide whether a draft may be spoken."""

    def test_valid_six_scene_script_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(), tmp)
        self.assertEqual(errors, [], f"unexpected errors: {errors}")

    def test_valid_script_word_count_in_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.txt"
            path.write_text(build_script() + "\n", encoding="utf-8")
            words = gs.count_spoken_words(path)
        self.assertGreaterEqual(words, gs.MIN_WORDS)
        self.assertLessEqual(words, gs.MAX_WORDS)

    def test_multiline_visual_is_accepted(self):
        script = build_script()
        script = script.replace(
            "(Visual: scene 1 establishing shot, Ada in frame.)",
            "(Visual: Ada stands at the market edge,\nthe scar above her left eyebrow catching the light.)",
        )
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(script, tmp)
        self.assertEqual(errors, [], f"unexpected errors: {errors}")

    def test_five_scenes_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(scene_lines=SCENE_LINES[:5]), tmp)
        self.assertTrue(any("6 scene headers" in e for e in errors), errors)

    def test_seven_scenes_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(scene_lines=SCENE_LINES + SCENE_LINES[:1]), tmp)
        self.assertTrue(any("6 scene headers" in e for e in errors), errors)

    def test_scenes_out_of_order_fails(self):
        script = build_script()
        script = script.replace("[SCENE 2 - MOOD:", "[SCENE 9 - MOOD:")
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(script, tmp)
        self.assertTrue(any("numbered 1..6" in e for e in errors), errors)

    def test_unknown_mood_fails(self):
        moods = ["romance_tension", "supernatural", "revenge_power", "wilderness", "sadness"]
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(moods=moods), tmp)
        self.assertTrue(any("unknown mood" in e for e in errors), errors)

    def test_missing_title_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(title=None), tmp)
        self.assertTrue(any("TITLE:" in e for e in errors), errors)

    def test_scene_without_visual_fails(self):
        script = build_script()
        script = script.replace("(Visual: scene 3 establishing shot, Ada in frame.)\n", "")
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(script, tmp)
        self.assertTrue(any("no '(Visual'" in e for e in errors), errors)

    def test_too_few_words_fails(self):
        short = ["She came back.", "The gate opened.", "Nobody spoke.",
                 "The lamp burned.", "He signed it.", "It was her name."]
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(scene_lines=short), tmp)
        self.assertTrue(any("outside the required" in e for e in errors), errors)

    def test_too_many_words_fails(self):
        long = [line + " " + line for line in SCENE_LINES]
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(scene_lines=long), tmp)
        self.assertTrue(any("outside the required" in e for e in errors), errors)

    def test_speaker_label_fails(self):
        lines = list(SCENE_LINES)
        lines[0] = "ADA: " + lines[0]
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(scene_lines=lines), tmp)
        self.assertTrue(any("speaker label" in e for e in errors), errors)

    def test_hash_in_narration_fails(self):
        lines = list(SCENE_LINES)
        lines[1] = lines[1] + " #episode"
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(scene_lines=lines), tmp)
        self.assertTrue(any("'#'" in e for e in errors), errors)

    def test_truncated_ending_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(build_script(ending=""), tmp)
        self.assertTrue(any("truncated" in e for e in errors), errors)

    def test_wrapped_visual_not_counted_as_spoken(self):
        """A Visual directive spanning lines must not inflate the spoken count."""
        script = build_script()
        script = script.replace(
            "(Visual: scene 1 establishing shot, Ada in frame.)",
            "(Visual: Ada stands at the market edge,\n"
            "the scar above her left eyebrow catching the light,\n"
            "crowds blurring past her in the dusk.)",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.txt"
            path.write_text(script + "\n", encoding="utf-8")
            words = gs.count_spoken_words(path)
            errors = gs.validate_script(script, path)
        self.assertEqual(errors, [], f"unexpected errors: {errors}")
        # The directive's words must not be in the count.
        self.assertNotIn("crowds", " ".join(SCENE_LINES))
        self.assertLess(words, gs.MAX_WORDS + 1)


class TestNormaliseScript(unittest.TestCase):
    """normalise_script: strips fences and emphasis, then the script validates."""

    def test_code_fence_and_bold_removed_then_passes(self):
        script = build_script()
        wrapped = "```markdown\n" + script + "\n```"
        normalised = gs.normalise_script(wrapped)

        self.assertFalse(normalised.startswith("```"))
        self.assertNotIn("```", normalised)

        bolded = normalised.replace("Ada", "**Ada**")
        bolded = bolded.replace("ledger", "_ledger_")
        cleaned = gs.normalise_script(bolded)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("_ledger_", cleaned)
        self.assertIn("ledger", cleaned)

        with tempfile.TemporaryDirectory() as tmp:
            errors = write_and_validate(cleaned, tmp)
        self.assertEqual(errors, [], f"unexpected errors: {errors}")


def make_fake_genai(responses):
    """
    Builds a fake google.generativeai module. Each entry in responses is either a
    string (returned as response.text) or a dict with text/finish_reason.
    Records every prompt it was called with.
    """
    calls = []

    class FakeCandidate:
        def __init__(self, finish_reason):
            self.finish_reason = finish_reason

    class FakeResponse:
        def __init__(self, spec):
            if isinstance(spec, dict):
                self.text = spec.get("text", "")
                self.candidates = [FakeCandidate(spec.get("finish_reason", "STOP"))]
            else:
                self.text = spec
                self.candidates = [FakeCandidate("STOP")]
            self.usage_metadata = None

    class FakeModel:
        def __init__(self, name):
            self.name = name

        def generate_content(self, prompt, generation_config=None):
            calls.append({"prompt": prompt, "config": generation_config})
            index = min(len(calls) - 1, len(responses) - 1)
            return FakeResponse(responses[index])

    module = types.ModuleType("google.generativeai")
    module.configure = lambda api_key: None
    module.GenerativeModel = FakeModel
    module._calls = calls

    google_pkg = types.ModuleType("google")
    google_pkg.generativeai = module
    return module, google_pkg


class TestRetryFlow(unittest.TestCase):
    """main(): draft-then-validate, retry with feedback, never clobber on failure."""

    def run_main(self, responses, tmpdir, extra_argv=None):
        """Runs gs.main() with a faked SDK and a temp output dir."""
        fake_genai, fake_google = make_fake_genai(responses)
        argv = ["generate_script.py", "--episode", "1", "--output-dir", tmpdir, "--force"]
        argv += extra_argv or []

        modules = {
            "google": fake_google,
            "google.generativeai": fake_genai,
        }
        with mock.patch.dict(sys.modules, modules):
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(gs, "load_environment",
                                       return_value={"api_key": "test-key-not-real",
                                                     "output_dir": tmpdir}):
                    with mock.patch("time.sleep", new=lambda *_: None):
                        try:
                            gs.main()
                            code = 0
                        except SystemExit as e:
                            code = e.code if e.code is not None else 0
        return code, fake_genai

    def test_first_invalid_second_valid_writes_final_after_two_attempts(self):
        bad = build_script(scene_lines=SCENE_LINES[:5])   # 5 scenes -> invalid
        good = build_script()

        with tempfile.TemporaryDirectory() as tmp:
            code, fake = self.run_main([bad, good], tmp)

            final = Path(tmp) / "episode-01" / "episode_01_script.txt"
            self.assertEqual(code, 0)
            self.assertTrue(final.exists())
            self.assertIn("TITLE:", final.read_text(encoding="utf-8"))

            # Two API calls, and the second carried the rejection feedback.
            self.assertEqual(len(fake._calls), 2)
            self.assertIn("YOUR PREVIOUS ATTEMPT WAS REJECTED", fake._calls[1]["prompt"])

            # Rejected drafts cleaned up on success.
            leftovers = list((Path(tmp) / "episode-01").glob("*rejected*"))
            self.assertEqual(leftovers, [])

    def test_all_attempts_invalid_exits_1_and_preserves_existing_script(self):
        bad = build_script(scene_lines=SCENE_LINES[:5])
        original = "TITLE: ORIGINAL SCRIPT THAT MUST SURVIVE\n"

        with tempfile.TemporaryDirectory() as tmp:
            ep_dir = Path(tmp) / "episode-01"
            ep_dir.mkdir(parents=True)
            final = ep_dir / "episode_01_script.txt"
            final.write_text(original, encoding="utf-8")

            code, fake = self.run_main([bad, bad, bad], tmp)

            self.assertEqual(code, 1)
            self.assertEqual(final.read_text(encoding="utf-8"), original)
            self.assertEqual(len(fake._calls), 3)

            rejected = sorted(p.name for p in ep_dir.glob("*rejected*"))
            self.assertEqual(
                rejected,
                [
                    "episode_01_script_rejected_attempt1.txt",
                    "episode_01_script_rejected_attempt2.txt",
                    "episode_01_script_rejected_attempt3.txt",
                ],
            )

    def test_skips_without_force_when_script_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            ep_dir = Path(tmp) / "episode-01"
            ep_dir.mkdir(parents=True)
            final = ep_dir / "episode_01_script.txt"
            final.write_text("TITLE: existing\n", encoding="utf-8")

            fake_genai, fake_google = make_fake_genai([build_script()])
            argv = ["generate_script.py", "--episode", "1", "--output-dir", tmp]
            with mock.patch.dict(sys.modules, {"google": fake_google,
                                               "google.generativeai": fake_genai}):
                with mock.patch.object(sys, "argv", argv):
                    with mock.patch.object(gs, "load_environment",
                                           return_value={"api_key": "k", "output_dir": tmp}):
                        try:
                            gs.main()
                            code = 0
                        except SystemExit as e:
                            code = e.code if e.code is not None else 0

            self.assertEqual(code, 0)
            self.assertEqual(len(fake_genai._calls), 0)
            self.assertEqual(final.read_text(encoding="utf-8"), "TITLE: existing\n")


class TestFinishReason(unittest.TestCase):
    """get_finish_reason: enum name and int form both understood."""

    def make_response(self, finish_reason):
        candidate = types.SimpleNamespace(finish_reason=finish_reason)
        return types.SimpleNamespace(candidates=[candidate])

    def test_enum_name_stop(self):
        enum = types.SimpleNamespace(name="STOP")
        self.assertEqual(gs.get_finish_reason(self.make_response(enum)), "STOP")

    def test_enum_name_max_tokens(self):
        enum = types.SimpleNamespace(name="MAX_TOKENS")
        self.assertEqual(gs.get_finish_reason(self.make_response(enum)), "MAX_TOKENS")

    def test_int_form_stop(self):
        self.assertEqual(gs.get_finish_reason(self.make_response(1)), "STOP")

    def test_int_form_max_tokens(self):
        self.assertEqual(gs.get_finish_reason(self.make_response(2)), "MAX_TOKENS")

    def test_no_candidates_returns_none(self):
        self.assertIsNone(gs.get_finish_reason(types.SimpleNamespace(candidates=[])))

    def test_max_tokens_response_is_a_failed_attempt(self):
        """A MAX_TOKENS response must not be accepted as a script."""
        truncated = {"text": build_script(), "finish_reason": "MAX_TOKENS"}
        with tempfile.TemporaryDirectory() as tmp:
            fake_genai, fake_google = make_fake_genai([truncated])
            argv = ["generate_script.py", "--episode", "1", "--output-dir", tmp, "--force"]
            with mock.patch.dict(sys.modules, {"google": fake_google,
                                               "google.generativeai": fake_genai}):
                with mock.patch.object(sys, "argv", argv):
                    with mock.patch.object(gs, "load_environment",
                                           return_value={"api_key": "k", "output_dir": tmp}):
                        with mock.patch("time.sleep", new=lambda *_: None):
                            try:
                                gs.main()
                                code = 0
                            except SystemExit as e:
                                code = e.code if e.code is not None else 0

            self.assertEqual(code, 1)
            self.assertFalse((Path(tmp) / "episode-01" / "episode_01_script.txt").exists())

    def test_max_tokens_int_form_is_a_failed_attempt(self):
        truncated = {"text": build_script(), "finish_reason": 2}
        with tempfile.TemporaryDirectory() as tmp:
            fake_genai, fake_google = make_fake_genai([truncated])
            argv = ["generate_script.py", "--episode", "1", "--output-dir", tmp, "--force"]
            with mock.patch.dict(sys.modules, {"google": fake_google,
                                               "google.generativeai": fake_genai}):
                with mock.patch.object(sys, "argv", argv):
                    with mock.patch.object(gs, "load_environment",
                                           return_value={"api_key": "k", "output_dir": tmp}):
                        with mock.patch("time.sleep", new=lambda *_: None):
                            try:
                                gs.main()
                                code = 0
                            except SystemExit as e:
                                code = e.code if e.code is not None else 0

            self.assertEqual(code, 1)


class TestRateLimitDetection(unittest.TestCase):
    """is_rate_limit_error: throttling only, not any error mentioning 'rate'."""

    def test_429_resource_exhausted_is_rate_limit(self):
        self.assertTrue(gs.is_rate_limit_error("429 RESOURCE_EXHAUSTED"))

    def test_failed_to_generate_is_not_rate_limit(self):
        self.assertFalse(gs.is_rate_limit_error("failed to generate"))

    def test_bare_rate_in_generate_is_not_rate_limit(self):
        self.assertFalse(gs.is_rate_limit_error("could not generate content"))

    def test_quota_is_rate_limit(self):
        self.assertTrue(gs.is_rate_limit_error("Quota exceeded for model"))

    def test_too_many_requests_is_rate_limit(self):
        self.assertTrue(gs.is_rate_limit_error("Too Many Requests"))


class TestMoodAlignment(unittest.TestCase):
    """ALLOWED_MOODS must match the moods M4 knows about."""

    def test_allowed_moods_match_mood_templates(self):
        with open(REPO_ROOT / "mood_templates.json", encoding="utf-8") as f:
            config = json.load(f)
        self.assertEqual(set(gs.ALLOWED_MOODS), set(config["moods"].keys()))


class TestPrintPrompt(unittest.TestCase):
    """--print-prompt: prints the prompt, needs no key, makes no API call."""

    def test_print_prompt_contains_new_rule_and_skips_api(self):
        fake_genai, fake_google = make_fake_genai([build_script()])
        argv = ["generate_script.py", "--episode", "1", "--print-prompt"]

        buffer = io.StringIO()
        with mock.patch.dict(sys.modules, {"google": fake_google,
                                           "google.generativeai": fake_genai}):
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(gs, "load_environment",
                                       return_value={"api_key": "", "output_dir": "."}):
                    with redirect_stdout(buffer):
                        try:
                            gs.main()
                            code = 0
                        except SystemExit as e:
                            code = e.code if e.code is not None else 0

        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("NO speaker labels", output)
        self.assertIn("CRITICAL RULES", output)
        self.assertEqual(len(fake_genai._calls), 0)


if __name__ == "__main__":
    unittest.main()
