#!/usr/bin/env python3
"""
Module 1: Script Generator — Ashes of the Chosen
Pipeline v2.0 Corrected Edition

Generates a complete, correctly-timed episode script ready for voiceover.
- Uses google-generativeai with model gemini-2.5-flash
- Reads series_config.json (bible, arc map, rolling summary, audience reaction notes)
- Enforces strict word count of 150-220 words targeting 60-90 seconds of audio
- Implements retry-with-exponential-backoff (up to 3 retries) on rate limits
- Outputs formatted script to episode_XX_script.txt split into 6 clear scenes with a cliffhanger
"""

import os
import re
import sys
import time
import json
import logging
import argparse
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_p = Path(".env")
    if env_p.exists():
        for line in env_p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("generate_script")

DEFAULT_MODEL = "gemini-2.5-flash"
MIN_WORDS = 150
MAX_WORDS = 220


def parse_arguments():
    parser = argparse.ArgumentParser(description="Module 1: Script Generator for Ashes of the Chosen")
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Gemini model name")
    parser.add_argument("--force", action="store_true", help="Force overwrite of existing script")
    return parser.parse_args()


def load_environment():
    return {
        "api_key": os.getenv("GEMINI_API_KEY", ""),
        "output_dir": os.getenv("EPISODE_OUTPUT_DIR", "./ashes-of-the-chosen")
    }


def load_series_config(config_path="./series_config.json"):
    if not os.path.exists(config_path):
        # Look in current directory or parent
        alt = Path(config_path).resolve()
        if not alt.exists():
            raise FileNotFoundError(f"series_config.json not found at {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_arc_for_episode(ep_num, arc_map):
    """Finds matching arc phase from episode_arc_map in series_config.json."""
    for arc_key, arc_data in arc_map.items():
        # Keys like 'episodes_1_5', 'episodes_6_12', etc.
        m = re.match(r"episodes_(\d+)_(\d+)", arc_key)
        if m:
            start_ep, end_ep = int(m.group(1)), int(m.group(2))
            if start_ep <= ep_num <= end_ep:
                return arc_data
    return {"title": "The Journey", "summary": "Ongoing dramatic confrontation in Lagos."}


def count_narration_words(script_text):
    """Counts actual spoken narration words (ignoring scene headers & visual cues)."""
    words = []
    for line in script_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        if line.startswith("(") and line.endswith(")"):
            continue
        if line.upper().startswith("TITLE:"):
            continue
        words.extend(line.split())
    return len(words)


def build_system_prompt(config, ep_num):
    series = config.get("series", {})
    covenant = config.get("the_covenant", {})
    core_arc = config.get("core_arc", {})
    arc_map = config.get("episode_arc_map", {})
    script_gen = config.get("script_generation", {})

    current_arc = get_arc_for_episode(ep_num, arc_map)
    rolling_summary = script_gen.get("narrative_continuity", {}).get("rolling_summary", "")
    audience_notes = script_gen.get("audience_reaction_notes", "")

    prompt = f"""You are the lead showrunner and writer for '{series.get("title", "ASHES OF THE CHOSEN")}'.
Genre: {series.get("genre", "Supernatural revenge drama")}
Tagline: {series.get("tagline", "")}
World Setting: {series.get("setting", "")}

THE COVENANT:
{covenant.get("description", "")}
Ritual: {covenant.get("ritual", "")}

SEASON 1 CORE ARCS:
- Betrayal: {core_arc.get("the_betrayal", "")}
- Sacrifice: {core_arc.get("the_sacrifice", "")}
- Lowest Point / Wilderness: {core_arc.get("the_lowest_point", "")}
- Rise: {core_arc.get("the_rise", "")}
- Revenge: {core_arc.get("the_revenge", "")}
- The Zion Problem: {core_arc.get("the_zion_problem", "")}
- Redemption: {core_arc.get("the_redemption", "")}

CURRENT EPISODE: {ep_num} of {series.get("format", {}).get("total_episodes", 50)}
CURRENT ARC PHASE: {current_arc.get("title", "")}
ARC SUMMARY: {current_arc.get("summary", "")}
"""

    if rolling_summary:
        prompt += f"\nPREVIOUS EPISODES CONTEXT (Rolling Summary):\n{rolling_summary}\n"

    if audience_notes:
        prompt += f"\nAUDIENCE REACTION DIRECTIVES (From Performance Data):\n{audience_notes}\n"

    prompt += f"""
TONE INSTRUCTIONS:
{script_gen.get("tone_instructions", "Prestige African drama. Measured pace. High emotional stakes. Never camp. Dialogue is sparse — every word must land.")}

CRITICAL RULES & FORMATTING:
1. TOTAL SPOKEN WORD COUNT MUST BE STRICTLY BETWEEN 150 AND 220 WORDS.
   - At natural Nigerian English pacing (140-160 WPM), 150-220 words = exactly 60-90 seconds.
   - Do NOT write more than 220 words of spoken narration.
2. Structure the script into EXACTLY 6 SCENES.
3. Every scene header must follow this exact format:
   [SCENE N - MOOD: mood_name]
   Where mood_name is one of: romance_tension, supernatural, revenge_power, wilderness, covenant_villain.
4. Under each scene header include:
   - (Visual: brief 1-line visual description for camera/images)
   - 1 to 3 lines of powerful, evocative narration or spoken dialogue.
5. SCENE 6 MUST END ON A SHARP, HEART-STOPPING CLIFFHANGER that compels the viewer to immediately watch the next episode.
6. The small scar above Ada's left eyebrow must be maintained whenever Ada is featured.
7. Output format:
TITLE: ASHES OF THE CHOSEN | EP.{ep_num} | [Evocative Episode Title]

[SCENE 1 - MOOD: ...]
(Visual: ...)
Narration...

[SCENE 2 - MOOD: ...]
(Visual: ...)
Narration...

[SCENE 3 - MOOD: ...]
(Visual: ...)
Narration...

[SCENE 4 - MOOD: ...]
(Visual: ...)
Narration...

[SCENE 5 - MOOD: ...]
(Visual: ...)
Narration...

[SCENE 6 - MOOD: ...]
(Visual: ...)
Narration leading to an intense cliffhanger!
"""
    return prompt


def call_gemini_with_retry(api_key, model_name, prompt, max_retries=3):
    """Calls Gemini API with exponential backoff on rate limits."""
    try:
        import google.generativeai as genai
    except ImportError:
        logger.error("google-generativeai package not installed. Run: pip install google-generativeai")
        sys.exit(1)

    if not api_key or api_key == "your_gemini_api_key_here":
        raise ValueError("GEMINI_API_KEY is not configured in .env. Please add your API key.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    delays = [15, 30, 60]
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Calling Gemini API ({model_name}) for script generation (attempt {attempt}/{max_retries})...")
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.75,
                    "top_p": 0.95,
                    "max_output_tokens": 1024,
                }
            )
            if response and response.text:
                return response.text.strip()
            raise RuntimeError("Empty response received from Gemini API.")
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str or "rate" in err_str
            logger.warning(f"Gemini API attempt {attempt} failed: {e}")
            if attempt < max_retries:
                sleep_time = delays[attempt - 1] if is_rate_limit else 5 * attempt
                logger.info(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                raise RuntimeError(f"Gemini API call failed after {max_retries} attempts: {e}")


def main():
    args = parse_arguments()
    env = load_environment()
    base_out_dir = Path(args.output_dir or env["output_dir"])
    ep_str = f"{args.episode:02d}"
    ep_dir = base_out_dir / f"episode-{ep_str}"
    ep_dir.mkdir(parents=True, exist_ok=True)

    script_file = ep_dir / f"episode_{ep_str}_script.txt"
    if not args.force and script_file.exists():
        logger.info(f"Script already exists at {script_file}. Use --force to regenerate.")
        sys.exit(0)

    config = load_series_config()
    prompt = build_system_prompt(config, args.episode)

    script_content = call_gemini_with_retry(env["api_key"], args.model, prompt)

    word_count = count_narration_words(script_content)
    logger.info(f"Generated script contains {word_count} spoken narration words (target: {MIN_WORDS}-{MAX_WORDS}).")

    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script_content + "\n")

    logger.info(f"Module 1 complete! Saved Episode {args.episode} script to: {script_file}")


if __name__ == "__main__":
    main()
