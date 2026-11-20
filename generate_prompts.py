#!/usr/bin/env python3
"""
Script-driven image prompts — Ashes of the Chosen (Phase B, Brief 008).

Reads an episode's own script through generate_voice.parse_script (the single
parser in the project) and writes one prompt per image to render. The number
and names of images follow make_placeholders.py / assemble_video.py: scene 1
gets scene_01a.png and scene_01b.png, every other scene gets scene_NN.png.
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import generate_voice
import try_cloudflare_image as cf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("generate_prompts")

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = Path(os.environ.get("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen"))
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 1280
MAX_REFERENCES = 4
REFERENCE_PHRASE = "same face and same hairstyle as the reference images"

# First explicit shot type wins. Ordered so "over-the-shoulder" is matched
# before the bare "shoulder", etc.
SHOT_PATTERNS = [
    ("over-the-shoulder", r"over[- ]the[- ]shoulder"),
    ("medium close-up", r"medium close[- ]?up"),
    ("close-up", r"close[- ]?up|close shot|extreme close"),
    ("low angle", r"low[- ]angle"),
    ("high angle", r"high[- ]angle"),
    ("wide", r"\bwide\b|establishing shot|long shot"),
    ("two-shot", r"two[- ]shot"),
    ("profile", r"profile shot|side profile"),
]
VISUAL_DIRECTIVE_RE = re.compile(r"\(\s*visual\s*:?(.*?)\)", re.IGNORECASE | re.DOTALL)
FALLBACK_SHOTS = ("Close-up", "Wide")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_visual_note(scene):
    """Recovers the (Visual: ...) note that M2 strips out of scene['text']."""
    # parse_script strips the directive from `text` but keeps the rest. Prefer
    # the raw script line when available; fall back to any directive left in
    # the scene text (for tests that build scene dicts directly).
    raw = scene.get("raw_body") or scene.get("text") or ""
    notes = [m.group(1).strip() for m in VISUAL_DIRECTIVE_RE.finditer(raw) if m.group(1).strip()]
    return " ".join(notes)


def load_visual_notes(script_path):
    """
    Returns {scene_number: visual note} read from the script file directly.

    M2's parse_script removes directives from its scene dicts, so the raw file
    is the only place they survive. This is a note extractor, not a second
    scene parser: scene boundaries still come from M2.
    """
    text = Path(script_path).read_text(encoding="utf-8-sig")
    notes = {}
    for match in re.finditer(
        r"\[\s*SCENE\s*(\d+)[^\]]*\](.*?)(?=\[\s*SCENE\s*\d+|\Z)",
        text, re.IGNORECASE | re.DOTALL
    ):
        number = int(match.group(1))
        body = match.group(2)
        found = [m.group(1).strip() for m in VISUAL_DIRECTIVE_RE.finditer(body)
                 if m.group(1).strip()]
        if found:
            notes[number] = " ".join(found)
    return notes


def shot_from_visual(note):
    """Returns the shot type named in the Visual note, or None.

    The matched substring is returned with the note's original casing so the
    prompt reads the way the writer wrote it ("Close-up", "WIDE", ...).
    """
    if not note:
        return None
    for label, pattern in SHOT_PATTERNS:
        match = re.search(pattern, note, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return None


def strip_not_prefixes(style_line):
    """
    Removes every sentence that starts with 'Not ' from the style line.

    The model has no negative prompt; naming what a thing is not pulls it
    toward that thing. Returns (cleaned_line, dropped_sentences).
    """
    if not style_line:
        return "", []
    sentences = re.split(r"(?<=[.!?])\s+", style_line.strip())
    kept, dropped = [], []
    for sentence in sentences:
        if sentence.strip().lower().startswith("not "):
            dropped.append(sentence.strip())
        else:
            kept.append(sentence.strip())
    return " ".join(s for s in kept if s), dropped


def character_names(scene_text, visual_note, cards_data):
    """
    Character ids present in a scene, in card-file order.

    Matches the card key (ADA, ZION, ...) and the full name, case-insensitively,
    on word boundaries. '_usage_note' and other metadata keys are excluded.
    """
    haystack = f"{scene_text or ''} {visual_note or ''}".lower()
    cards = cards_data.get("character_cards", {})
    present = []
    for key, card in cards.items():
        if key.startswith("_") or not isinstance(card, dict):
            continue
        names = [key.replace("_", " "), key.lower()]
        full = card.get("full_name")
        if isinstance(full, str) and full.strip():
            names.append(full.strip().lower())
        # "ADAEZE (ADA) OKAFOR" -> also match the short name in parentheses.
        paren = re.findall(r"\(([^)]+)\)", full or "")
        names.extend(p.strip().lower() for p in paren)
        if any(re.search(r"\b" + re.escape(n) + r"\b", haystack) for n in names if n):
            present.append(key)
    return present


def character_text(cards_data, names):
    """Verbatim card text + mandatory_always for each present character."""
    cards = cards_data.get("character_cards", {})
    parts = []
    for name in names:
        card = cards.get(name, {})
        for field in cf.CARD_FIELDS:
            value = card.get(field)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return parts


def find_references(names, repo_root=None):
    """
    Round-robin reference paths, capped at MAX_REFERENCES total.

    For each character, files in assets/characters/<lowercase name>/ matching
    ref_*.jpg/.jpeg/.png, sorted by name. Missing folders are a warning, not
    an error.
    """
    root = Path(repo_root) if repo_root else REPO_ROOT
    per_character = []
    for name in names:
        folder = root / "assets" / "characters" / name.lower()
        if not folder.is_dir():
            logger.warning(f"No reference folder for {name}: {folder}. Continuing without it.")
            per_character.append([])
            continue
        files = sorted(
            p for p in folder.iterdir()
            if p.is_file() and p.name.lower().startswith("ref_")
            and p.suffix.lower() in (".jpg", ".jpeg", ".png")
        )
        if not files:
            logger.warning(f"No ref_* images in {folder}. Continuing without references.")
        per_character.append(files)

    result = []
    depth = 0
    while len(result) < MAX_REFERENCES:
        added = False
        for files in per_character:
            if depth < len(files):
                result.append(files[depth])
                added = True
                if len(result) >= MAX_REFERENCES:
                    break
        if not added:
            break
        depth += 1
    return result


def image_plan(scene_number):
    """Returns [(image_label, file_name), ...] for one scene, per M4 contract."""
    if scene_number == 1:
        return [("1a", f"scene_{scene_number:02d}a.png"),
                ("1b", f"scene_{scene_number:02d}b.png")]
    return [(f"{scene_number}", f"scene_{scene_number:02d}.png")]


def build_prompt(shot, character_parts, visual_note, mood_words, style_line,
                 references):
    """Assembles the prompt in the order required by the brief."""
    parts = [shot] if shot else []
    parts.extend(character_parts)
    if visual_note:
        parts.append(visual_note.strip())
    if mood_words:
        parts.append(mood_words.strip())
    if style_line:
        parts.append(style_line.strip())
    if references:
        parts.append(REFERENCE_PHRASE)

    prompt = ""
    for part in parts:
        if not part:
            continue
        if not prompt:
            prompt = part
        elif prompt.endswith((".", "!", "?", ";", ":")):
            prompt = f"{prompt} {part}"
        else:
            prompt = f"{prompt}. {part}"
    return prompt


def load_mood_words(mood, repo_root=None):
    """prompt_text from mood_templates.json for a scene mood, or ''."""
    root = Path(repo_root) if repo_root else REPO_ROOT
    path = root / "mood_templates.json"
    if not path.exists():
        return ""
    try:
        data = load_json(path)
    except Exception as e:
        logger.warning(f"Could not read mood_templates.json: {type(e).__name__}")
        return ""
    entry = data.get("moods", {}).get(mood, {})
    words = entry.get("prompt_text", "") if isinstance(entry, dict) else ""
    return words.strip() if isinstance(words, str) else ""


def generate_prompts(episode, output_dir=None, repo_root=None, verbose=False):
    """Builds the prompts JSON for an episode. Returns (entries, dropped_sentences)."""
    root = Path(repo_root) if repo_root else REPO_ROOT
    base = Path(output_dir) if output_dir else (root / DEFAULT_OUTPUT_DIR)
    ep_str = f"{episode:02d}"
    ep_dir = base / f"episode-{ep_str}"
    script_path = ep_dir / f"episode_{ep_str}_script.txt"

    scenes = generate_voice.parse_script(str(script_path))
    notes = load_visual_notes(script_path)
    cards_data = load_json(root / "character_cards.json")
    config_data = load_json(root / "series_config.json")
    raw_style = cf.get_style_line(config_data)
    style_line, dropped = strip_not_prefixes(raw_style)
    if dropped:
        logger.info(f"Dropped {len(dropped)} 'Not ' sentence(s) from style line: "
                    + " | ".join(dropped))

    entries = []
    for scene in scenes:
        number = int(scene["scene_number"])
        visual_note = notes.get(number, get_visual_note(scene))
        names = character_names(scene.get("text", ""), visual_note, cards_data)
        card_parts = character_text(cards_data, names)
        refs = find_references(names, repo_root=root) if names else []
        mood_words = load_mood_words(scene.get("mood", ""), repo_root=root)
        named_shot = shot_from_visual(visual_note)

        plan = image_plan(number)
        for index, (label, file_name) in enumerate(plan):
            shot = named_shot or FALLBACK_SHOTS[index % len(FALLBACK_SHOTS)]
            prompt = build_prompt(shot, card_parts, visual_note, mood_words,
                                  style_line, refs)
            entries.append({
                "scene": number,
                "image_label": label,
                "file_name": file_name,
                "width": DEFAULT_WIDTH,
                "height": DEFAULT_HEIGHT,
                "prompt": prompt,
                "reference_paths": [str(p) for p in refs],
                "characters": names,
                "source_visual_note": visual_note,
            })

    ep_dir.mkdir(parents=True, exist_ok=True)
    out_path = ep_dir / f"episode_{ep_str}_image_prompts.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    logger.info(f"Wrote {len(entries)} image prompts to {out_path}")
    return entries, dropped


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build image prompts from an episode script")
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--scene", type=int, default=None, help="Only this scene number")
    parser.add_argument("--print", dest="print_prompts", action="store_true",
                        help="Print every prompt as text")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        entries, dropped = generate_prompts(args.episode)
    except Exception as e:
        logger.error(f"Could not build prompts: {type(e).__name__}: {e}")
        return 1

    if args.scene is not None:
        entries = [e for e in entries if e["scene"] == args.scene]

    if args.print_prompts:
        for e in entries:
            print(f"\n=== Scene {e['scene']} / image {e['image_label']} "
                  f"({e['file_name']}) ===")
            print(f"characters : {', '.join(e['characters']) or '(none)'}")
            print(f"references : {len(e['reference_paths'])}")
            print(f"prompt     : {e['prompt']}")
    else:
        print(f"Wrote {len(entries)} prompt(s) for episode {args.episode:02d}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
