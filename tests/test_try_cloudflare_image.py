#!/usr/bin/env python3
"""
Tests for try_cloudflare_image.py — Phase B probe.

Stdlib unittest only. No network: every HTTP call is mocked.
"""

import sys
import json
import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import try_cloudflare_image as cf  # noqa: E402

# A 1x1 PNG, so the magic-byte detection is exercised with real bytes.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 60

FAKE_TOKEN = "test-token-value-should-never-be-printed"
FAKE_ACCOUNT = "0123456789abcdef0123456789abcdef"


def load_real_cards():
    return cf.load_json(cf.CHARACTER_CARDS_PATH)


def load_real_config():
    return cf.load_json(cf.SERIES_CONFIG_PATH)


class FakeResponse:
    def __init__(self, status_code, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class TestPromptBuilder(unittest.TestCase):
    """build_prompt: card text verbatim, then scene, then style line."""

    def setUp(self):
        self.cards = load_real_cards()
        self.config = load_real_config()
        self.card = cf.get_character_card(self.cards, "ADA")

    def test_ada_is_the_default_and_exists(self):
        self.assertIn("ADA", self.cards["character_cards"])
        self.assertEqual(cf.DEFAULT_CHARACTER, "ADA")

    def test_card_text_is_verbatim(self):
        """The card description must appear byte-for-byte, not paraphrased."""
        prompt = cf.build_prompt(self.card, "close-up portrait", None)
        self.assertIn(self.card["image_prompt_card"], prompt)
        self.assertIn(self.card["mandatory_always"], prompt)

    def test_mandatory_fingerprint_is_included(self):
        """The scar line is the consistency mechanism; it must never be dropped."""
        prompt = cf.build_prompt(self.card, "close-up portrait", None)
        self.assertIn("small scar above left eyebrow", prompt)

    def test_scene_text_is_included(self):
        scene = "close-up portrait, cinematic dark luxury lighting"
        prompt = cf.build_prompt(self.card, scene, None)
        self.assertIn(scene, prompt)

    def test_style_line_appended_last(self):
        style = cf.get_style_line(self.config)
        self.assertIsNotNone(style)
        prompt = cf.build_prompt(self.card, "a scene", style)
        self.assertTrue(prompt.endswith(style))

    def test_order_is_card_then_scene_then_style(self):
        style = "STYLE MARKER"
        prompt = cf.build_prompt(self.card, "SCENE MARKER", style)
        self.assertLess(prompt.index(self.card["image_prompt_card"]), prompt.index("SCENE MARKER"))
        self.assertLess(prompt.index("SCENE MARKER"), prompt.index(style))

    def test_style_line_from_series_config(self):
        style = cf.get_style_line(self.config)
        self.assertEqual(style, self.config["series"]["overall_aesthetic"])

    def test_missing_style_line_returns_none(self):
        self.assertIsNone(cf.get_style_line({"series": {}}))
        self.assertIsNone(cf.get_style_line({}))

    def test_unknown_character_raises_with_valid_ids(self):
        with self.assertRaises(KeyError) as ctx:
            cf.get_character_card(self.cards, "NOBODY")
        self.assertIn("ADA", str(ctx.exception))

    def test_usage_note_key_is_not_a_character(self):
        with self.assertRaises(KeyError):
            cf.get_character_card(self.cards, "_usage_note")

    def test_no_double_periods(self):
        """Card text ends in a full stop; the joiner must not add a second one."""
        style = cf.get_style_line(self.config)
        prompt = cf.build_prompt(self.card, "close-up portrait", style)
        self.assertNotIn("..", prompt)

    def test_prompt_sources_reported(self):
        fields, style_source = cf.describe_prompt_sources(
            self.card, cf.get_style_line(self.config)
        )
        self.assertIn("image_prompt_card", fields)
        self.assertIn("mandatory_always", fields)
        self.assertEqual(style_source, "series.overall_aesthetic")


class TestDryRun(unittest.TestCase):
    """--dry-run prints the prompt and makes no network call."""

    def test_dry_run_makes_no_request(self):
        with mock.patch.object(cf, "post_image") as post:
            with mock.patch.object(cf, "request_with_retries") as retry:
                code = cf.main(["--scene", "a test scene", "--dry-run"])
        self.assertEqual(code, 0)
        post.assert_not_called()
        retry.assert_not_called()

    def test_dry_run_prints_prompt_and_masks_account(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            cf.main(["--scene", "a test scene", "--dry-run"])
        out = buf.getvalue()
        self.assertIn("DRY RUN", out)
        self.assertIn("PROMPT", out)
        self.assertIn("a test scene", out)
        self.assertIn("masked", out)
        # The real account id must not appear anywhere.
        self.assertNotIn(FAKE_ACCOUNT, out)

    def test_dry_run_needs_no_credentials(self):
        """Dry run must work with no .env values at all."""
        with mock.patch.dict(cf.os.environ, {}, clear=True):
            code = cf.main(["--scene", "a test scene", "--dry-run"])
        self.assertEqual(code, 0)


class TestMasking(unittest.TestCase):
    """The account id is never shown in full."""

    def test_mask_hides_middle(self):
        masked = cf.mask_account_id(FAKE_ACCOUNT)
        self.assertNotIn(FAKE_ACCOUNT, masked)
        self.assertIn("...", masked)

    def test_short_id_fully_masked(self):
        self.assertEqual(cf.mask_account_id("abc"), "***")

    def test_missing_id(self):
        self.assertEqual(cf.mask_account_id(""), "<missing>")

    def test_endpoint_contains_model(self):
        url = cf.build_endpoint(FAKE_ACCOUNT)
        self.assertIn(cf.MODEL_ID, url)
        self.assertTrue(url.startswith("https://api.cloudflare.com/client/v4/accounts/"))


class TestResponseParsing(unittest.TestCase):
    """extract_image_bytes: documented shape, and safe failure otherwise."""

    def test_documented_shape(self):
        payload = {"result": {"image": base64.b64encode(PNG_BYTES).decode()},
                   "success": True, "errors": []}
        data, problem = cf.extract_image_bytes(payload)
        self.assertIsNone(problem)
        self.assertEqual(data, PNG_BYTES)

    def test_result_string_shape(self):
        payload = {"result": base64.b64encode(PNG_BYTES).decode()}
        data, problem = cf.extract_image_bytes(payload)
        self.assertIsNone(problem)
        self.assertEqual(data, PNG_BYTES)

    def test_unexpected_shape_reports_keys_only(self):
        payload = {"result": {"something_else": "x"}, "success": True}
        data, problem = cf.extract_image_bytes(payload)
        self.assertIsNone(data)
        self.assertIn("something_else", problem)

    def test_top_level_keys_reported_when_result_missing(self):
        payload = {"weird": 1, "other": 2}
        data, problem = cf.extract_image_bytes(payload)
        self.assertIsNone(data)
        self.assertIn("weird", problem)
        self.assertIn("other", problem)

    def test_non_dict_payload(self):
        data, problem = cf.extract_image_bytes(["a", "list"])
        self.assertIsNone(data)
        self.assertIn("list", problem)

    def test_invalid_base64(self):
        payload = {"result": {"image": "!!!not base64!!!"}}
        data, problem = cf.extract_image_bytes(payload)
        self.assertIsNone(data)
        self.assertIn("base64", problem)


class TestFormatDetection(unittest.TestCase):
    def test_png(self):
        self.assertEqual(cf.detect_image_format(PNG_BYTES), "png")

    def test_jpeg(self):
        self.assertEqual(cf.detect_image_format(JPEG_BYTES), "jpg")

    def test_unknown(self):
        self.assertIsNone(cf.detect_image_format(b"GIF89a" + b"\x00" * 20))


class TestErrorMessages(unittest.TestCase):
    def test_cloudflare_errors_joined(self):
        payload = {"errors": [{"code": 10000, "message": "Authentication error"}]}
        message = cf.cloudflare_error_message(payload)
        self.assertIn("10000", message)
        self.assertIn("Authentication error", message)

    def test_missing_errors_key(self):
        self.assertIn("no error message", cf.cloudflare_error_message({}))


class TestRetries(unittest.TestCase):
    """429 and 5xx retry, then fail cleanly."""

    def test_429_retries_then_fails(self):
        responses = [FakeResponse(429, {"errors": [{"message": "rate limited"}]})] * 3
        session = mock.Mock()
        session.post.side_effect = responses
        sleep = mock.Mock()

        status, payload, attempts = cf.request_with_retries(
            "https://example.invalid", FAKE_TOKEN, "prompt", 720, 1280,
            session=session, sleep=sleep
        )
        self.assertEqual(status, 429)
        self.assertEqual(attempts, 3)
        self.assertEqual(session.post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_500_retries_then_succeeds(self):
        ok = FakeResponse(200, {"result": {"image": base64.b64encode(PNG_BYTES).decode()}})
        session = mock.Mock()
        session.post.side_effect = [FakeResponse(503, {}), ok]
        sleep = mock.Mock()

        status, payload, attempts = cf.request_with_retries(
            "https://example.invalid", FAKE_TOKEN, "prompt", 720, 1280,
            session=session, sleep=sleep
        )
        self.assertEqual(status, 200)
        self.assertEqual(attempts, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_400_does_not_retry(self):
        session = mock.Mock()
        session.post.return_value = FakeResponse(400, {"errors": [{"message": "bad"}]})
        sleep = mock.Mock()

        status, _, attempts = cf.request_with_retries(
            "https://example.invalid", FAKE_TOKEN, "prompt", 720, 1280,
            session=session, sleep=sleep
        )
        self.assertEqual(status, 400)
        self.assertEqual(attempts, 1)
        self.assertEqual(session.post.call_count, 1)
        sleep.assert_not_called()

    def test_network_exception_fails_cleanly(self):
        session = mock.Mock()
        session.post.side_effect = ConnectionError("boom")
        status, payload, attempts = cf.request_with_retries(
            "https://example.invalid", FAKE_TOKEN, "prompt", 720, 1280,
            session=session, sleep=mock.Mock()
        )
        self.assertIsNone(status)
        self.assertEqual(attempts, 1)


class TestSuccessPath(unittest.TestCase):
    """A good response saves a file and logs usage."""

    def run_main(self, responses, extra_args=None):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        session = mock.Mock()
        session.post.side_effect = responses
        args = ["--scene", "a test scene"] + (extra_args or [])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(cf.os.environ,
                             {"CLOUDFLARE_ACCOUNT_ID": FAKE_ACCOUNT,
                              "CLOUDFLARE_API_TOKEN": FAKE_TOKEN}):
            with mock.patch("requests.post", side_effect=session.post):
                with mock.patch.object(cf, "probe_pixels", return_value="720x1280"):
                    with redirect_stdout(out), redirect_stderr(err):
                        code = cf.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_success_saves_png_and_logs_usage(self):
        payload = {"result": {"image": base64.b64encode(PNG_BYTES).decode()},
                   "success": True, "errors": []}
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.run_main([FakeResponse(200, payload)],
                                           ["--out", tmp])
            self.assertEqual(code, 0, err)
            files = list(Path(tmp).glob("cf_*.png"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), PNG_BYTES)
            self.assertIn("720x1280", out)

            log_path = Path(tmp) / "usage_log.jsonl"
            self.assertTrue(log_path.exists())
            entry = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["http_status"], 200)
            self.assertEqual(entry["width"], 720)
            self.assertEqual(entry["height"], 1280)
            self.assertIn("timestamp", entry)

    def test_success_saves_jpeg(self):
        payload = {"result": {"image": base64.b64encode(JPEG_BYTES).decode()}}
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.run_main([FakeResponse(200, payload)], ["--out", tmp])
            self.assertEqual(code, 0, err)
            self.assertEqual(len(list(Path(tmp).glob("cf_*.jpg"))), 1)

    def test_unexpected_shape_exits_1(self):
        payload = {"result": {"unexpected": "field"}, "success": True}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("try_cloudflare_image", level="ERROR") as logs:
                code, out, err = self.run_main([FakeResponse(200, payload)], ["--out", tmp])
            self.assertEqual(code, 1)
            self.assertIn("unexpected", "\n".join(logs.output))
            self.assertEqual(list(Path(tmp).glob("cf_*.png")), [])

    def test_429_exits_1_with_allowance_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("try_cloudflare_image", level="ERROR") as logs:
                code, _, err = self.run_main(
                    [FakeResponse(429, {"errors": [{"message": "rate limited"}]})] * 3,
                    ["--out", tmp]
                )
            text = "\n".join(logs.output)
            self.assertEqual(code, 1)
            self.assertIn("429", text)
            self.assertIn("daily allowance", text.lower())

    def test_missing_credentials_exits_1(self):
        with mock.patch.dict(cf.os.environ, {}, clear=True):
            with self.assertLogs("try_cloudflare_image", level="ERROR") as logs:
                code = cf.main(["--scene", "a test scene"])
        self.assertEqual(code, 1)
        self.assertIn("CLOUDFLARE_ACCOUNT_ID", "\n".join(logs.output))

    def test_token_never_printed_on_success(self):
        payload = {"result": {"image": base64.b64encode(PNG_BYTES).decode()}}
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.run_main([FakeResponse(200, payload)], ["--out", tmp])
        self.assertNotIn(FAKE_TOKEN, out + err)

    def test_token_never_printed_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.run_main(
                [FakeResponse(401, {"errors": [{"message": "Authentication error"}]})],
                ["--out", tmp]
            )
        self.assertEqual(code, 1)
        self.assertNotIn(FAKE_TOKEN, out + err)

    def test_account_id_masked_in_logs(self):
        payload = {"result": {"image": base64.b64encode(PNG_BYTES).decode()}}
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.run_main([FakeResponse(200, payload)], ["--out", tmp])
        self.assertNotIn(FAKE_ACCOUNT, out + err)


class TestRequestShape(unittest.TestCase):
    """The multipart request carries prompt, width and height."""

    def test_fields_and_header(self):
        session = mock.Mock()
        session.post.return_value = FakeResponse(200, {"result": {}})
        cf.post_image("https://example.invalid", FAKE_TOKEN, "a prompt",
                      720, 1280, session=session)

        kwargs = session.post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {FAKE_TOKEN}")
        self.assertEqual(kwargs["files"]["prompt"], (None, "a prompt"))
        self.assertEqual(kwargs["files"]["width"], (None, "720"))
        self.assertEqual(kwargs["files"]["height"], (None, "1280"))
        self.assertIn("timeout", kwargs)


if __name__ == "__main__":
    unittest.main()
