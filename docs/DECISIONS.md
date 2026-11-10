# DECISIONS — Ashes of the Chosen

Decision log. Each entry records what was decided, why, and what it rules out. Decisions are
not silently reversed — supersede them with a new entry.

---

## DEC-001 — Phase 2 and hosting are parked

**Status:** Active

Phase 2 and any hosting or deployment work are parked. Not in scope, not scheduled, not
estimated.

**Why:** The current pipeline has never produced a real episode end to end. Adding scope on
top of an unproven base compounds risk without evidence.

**Rules out:** Hosting discussions, deployment work, and Phase 2 features until the S0-S6
roadmap is worked through.

---

## DEC-002 — Windows, local, free-first

**Status:** Active

Development and operation target Windows, run locally, with a free-first budget. Command
Prompt (cmd) is the reference shell for documented commands. Python 3.11.9, FFmpeg on PATH.

**Why:** That is the actual environment. Docs that assume bash or a hosted runner are wrong
for this project.

**Rules out:** Cloud-first architecture, Linux-only tooling, and paid services where a free
tier will do.

---

## DEC-003 — Image provider: bake-off, not a guess

**Status:** Active

- **Primary candidate:** Cloudflare FLUX.2 klein 4B
- **Alternative:** Meta Muse Image
- **Benchmark:** Google Flow

The final choice is made by a **blind bake-off** in S2, not by preference or convenience.

**Why:** Character consistency is the hard requirement for this series, and provider
marketing does not answer that question. Blind comparison on identical inputs does.

**Rules out:** Committing to a provider before the S2 gate, and switching providers
mid-episode.

---

## DEC-004 — Provider-swappable architecture

**Status:** Active

Image generation sits behind a provider interface. The pipeline calls the interface, not a
specific vendor's API.

**Why:** DEC-003 defers the provider choice. If the choice is deferred, the code must not
bake it in — otherwise the bake-off winner cannot actually be adopted without a rewrite.

**Rules out:** Vendor SDK calls scattered through pipeline modules.

---

## DEC-005 — Ada's scar: above her own left eyebrow

**Status:** Active

Ada's scar is above **her own left eyebrow**. When she faces camera, that appears on the
**viewer's right**.

**Why:** Character continuity is the thing viewers notice first when it breaks, and
left/right is exactly where image models drift. Stating it in viewer terms removes the
ambiguity at the point of use.

**Rules out:** Any prompt or reference image that places the scar on the viewer's left.

---

## DEC-006 — New modules arrive stage by stage

**Status:** Active

The following are introduced incrementally, each in its own stage, not as one big drop:

- `generate_scenes.py`
- `generate_images.py`
- `review_images.py`
- character bible
- continuity ledger
- provider interface
- a rewritten `generate_prompts.py`

**Why:** A large simultaneous introduction is unreviewable and untestable. Stage-by-stage
keeps each change small enough to verify against a gate.

**Rules out:** A single "Phase 2" branch that lands everything at once.

---

## DEC-007 — Continuity: plan the big things, flag the small ones

**Status:** Active

Major continuity events are planned up front. Minor ones are flagged for review rather than
enforced automatically.

**Why:** Enforcing every minor detail mechanically produces false failures and slows
production. Planning the major beats and reviewing the rest matches how the work actually
gets done.

**Rules out:** A fully automated continuity enforcer as a first implementation.

---

## DEC-008 — One stage at a time, with acceptance tests

**Status:** Active

Work proceeds one stage at a time. Each stage has a written gate that must pass before the
next stage opens. See `docs/ROADMAP.md`.

**Why:** The pipeline is a chain — a defect in M2 surfaces as a mystery in M5. Isolating
stages is what makes failures attributable.

**Rules out:** Parallel stage work and "we'll fix it in the next stage" carry-over.

---

## DEC-009 — No dependency changes without compatibility review

**Status:** Active

No dependency is added, removed or version-bumped without a review of what it does to the
existing modules.

**Why:** B-01 is exactly this failure. `edge-tts>=6.1.9` resolved to 7.3.1, whose default
boundary behaviour silently broke word timestamps. An unpinned minimum version was enough
to break a downstream module.

**Rules out:** Casual `pip install` additions and unpinned minimum-version ranges without a
check.

---

## DEC-010 — Meta Muse integration and spending are on hold

**Status:** Active

Meta Muse integration and any spending on it are on hold until the image-quality
optimisation stage.

**Why:** Muse is the alternative candidate in DEC-003, and choosing it before the bake-off
would pre-empt the comparison. Integration work on a provider that may lose the bake-off is
wasted effort.

**Rules out:** Muse API work, Muse spend, and Muse-specific code before the image-quality
optimisation stage.

---

## DEC-011 — Work Phases A-E and the Definition-of-Done ladder

**Status:** Active

Work is organised into five phases:

- **A — Core repair.** Fix what is broken in the existing pipeline.
- **B — Image provider.** Select and integrate the provider.
- **C — Character and scene intelligence.** Bible, ledger, scene breakdown, prompt composer.
- **D — Image generation and QC.** Generate and review images.
- **E — Integration and production readiness.** End-to-end runs.

Completion is tracked on a four-rung ladder:

- **D1 — Written.** The code or document exists.
- **D2 — Tested.** Automated tests pass.
- **D3 — User-verified.** The user has confirmed it against real output.
- **D4 — Production-ready.** It has run in a real episode without intervention.

**A brief closes at D2. A phase gate requires D3.** Nothing is called "verified" by an
agent — only "coded and tested".

**Why:** The distinction between "I wrote it" and "it works on real data" is exactly where
this project has been losing time. Naming the rungs stops the two from being conflated.

**Rules out:** Closing a brief on code alone, and opening a phase gate on unit tests alone.

---

## DEC-012 — Flow reference images live under `assets\characters\<name>\`

**Status:** Active

Reference images for Google Flow live under `assets\characters\<name>\` and stay untracked
until Phase C.

**Why:** They are large binaries tied to one provider's workflow, and Phase C is where the
character bible decides their final structure. Tracking them now would lock in a layout
before the design exists.

**Rules out:** Committing reference images before Phase C, and scattering them outside the
`assets\characters\` tree.
