# ROADMAP — Ashes of the Chosen

Stages S0 through S6. One stage at a time.

**Rule: do not start a stage until the previous stage's gate has passed.** A gate is a
specific, checkable condition — not "it looks fine." If a gate fails, the stage is not done
and the next one does not open.

---

## S0 — Baseline and project hygiene

**Scope:** Documentation only. Make the repo self-describing and safe to work on. Zero
behaviour change.

**Work:**
- README with pipeline diagram, module status, setup and run order.
- `.env.example` with placeholders only.
- `.gitignore` fix so `.env.example` is tracked.
- Project log, roadmap, decision log, agent rules.

**Gate:** `git status` shows only the intended doc changes; all modules still compile;
`.env` remains ignored and `.env.example` is not; no key material anywhere in tracked files.

---

## S1 — Text to audio

**Scope:** Make M2 produce usable word timings and make M1 trustworthy enough to feed it.

**Work:**
- M2: pass `boundary="WordBoundary"` to `edge_tts.Communicate` (B-01).
- M2: fix `parse_script` so visual cues of any shape are excluded from narration (B-02).
- M1: enforce the 150-220 word range and check finish reason before writing (B-03).

**Gate:** A real Episode 1 script of **150-220 words**, where the word-timestamp count is
approximately equal to the narration word count, and the resulting audio runs **60-90
seconds**. Verified on a real episode, not a smoke test.

---

## S2 — Image provider selection (bake-off)

**Scope:** Choose the image provider on evidence, not vibes.

**Work:**
- Blind bake-off across the candidates: Cloudflare FLUX.2 klein 4B (primary candidate),
  Meta Muse Image (alternative), Google Flow (benchmark).
- Same prompts, same reference images, same blind review, scored on character consistency,
  style match and cost.

**Gate:** A recorded winner with the comparison evidence attached, logged as a decision in
`docs/DECISIONS.md`.

---

## S3 — Character continuity and image generation

**Scope:** The largest stage. Build the machinery that keeps characters consistent and
actually generates images.

**Work:**
- Character bible and continuity ledger.
- Scene breakdown.
- Prompt composer.
- Image generation through the chosen provider.
- Image QC.

**Gate:** A full episode's scenes generated with recognisable, consistent characters across
all six scenes, passing QC — including Ada's scar on the correct side (DEC-005).

---

## S4 — M4 / M5 / M6 fixes

**Scope:** Clear the assembly, subtitle and output defects.

**Work:**
- M4: honour the 0.2 s inter-scene gap; fix `-shortest` clipping; time the fade-out from
  video length; scale/crop to 9:16 before `zoompan`; fail loudly on missing scene images;
  two-pass `loudnorm`; 48 kHz output (B-06, B-07).
- M5: make the font fallback real or remove it; review subtitle size (B-08).
- M6: replace the service-account Drive path with something that works on personal Drive
  (B-09).

**Gate:** A complete video assembles with no drift, no clipping, correct audio format, and
subtitles that are legible and in sync.

---

## S5 — Verify Episodes 1-3 end to end

**Scope:** Prove the pipeline works more than once.

**Work:**
- Run Episodes 1, 2 and 3 through the full pipeline with no manual intervention beyond
  what the pipeline itself requires.
- Record outputs, timings and any deviations.

**Gate:** Three consecutive episodes produced end to end, each 60-90 seconds, each passing
the same checks as S4's gate.

---

## S6 — Remaining items

**Scope:** Whatever is still open after S5 — deferred bugs, polish, and anything the
end-to-end runs surfaced.

**Work:**
- Triage the remaining bug register entries.
- Address the `*.png` ignore rule conflict for character reference images (B-10).
- Add the missing test coverage (B-10).

**Gate:** Bug register has no Open entries that are in scope, and the repo has tests that
run.

---

## Parked

Phase 2 and hosting are parked. Not scheduled, not estimated. See DEC-001.
