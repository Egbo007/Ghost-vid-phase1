#!/usr/bin/env python3
"""Tests for generate_prompts.py — stdlib unittest, no network."""

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

import generate_prompts as gp


CARDS = {
    "character_cards": {
        "_usage_note": "ignore me",
        "ADA": {
            "full_name": "ADAEZE (ADA) OKAFOR",
            "image_prompt_card": "VERBATIM ADA CARD.",
            "mandatory_always": "MANDATORY ADA SCAR.",
        },
        "ZION": {
            "full_name": "ZION ADEYEMI",
            "image_prompt_card": "VERBATIM ZION CARD.",
            "mandatory_always": "MANDATORY ZION JAW.",
        },
    }
}
CONFIG = {"series": {"overall_aesthetic": (
    "Dark luxury meets African mysticism. Not Nollywood low-budget. "
    "Not Western fantasy. Entirely its own identity."
)}}


class FakeScene:
    def __init__(self, number, text, mood="romance_tension"):
        self.number = number
        self.text = text
        self.mood = mood


def make_episode(tmp, script_text, cards=None, config=None, refs=None):
    """Creates a minimal episode folder and returns (root, base, script_path)."""
    root = Path(tmp)
    base = root / "ashes-of-the-chosen"
    ep = base / "episode-01"
    ep.mkdir(parents=True)
    script = ep / "episode_01_script.txt"
    script.write_text(script_text, encoding="utf-8")
    (root / "character_cards.json").write_text(
        json.dumps(cards or CARDS), encoding="utf-8")
    (root / "series_config.json").write_text(
        json.dumps(config or CONFIG), encoding="utf-8")
    if refs:
        for name, files in refs.items():
            folder = root / "assets" / "characters" / name
            folder.mkdir(parents=True, exist_ok=True)
            for f in files:
                (folder / f).write_bytes(b"x")
    return root, base, script


SCRIPT = """TITLE: test
[SCENE 1 - MOOD: romance_tension]
(Visual: Close-up of Ada in a market.)
Ada walked on.

[SCENE 2 - MOOD: covenant_villain]
(Visual: A wide shot of an empty hall.)
Zion waited.

"""


class TestHelpers(unittest.TestCase):
    def test_shot_type_first_from_visual(self):
        self.assertEqual(gp.shot_from_visual("Close-up of Ada."), "Close-up")
        self.assertEqual(gp.shot_from_visual("A WIDE establishing shot."), "WIDE")
        self.assertEqual(gp.shot_from_visual("over-the-shoulder angle"),
                         "over-the-shoulder")
        self.assertIsNone(gp.shot_from_visual("A crowded street."))

    def test_medium_close_up_beats_plain_close_up(self):
        self.assertEqual(gp.shot_from_visual("a medium close-up of Ada"),
                         "medium close-up")

    def test_not_sentences_dropped(self):
        clean, dropped = gp.strip_not_prefixes(CONFIG["series"]["overall_aesthetic"])
        self.assertEqual(len(dropped), 2)
        self.assertIn("Not Nollywood low-budget.", dropped)
        self.assertIn("Not Western fantasy.", dropped)
        self.assertNotIn("Not ", clean)
        self.assertIn("Dark luxury meets African mysticism.", clean)

    def test_visual_note_extracted_from_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, script = make_episode(tmp, SCRIPT)
            notes = gp.load_visual_notes(script)
        self.assertIn("Close-up of Ada in a market.", notes[1])
        self.assertIn("wide shot", notes[2].lower())

    def test_character_detection(self):
        names = gp.character_names("Ada met Zion.", "", CARDS)
        self.assertEqual(names, ["ADA", "ZION"])
        self.assertEqual(gp.character_names("Nobody here.", "", CARDS), [])

    def test_image_plan_contract(self):
        self.assertEqual(gp.image_plan(1),
                         [("1a", "scene_01a.png"), ("1b", "scene_01b.png")])
        self.assertEqual(gp.image_plan(3), [("3", "scene_03.png")])

    def test_references_round_robin_and_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("ada", "zion"):
                folder = root / "assets" / "characters" / name
                folder.mkdir(parents=True)
                for i in range(3):
                    (folder / f"ref_{i}.jpg").write_bytes(b"x")
            refs = gp.find_references(["ADA", "ZION"], repo_root=root)
        self.assertEqual(len(refs), 4)
        self.assertEqual([p.parent.name for p in refs],
                         ["ada", "zion", "ada", "zion"])

    def test_missing_reference_folder_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("generate_prompts", level="WARNING") as logs:
                refs = gp.find_references(["ADA"], repo_root=tmp)
        self.assertEqual(refs, [])
        self.assertIn("No reference folder", "\n".join(logs.output))

    def test_prompt_order(self):
        prompt = gp.build_prompt(
            "Close-up", ["VERBATIM ADA CARD.", "MANDATORY ADA SCAR."],
            "A market scene.", "Cinematic lighting.", "Dark luxury.", ["ref.jpg"]
        )
        self.assertTrue(prompt.startswith("Close-up"))
        self.assertLess(prompt.index("Close-up"), prompt.index("VERBATIM ADA CARD."))
        self.assertLess(prompt.index("MANDATORY ADA SCAR."), prompt.index("A market"))
        self.assertLess(prompt.index("A market"), prompt.index("Cinematic lighting."))
        self.assertLess(prompt.index("Cinematic lighting."), prompt.index("Dark luxury."))
        self.assertTrue(prompt.endswith(gp.REFERENCE_PHRASE))


class TestGenerate(unittest.TestCase):
    def test_full_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, script = make_episode(
                tmp, SCRIPT,
                refs={"ada": ["ref_1.jpg", "ref_2.jpg"]}
            )
            entries, dropped = gp.generate_prompts(1, output_dir=base, repo_root=root)
        # Scene 1 has two images (a/b), scene 2 has one: 3 entries from 2 scenes.
        self.assertEqual(len(entries), 3)
        self.assertEqual([e["file_name"] for e in entries],
                         ["scene_01a.png", "scene_01b.png", "scene_02.png"])
        self.assertEqual(len(dropped), 2)
        first = entries[0]
        self.assertEqual(first["image_label"], "1a")
        self.assertEqual(first["characters"], ["ADA"])
        self.assertEqual(len(first["reference_paths"]), 2)
        self.assertTrue(first["prompt"].startswith("Close-up"))
        self.assertIn("VERBATIM ADA CARD.", first["prompt"])
        self.assertIn("MANDATORY ADA SCAR.", first["prompt"])
        self.assertNotIn("Not Nollywood", first["prompt"])

    def test_no_character_means_no_references(self):
        script = ("TITLE: test\n[SCENE 1 - MOOD: wilderness]\n"
                  "(Visual: An empty road.)\nNothing moved.\n")
        with tempfile.TemporaryDirectory() as tmp:
            root, base, script_path = make_episode(
                tmp, script, refs={"ada": ["ref_1.jpg"]})
            entries, _ = gp.generate_prompts(1, output_dir=base, repo_root=root)
        self.assertEqual(entries[0]["characters"], [])
        self.assertEqual(entries[0]["reference_paths"], [])
        self.assertNotIn(gp.REFERENCE_PHRASE, entries[0]["prompt"])

    def test_fallback_shots_alternate(self):
        script = ("TITLE: test\n[SCENE 1 - MOOD: romance_tension]\n"
                  "(Visual: Ada in a room.)\nAda waited.\n")
        with tempfile.TemporaryDirectory() as tmp:
            root, base, script_path = make_episode(tmp, script)
            entries, _ = gp.generate_prompts(1, output_dir=base, repo_root=root)
        self.assertTrue(entries[0]["prompt"].startswith("Close-up"))
        self.assertTrue(entries[1]["prompt"].startswith("Wide"))

    def test_print_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, base, script = make_episode(tmp, SCRIPT)
            with mock.patch.object(gp, "generate_prompts",
                                   return_value=([], [])):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = gp.main(["--episode", "1", "--print"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
