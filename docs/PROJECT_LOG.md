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
| 003 | S1b: harden M1 (validate, retry, fail cleanly) | S1 | Coded and tested; awaiting user verification | B-03, B-13, B-14 fixed. Draft-then-validate via M2's parser; adds `--print-prompt`. |
| 004 | Finish Phase A: repair M4, M5, M6 | S1 | Coded and tested; awaiting user verification | B-06, B-07, B-08, B-15, B-16, B-17 fixed. Adds `make_placeholders.py`, `check_episode.py`. |
| 005 | S1c: fix silence-buffer timing drift | S1 | Coded and tested; awaiting user verification | B-18 fixed. Root cause was the concat step, not the silence length; see B-18. |
| 006 | Phase B: first real Cloudflare image (flux-2-klein-4b) | B | Coded and tested; awaiting the user's real run | Adds `try_cloudflare_image.py` (probe only, not wired into the pipeline). Prompt = Ada's card text verbatim + scene + `series.overall_aesthetic`. Pins `requests==2.34.2`. |
| 007 | Phase B: reference images + prompt file | B | Coded and tested; awaiting the user's real run | `--reference` (repeatable, max 4) centre-crops to square and scales to 448x448 PNG via ffmpeg, sent as `input_image_0..3`. `--prompt-file` replaces the card/scene/style builder. `usage_log.jsonl` records `reference_count`. |
| 008 | Phase B: script-driven prompts + batch generation | B | Coded and tested; awaiting the user's real run | Rewrites `generate_prompts.py` to use M2's parser; adds `image_providers.py` (Cloudflare/Gemini stub) and `generate_images.py` (resume, manifest, quota stop, retries, ffprobe check). Drops the two `Not ` sentences from the style line. |

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
**Status:** Fixed, pending user verification

`max_output_tokens=1024` on a thinking model, with no `finish_reason` check. The 150-220
word range was only logged, never enforced. An invalid or truncated script was written to
disk anyway.

Fixed in Brief 003: `MAX_OUTPUT_TOKENS=8192`; finish reason checked (enum and int forms,
STOP/1 only); draft-then-validate with up to 3 attempts; the final file is only replaced
atomically after validation passes.

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

### B-13 — M1: speaker labels and markdown could be spoken
**Status:** Fixed, pending user verification

The prompt allowed "spoken dialogue" with no format rule, so `ADA: ...` labels and markdown
(`**`, `#`, backticks) could reach the TTS and be read aloud. Validation now rejects speaker
labels at sentence starts, `#`, and backticks; `normalise_script` strips code fences and
emphasis markers first.

### B-14 — M1: rate-limit false positive
**Status:** Fixed, pending user verification

`is_rate_limit` matched any error containing the bare word "rate", which also matches
"generate". Failures were treated as throttling and retried on the long backoff. Detection
now requires one of "429", "quota", "resource_exhausted", "rate limit", "rate_limit",
"too many requests".

### B-15 — M5: caption blocks could overlap
**Status:** Fixed, pending user verification

`end = max(start + 0.4, end + 0.1)` could push a block past the next block's start,
stacking captions. Blocks now end no later than the next starts, with a 0.10 s minimum
visible time where there is room.

### B-16 — M6: silent raw fallback and unsafe cleanup
**Status:** Fixed, pending user verification

Missing subtitled master silently shipped the raw video, and `--cleanup --skip-drive`
deleted scene audio, images, raw video and voice with no backup. Raw now needs
`--allow-raw`; cleanup needs a confirmed Drive sync, or `--skip-drive` plus
`--confirm-cleanup`. Deliverables are never deleted; a zero-byte master is rejected.

### B-17 — ffmpeg errors hidden
**Status:** Fixed, pending user verification

Every module discarded stderr on failure. Each now has a `run_ffmpeg(cmd, label)` helper that logs the last 15 stderr lines and exits 1.

### B-18 — M2: timeline drift from the concat step
**Status:** Fixed, pending user verification

Timestamps and cuts for scene k ran early by about k x 0.06 s. The silence buffer was
blamed, and Brief 004 called the excess "per-scene MP3 padding". Both were wrong: the
silence measures exactly the 0.200 s requested. `-c copy` concat drops each file's MP3
gapless header, so every join padded to a whole 1152-sample frame (~0.124 s at 24 kHz);
episode 99 joined at 61.032 s vs 60.736 s predicted. M2 now re-encodes at concat
(`libmp3lame` 48 kbps) and measures the silence: joined 60.736 s, delta +0.000 s, offset a
constant +0.096 s. The voice check enforces +/-0.05 s and per-scene windows.
---

## Stage history

| Stage | Scope | Result |
|---|---|---|
| S0 | Baseline and hygiene | Complete — `56236ba` |
| S1 | Text to audio | In progress — Brief 002 coded and tested, awaiting user verification |
