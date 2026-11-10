# PROJECT LOG — Ashes of the Chosen

Append-only record of stages, tasks and known defects. Newest task rows go at the bottom of
the task log; bug status changes are recorded in the bug register.

---

## Current stage

**S1 — Text to audio.** Brief 002 (S1a) fixes M2: word-level timestamps and
stage-direction parsing. Coded and tested; awaiting user verification.

Baseline is tagged `baseline-v0`. S0 work is on `stage0-hygiene`; S1a work is on
`stage1-m2-word-timing`. Nothing is pushed or merged.

---

## Task log

| Brief | Title | Stage | Status | Notes |
|---|---|---|---|---|
| 001 | Stage 0 baseline and project hygiene | S0 | Complete | Docs only. Committed `56236ba` on `stage0-hygiene`. |
| 002 | S1a: fix M2 word timing and stage-direction parsing | S1 | Coded and tested; awaiting user verification | B-01, B-02, B-12 fixed. Adds `tests/`, `episode_XX_spoken.txt`, `edge-tts>=7.3.1,<8`. |

---

## Bug register

Bugs are **Open** unless marked otherwise. "Fixed, pending user verification" means the
code and unit tests are in place but the fix has not been confirmed against real Edge-TTS
output. Severity is not assigned here; ordering is by module.

### B-01 — M2: word timestamps are always empty
**Status:** Fixed, pending user verification

`generate_voice.py` called `edge_tts.Communicate(text, voice)` without
`boundary="WordBoundary"`. edge-tts 7.3.1 defaults to sentence boundaries, so the stream
never emitted `WordBoundary` chunks. `word_boundaries` stayed empty,
`episode_XX_timestamps.json` was written with zero words, and M5 had nothing to sync
subtitles to.

Fixed in Brief 002: `Communicate(..., boundary="WordBoundary")`, plus a guard that raises
inside the retry loop if non-empty text yields zero word boundaries, so an empty timing
file can no longer be written silently. `requirements.txt` now pins `edge-tts>=7.3.1,<8`.

### B-02 — M2: parser leaks visual cues into narration
**Status:** Fixed, pending user verification

`parse_script` skipped a line only when it both started with `(` **and** ended with `)`.
Truncated visual lines, or `(Visual: ...)` cues wrapped across multiple lines, failed that
test and were spoken as narration — and counted in the word total.

Fixed in Brief 002: `strip_visual_directives` removes `(Visual: ...)` directives of any
length, case-insensitively, keeping surrounding narration. An unclosed directive ends at
the next blank line, the next scene header, or end of scene, and logs a WARNING.
Mid-sentence parentheses are preserved. `validate_scenes` then refuses to proceed if any
scene is empty or still contains `Visual:`.

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
- No tests exist anywhere in the repo. **Partially addressed in Brief 002**: `tests/` now
  exists with unit coverage for M2 parsing, validation and word timing.

### B-11 — M2: flashback voice triggered by the word "flashback" in narration
**Status:** Open

`parse_script` sets `is_flashback` when the scene meta **or the narration text** contains
the string "flashback". A line of dialogue that merely mentions a flashback switches the
scene to `en-NG-AbeoNeural`, which is a continuity and casting decision made by a substring
match. Recorded here rather than fixed in Brief 002, which was scoped to timing and parsing
only.

### B-12 — M2: ffprobe failure returned 0.0
**Status:** Fixed, pending user verification

`get_audio_duration` caught every ffprobe exception, logged a warning, and returned `0.0`.
A zero duration silently corrupts `episode_XX_scene_durations.json` (which M4 cuts to) and
collapses the running timeline for every later scene.

Fixed in Brief 002: it raises `RuntimeError` instead, and `main()` reports it and exits 1.

---

## Stage history

| Stage | Scope | Result |
|---|---|---|
| S0 | Baseline and hygiene | Complete — `56236ba` |
| S1 | Text to audio | In progress — Brief 002 coded and tested, awaiting user verification |
