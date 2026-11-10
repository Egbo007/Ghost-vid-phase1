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

# Headroom for thinking tokens. 1024 was cutting scripts off mid-sentence.
MAX_OUTPUT_TOKENS = 8192

# How many generated drafts we are willing to validate before giving up.
MAX_VALIDATION_ATTEMPTS = 3

# Moods must match the keys under "moods" in mood_templates.json, which M4 uses
# to pick motion effects. Kept here as a literal so validation needs no file read.
ALLOWED_MOODS = (
    "romance_tension",
    "supernatural",
    "revenge_power",
    "wilderness",
    "covenant_villain",
)

SCENE_HEADER_RE = re.compile(r"^\[SCENE (\d+) - MOOD: ([a-z_]+)\]$")
SPEAKER_LABEL_RE = re.compile(r"^[A-Z][A-Z .'-]{1,30}:\s")
# M2 joins scene narration onto one line, so labels are matched at sentence starts.
SPEAKER_LABEL_INLINE_RE = re.compile(r"(?:^|(?<=[.!?…]\s))([A-Z][A-Z .'-]{1,30}:\s)")
VALID_ENDINGS = (".", "!", "?", "…", '"', "”")

# Errors that mean "slow down", not "something is broken". Deliberately does not
# include the bare word "rate" — that matched "generate" and similar.
RATE_LIMIT_MARKERS = (
    "429",
    "quota",
    "resource_exhausted",
    "rate limit",
    "rate_limit",
    "too many requests",
)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Module 1: Script Generator for Ashes of the Chosen")
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Gemini model name")
    parser.add_argument("--force", action="store_true", help="Force overwrite of existing script")
    parser.add_argument("--print-prompt", action="store_true", help="Print the system prompt and exit (no API key or network needed)")
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


def normalise_script(text):
    """
    Removes a wrapping ``` code fence and strips * and _ emphasis markers, so that
    markdown the model adds defensively does not end up spoken by the TTS.
    """
    if not text:
        return ""

    cleaned = text.strip()

    # Strip a wrapping code fence, with or without a language tag.
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    # Remove emphasis markers. Underscores inside a word are structural
    # (covenant_villain), so only strip runs not flanked by alphanumerics.
    cleaned = cleaned.replace("*", "")
    cleaned = _strip_underscore_emphasis(cleaned)

    return cleaned.strip()


def _strip_underscore_emphasis(text):
    """Removes _emphasis_ markers while leaving word_internal underscores intact."""

    def repl(match):
        start, end = match.start(), match.end()
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if before.isalnum() and after.isalnum():
            return match.group(0)
        return ""

    return re.sub(r"_+", repl, text)


def is_rate_limit_error(error_text):
    """True only for genuine throttling, not for any error containing 'rate'."""
    err = (error_text or "").lower()
    return any(marker in err for marker in RATE_LIMIT_MARKERS)


def get_finish_reason(response):
    """
    Returns the finish reason as an uppercase string, or None when unavailable.
    Handles both the enum form (FinishReason.STOP) and the int form (1 = STOP).
    """
    candidates = response.candidates if response is not None else None
    if not candidates:
        return None

    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return None

    name = getattr(reason, "name", None)
    if isinstance(name, str):
        return name.upper()

    try:
        return {0: "UNSPECIFIED", 1: "STOP", 2: "MAX_TOKENS", 3: "SAFETY",
                4: "RECITATION", 5: "OTHER"}.get(int(reason), str(reason).upper())
    except (TypeError, ValueError):
        return str(reason).upper()


def log_token_usage(response):
    """Logs prompt/candidate token counts when the SDK reports them."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return
    prompt_tokens = getattr(usage, "prompt_token_count", None)
    output_tokens = getattr(usage, "candidates_token_count", None)
    total_tokens = getattr(usage, "total_token_count", None)
    if any(v is not None for v in (prompt_tokens, output_tokens, total_tokens)):
        logger.info(
            f"Token usage: prompt={prompt_tokens}, output={output_tokens}, total={total_tokens}"
        )


def validate_script(script_text, draft_path):
    """
    Validates a generated script against what M2 will actually speak.

    draft_path must point at the file holding script_text, because M2's parser reads
    from disk. Returns a list of human-readable error strings; empty means valid.
    """
    errors = []

    if not script_text or not script_text.strip():
        return ["Script is empty."]

    lines = script_text.splitlines()

    # 1. TITLE line must come first.
    first_line = next((ln.strip() for ln in lines if ln.strip()), "")
    if not first_line.upper().startswith("TITLE:"):
        errors.append("First non-empty line must start with 'TITLE:'.")

    # 2. Exactly six scene headers, numbered 1..6 in order.
    headers = []
    for line in lines:
        match = SCENE_HEADER_RE.match(line.strip())
        if match:
            headers.append((int(match.group(1)), match.group(2)))

    if len(headers) != 6:
        errors.append(f"Expected exactly 6 scene headers, found {len(headers)}.")
    else:
        numbers = [num for num, _ in headers]
        if numbers != [1, 2, 3, 4, 5, 6]:
            errors.append(f"Scene headers must be numbered 1..6 in order, found {numbers}.")

    # 3. Moods must be known.
    for num, mood in headers:
        if mood not in ALLOWED_MOODS:
            errors.append(
                f"Scene {num} has unknown mood '{mood}'. Allowed: {', '.join(ALLOWED_MOODS)}."
            )

    # 4. Every scene body must carry a (Visual ...) directive.
    bodies = split_scene_bodies(lines)
    for num, body in bodies.items():
        if "(visual" not in body.lower():
            errors.append(f"Scene {num} has no '(Visual' directive.")

    # 5/6. Delegate the spoken-text rules to M2 — single source of truth.
    try:
        import generate_voice
    except ImportError as e:
        errors.append(f"Could not import generate_voice for validation: {e}")
        return errors

    try:
        scenes = generate_voice.parse_script(str(draft_path))
        generate_voice.validate_scenes(scenes)
    except ValueError as e:
        errors.append(f"M2 parser rejected the script: {e}")
        return errors

    spoken_words = sum(len(scene["text"].split()) for scene in scenes)

    # 7. Word count.
    if spoken_words < MIN_WORDS or spoken_words > MAX_WORDS:
        errors.append(
            f"Spoken word count {spoken_words} is outside the required "
            f"{MIN_WORDS}-{MAX_WORDS} range."
        )

    # 8/9. Speaker labels and markdown in the narration itself. M2 joins scene
    # narration onto one line, so look for a label at a sentence boundary.
    for scene in scenes:
        text = scene["text"]
        label = SPEAKER_LABEL_INLINE_RE.search(text)
        if label:
            errors.append(
                f"Scene {scene['scene_number']} has a speaker label: {label.group(0).strip()!r}"
            )
        if "#" in text:
            errors.append(f"Scene {scene['scene_number']} contains '#' in narration.")
        if "`" in text:
            errors.append(f"Scene {scene['scene_number']} contains a backtick in narration.")

    # 10. Truncation guard.
    if scenes:
        last_text = scenes[-1]["text"].rstrip()
        if last_text and not last_text.endswith(VALID_ENDINGS):
            errors.append(
                f"Script appears truncated: last narration ends with "
                f"{last_text[-1]!r}, expected one of {''.join(VALID_ENDINGS)}."
            )

    return errors


def split_scene_bodies(lines):
    """Maps scene number -> raw body text between that header and the next."""
    bodies = {}
    current = None
    buffer = []
    for line in lines:
        match = SCENE_HEADER_RE.match(line.strip())
        if match:
            if current is not None:
                bodies[current] = "\n".join(buffer)
            current = int(match.group(1))
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        bodies[current] = "\n".join(buffer)
    return bodies


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
7. Write narration as plain continuous prose. Any dialogue must be inline in quotation marks with NO speaker labels (no 'NAME:' prefixes) and no markdown (no *, #, or backticks).
8. Output format:
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
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                }
            )

            log_token_usage(response)

            finish_reason = get_finish_reason(response)
            if finish_reason not in (None, "STOP"):
                raise RuntimeError(
                    f"Gemini stopped early with finish_reason={finish_reason}; "
                    "the script would be incomplete."
                )

            if response and response.text:
                return response.text.strip()
            raise RuntimeError("Empty response received from Gemini API.")
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = is_rate_limit_error(err_str)
            logger.warning(f"Gemini API attempt {attempt} failed: {e}")
            if attempt < max_retries:
                sleep_time = delays[attempt - 1] if is_rate_limit else 5 * attempt
                logger.info(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                raise RuntimeError(f"Gemini API call failed after {max_retries} attempts: {e}")


def main():
    args = parse_arguments()

    # --print-prompt needs no key and no network.
    if args.print_prompt:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        try:
            config = load_series_config()
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Could not load series_config.json: {e}")
            sys.exit(1)
        print(build_system_prompt(config, args.episode))
        sys.exit(0)

    env = load_environment()
    base_out_dir = Path(args.output_dir or env["output_dir"])
    ep_str = f"{args.episode:02d}"
    ep_dir = base_out_dir / f"episode-{ep_str}"
    ep_dir.mkdir(parents=True, exist_ok=True)

    script_file = ep_dir / f"episode_{ep_str}_script.txt"
    draft_file = ep_dir / f"episode_{ep_str}_script_draft.txt"

    if not args.force and script_file.exists():
        logger.info(f"Script already exists at {script_file}. Use --force to regenerate.")
        sys.exit(0)

    try:
        config = load_series_config()
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Could not load series_config.json: {e}")
        sys.exit(1)

    base_prompt = build_system_prompt(config, args.episode)
    prompt = base_prompt
    rejected_files = []
    last_errors = []

    try:
        for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
            raw_text = call_gemini_with_retry(env["api_key"], args.model, prompt)
            script_text = normalise_script(raw_text)

            # Always write to the draft, never to the final file.
            with open(draft_file, "w", encoding="utf-8") as f:
                f.write(script_text + "\n")

            errors = validate_script(script_text, draft_file)

            if not errors:
                spoken_words = count_spoken_words(draft_file)
                os.replace(draft_file, script_file)
                for rejected in rejected_files:
                    if rejected.exists():
                        rejected.unlink()
                logger.info(
                    f"Validation passed: {spoken_words} spoken words, attempt {attempt}"
                )
                logger.info(f"Module 1 complete! Saved Episode {args.episode} script to: {script_file}")
                sys.exit(0)

            last_errors = errors
            rejected = ep_dir / f"episode_{ep_str}_script_rejected_attempt{attempt}.txt"
            os.replace(draft_file, rejected)
            rejected_files.append(rejected)

            logger.warning(
                f"Attempt {attempt}/{MAX_VALIDATION_ATTEMPTS} failed validation "
                f"({len(errors)} error(s)):"
            )
            for err in errors:
                logger.warning(f"  - {err}")

            if attempt < MAX_VALIDATION_ATTEMPTS:
                prompt = (
                    base_prompt
                    + "\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED:\n"
                    + "\n".join(f"- {err}" for err in errors)
                    + "\nFix these and output the complete script again in the exact format."
                )

        logger.error(
            f"All {MAX_VALIDATION_ATTEMPTS} attempts failed validation for Episode "
            f"{args.episode}. The existing script was left untouched. Last errors:"
        )
        for err in last_errors:
            logger.error(f"  - {err}")
        logger.error(
            "Rejected drafts kept for inspection: "
            + ", ".join(str(p.name) for p in rejected_files)
        )
        sys.exit(1)

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except RuntimeError as e:
        logger.error(f"Module 1 failed for Episode {args.episode}: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Module 1 failed for Episode {args.episode}: {type(e).__name__}: {e}")
        sys.exit(1)


def count_spoken_words(script_path):
    """Spoken word count as M2 defines it — the number that actually gets voiced."""
    import generate_voice
    scenes = generate_voice.parse_script(str(script_path))
    return sum(len(scene["text"].split()) for scene in scenes)


if __name__ == "__main__":
    main()
