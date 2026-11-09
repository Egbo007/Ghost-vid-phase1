# AGENT RULES — Ashes of the Chosen

Standing rules for any coding agent working in this repo. These apply to every task unless a
brief explicitly overrides a specific rule — and even then, the security rules do not bend.

---

## Hard rules

1. **Never open, print, copy or summarise the contents of `.env`.** It holds real keys. You
   do not need to read it to do your job. If a task seems to require it, stop and ask.

2. **Never write a real key anywhere.** Not in code, not in docs, not in a test fixture, not
   in a commit message, not in a log line, not in an example. Placeholders only, in the form
   `your_..._here`.

3. **Do not modify `.py`, `.json` or `requirements.txt` unless the brief says to.** This
   repo is mid-recovery; unrequested edits to working files destroy the ability to tell what
   changed and why.

4. **If anything is unclear, ask before acting.** A wrong guess here costs more than a
   question. This is not a formality — it is the rule that keeps the rest of them honest.

---

## Working rules

5. **Work on a branch per task.** Never commit task work directly to `main`. The branch name
   should say what the task is (`stage0-hygiene`, not `fixes`).

6. **Show a diff summary and test output.** Every task ends with what changed and what you
   ran to prove it. "Done" without evidence is not done.

7. **Do not refactor beyond the brief.** If you spot something ugly outside your scope, note
   it in the bug register or mention it — do not fix it in passing. Unrequested refactors
   hide real changes in noise.

8. **Do not change CLI flags or folder naming.** The flags (`--episode`, `--output-dir`,
   `--force`, `--music`, `--skip-drive`, `--model`, `--cleanup`, `--upload-dir`,
   `--mood-templates`) and the naming scheme are load-bearing:
   - folders: `episode-01` (hyphen)
   - files: `episode_01_*` (underscore)

   Changing either breaks every module that reads them, and breaks any documented command.

---

## Verification expectations

9. **Run what you claim.** If you say the modules compile, run `py_compile`. If you say a
   file is ignored, run `git check-ignore`. Do not describe results you have not seen.

10. **Report blockers plainly.** If a tool is unavailable, a step cannot run, or a gate
    cannot be checked, say so and name what is missing. Do not substitute a plausible
    narrative for a missing result.

11. **Never fabricate.** No invented file names, line numbers, commit hashes, test output or
    bug IDs. If it is not in the repo or in a command result, it does not exist.

---

## Scope discipline

12. **One stage at a time.** See `docs/ROADMAP.md`. Do not start the next stage's work
    because it is nearby and easy.

13. **Do not push or merge unless the brief says so.** Commit locally, report the hash, stop.

14. **Parked means parked.** Phase 2 and hosting are out of scope (DEC-001). Do not
    "prepare for" them, scaffold for them, or leave hooks for them.
