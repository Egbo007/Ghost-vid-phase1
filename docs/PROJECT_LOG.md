# PROJECT LOG — Ashes of the Chosen

Append-only record of stages, tasks and known defects. Newest task rows go at the bottom of
the task log; bug status changes are recorded in the bug register.

---

## Current stage

**S0 — Baseline and project hygiene.** Documentation only. No behaviour change.

Baseline is tagged `baseline-v0`. Work happens on branch `stage0-hygiene`. Nothing is
pushed or merged from this stage.

---

## Task log

| Brief | Title | Stage | Status | Notes |
|---|---|---|---|---|
| 001 | Stage 0 baseline and project hygiene | S0 | In progress | Docs only. No `.py`, `.json` or `requirements.txt` changes. |

---

## Bug register

All bugs below are **Open**. None are fixed in S0 — this stage deliberately changes no
behaviour. Severity is not assigned here; ordering is by module.

### B-01 — M2: word timestamps are always empty
**Status:** Open · **Confirmed**

`generate_voice.py` calls `edge_tts.Communicate(text, voice)` without
`boundary="WordBoundary"`. edge-tts 7.3.1 defaults to sentence boundaries, so the stream
never emits `WordBoundary` chunks. `word_boundaries` stays empty, `episode_XX_timestamps.json`
is written with zero words, and M5 has nothing to sync subtitles to.

Confirmed by inspection of `generate_voice.py` line 145 against the installed edge-tts
version. `requirements.txt` pins `edge-tts>=6.1.9`, which resolves to 7.3.1.

### B-02 — M2: parser leaks visual cues into narration
**Status:** Open

`parse_script` skips a line only when it both starts with `(` **and** ends with `)`.
Truncated visual lines, or `(Visual: ...)` cues wrapped across multiple lines, fail that
test and get spoken as narration — and counted in the word total.

### B-03 — M1: no enforcement of the word range or finish reason
**Status:** Open

`max_output_tokens=1024` on a thinking model, with no `finish_reason` check. The 150-220
word range is only logged (`generate_script.py` line 249), never enforced. An invalid or
truncated script is written to disk anyway.

### B-04 — M1: continuity inputs are read but never written
**Status:** Open

`rolling_summary` is read from `series_config.json` and injected into the prompt, but no
code ever writes it back — so it never accumulates. `character_cards.json` and
`negative_prompts.json` exist in the repo and are read by no code at all.

### B-05 — M3: prompt generator describes the wrong story
**Status:** Open

`generate_prompts.py` is a hardcoded post-apocalyptic dictionary. It bears no relation to
the Ashes of the Chosen series bible in `series_config.json`. Any prompt it emits is wrong.

### B-06 — M4: timeline drift and clipping
**Status:** Open

- Video ignores the 0.2 s inter-scene audio gap that M2 inserts, producing roughly 1 s of
  cumulative drift across six scenes.
- `-shortest` may clip the final narration.
- The fade-out is timed from audio length rather than video length.

### B-07 — M4: scaling, fallbacks and audio format
**Status:** Open

- No scale/crop to 9:16 before `zoompan`, so the Ken Burns motion runs on unnormalised
  source dimensions.
- Silent fallback to any available PNG when a scene image is missing — a missing scene
  becomes a duplicated frame instead of an error.
- Single-pass `loudnorm` instead of two-pass.
- Output audio is 96 kHz; should be 48 kHz.

### B-08 — M5: font fallback cannot trigger
**Status:** Open

`FALLBACK_FONT` logic is unreachable — libass substitutes a missing font silently, so the
code never sees a failure to fall back from. Separately, 52 px may be too small for a
1080x1920 frame.

### B-09 — M6: Drive upload fails on personal Drive
**Status:** Open

The Drive upload path uses a service account, which commonly fails against a personal
Google Drive. `--skip-drive` is the current workaround.

### B-10 — Repo: ignore rules and missing tests
**Status:** Open (partially addressed in S0)

- The `.env.*` pattern hid `.env.example`. **Addressed in S0** by adding `!.env.example`
  directly after `.env.*`. The register entry stays Open because the remaining items below
  are not resolved.
- The `*.png` rule will hide character reference images once the character bible stage
  introduces them. Not yet an active problem; will become one at S3.
- No tests exist anywhere in the repo.

---

## Stage history

| Stage | Scope | Result |
|---|---|---|
| S0 | Baseline and hygiene | In progress |
