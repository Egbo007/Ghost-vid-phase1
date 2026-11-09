# Ashes of the Chosen

A Python pipeline that turns an episode number into a finished 9:16 vertical short-drama
video, 60-90 seconds long, for TikTok, Facebook and YouTube.

Give it an episode number. It writes a script, voices it in Nigerian English, builds image
prompts, assembles the scenes with FFmpeg, burns word-synced subtitles, and files the
result into per-platform upload folders.

Repo: `github.com/Egbo007/Ghost-vid-phase1` — flat folder, no packages, no `src/` layout.

**Status: pre-production.** All six modules compile. Only two of them have ever been run,
and only as single-scene smoke tests. See the module table below before you assume anything
works end to end.

---

## Pipeline

```
  episode number
        |
        v
  +-------------------+      series_config.json
  | M1 generate_script| <--- (bible, arc map, tone)
  |    Gemini LLM     |
  +---------+---------+
            |  episode_XX_script.txt
            v
  +-------------------+      .env (EPISODE_OUTPUT_DIR)
  | M2 generate_voice | ---> Edge-TTS, 6 scene mp3s
  |    Edge-TTS       |      + 0.2 s silence between scenes
  +---------+---------+
            |  episode_XX_voice.mp3
            |  episode_XX_scene_durations.json
            |  episode_XX_timestamps.json
            +---------------------------+
            |                           |
            v                           v
  +-------------------+      +----------------------+
  | M3 generate_prompts|     | M4 assemble_video    |
  |    (hardcoded)    |      |    FFmpeg + Ken Burns|
  +---------+---------+      +----------+-----------+
            |  episode_XX_prompts.txt    |  episode_XX_raw.mp4
            v                           v
     [ image generation ]  ------> +----------------------+
     (manual / external,            | M5 burn_subtitles    |
      not yet automated)            |    FFmpeg libass     |
                                    +----------+-----------+
                                               |  episode_XX_final.mp4
                                               v
                                    +----------------------+
                                    | M6 organise_output   |
                                    |  per-platform folders|
                                    +----------+-----------+
                                               |
                                               v
                                    upload-ready/{tiktok,facebook,youtube}/
```

Note the gap: nothing in this repo generates images. M3 writes prompts and the pipeline
expects `scene_XX.png` files to already be sitting in the episode folder. That handoff is
one of the things Phase 2 was going to solve.

---

## Module status

| Module | File | Compiles | Ever run? | Notes |
|---|---|---|---|---|
| M1 | `generate_script.py` | Yes | **No** — never run for real | Gemini call path unexercised. Word-count range is logged, not enforced. |
| M2 | `generate_voice.py` | Yes | **Smoke test only** — single scene | Produces audio and a durations JSON. Word timestamps come out empty (B-01). |
| M3 | `generate_prompts.py` | Yes | **No** — never run for real | Hardcoded post-apocalyptic dictionary. Wrong story entirely (B-05). |
| M4 | `assemble_video.py` | Yes | **Smoke test only** — single scene | Stitches audio and an image into a raw mp4. Multi-scene timing untested (B-06). |
| M5 | `burn_subtitles.py` | Yes | **No** — never run for real | Depends on word timestamps that M2 does not currently produce. |
| M6 | `organise_output.py` | Yes | **No** — never run for real | Drive upload path is unproven (B-09). |

"Compiles" means `python -m py_compile` is clean. That is the whole claim. No module has
been validated against a real 6-scene episode, and no full episode has been produced.

---

## Prerequisites

- **Windows** with **Command Prompt (cmd)**. The commands below are cmd syntax.
- **Python 3.11.9** on PATH (`python --version`).
- **FFmpeg** on PATH, built with **libass** (needed by M4 and M5). Check with
  `ffmpeg -version` and confirm `--enable-libass` appears in the configuration line.
- **ffprobe** on PATH — ships with FFmpeg, used by M2 for exact audio durations.
- A **Gemini API key** for M1. Everything else in the current pipeline is free.

---

## Setup

From the repo root, in cmd:

```cmd
python -m pip install -r requirements.txt
copy .env.example .env
```

Then open `.env` in an editor and fill in `GEMINI_API_KEY`. Do not commit it.

`requirements.txt` pins minimum versions only. `edge-tts>=6.1.9` will currently resolve to
7.3.1, which changes the default boundary behaviour M2 relies on — that is bug B-01, not a
setup problem, and the code has not been updated for it yet.

---

## Running an episode

Run from the repo root. `EPISODE_OUTPUT_DIR` in `.env` controls where episode folders land
(default `./ashes-of-the-chosen`).

```cmd
python generate_script.py --episode 1
python generate_voice.py --episode 1
python generate_prompts.py --episode 1
```

Then place the scene images in the episode folder by hand:

```cmd
dir ashes-of-the-chosen\episode-01
```

Expected image names are `scene_01.png` through `scene_06.png`. Nothing generates these yet.

Then finish the video:

```cmd
python assemble_video.py --episode 1
python burn_subtitles.py --episode 1
python organise_output.py --episode 1
```

Useful flags:

- `--force` on M1, M2, M4, M5, M6 — regenerate even if the output already exists.
- `--output-dir <path>` on M1, M2, M4, M5, M6 — override `EPISODE_OUTPUT_DIR`.
- `--skip-drive` on M6 — skip the Google Drive sync.
- `--model <name>` on M1 — override the Gemini model (default `gemini-2.5-flash`).
- `--music <path>` on M4 — override the background music track.

M3 accepts `--episode` only. It has no `--force` and no `-e` short form.

Known-broken steps are logged in `docs/PROJECT_LOG.md`. Expect M2 to emit an empty
timestamps file and M5 to have nothing to sync to. That is the current baseline, not a
mistake you made.

---

## Folder layout

```
ashes_of_the_chosen/                <- repo root
├── generate_script.py              M1  Gemini script generator
├── generate_voice.py               M2  Edge-TTS voiceover + timestamps
├── generate_prompts.py             M3  image prompt generator (hardcoded, wrong story)
├── assemble_video.py               M4  FFmpeg scene assembly
├── burn_subtitles.py               M5  FFmpeg libass subtitle burn
├── organise_output.py              M6  per-platform folders + Drive sync
├── series_config.json              series bible, arc map, tone instructions
├── mood_templates.json             Ken Burns motion + mood presets for M4
├── character_cards.json            character reference (read by no code yet)
├── negative_prompts.json           negative prompt list (read by no code yet)
├── requirements.txt
├── .env.example                    template — copy to .env
├── .gitignore
├── README.md
├── docs/
│   ├── PROJECT_LOG.md              stage, task log, bug register
│   ├── ROADMAP.md                  stages S0-S6 and their gates
│   ├── DECISIONS.md                decision log
│   └── AGENT_RULES.md              standing rules for coding agents
└── ashes-of-the-chosen/            working output, gitignored
    └── episode-01/
        ├── episode_01_script.txt
        ├── episode_01_voice.mp3
        ├── episode_01_scene_durations.json
        ├── episode_01_timestamps.json
        ├── episode_01_prompts.txt
        ├── scene_01_audio.mp3 ...
        ├── scene_01.png ...         (placed by hand; not generated)
        ├── episode_01_raw.mp4
        └── episode_01_final.mp4
```

Naming is load-bearing: folders are `episode-01` (hyphen), files are `episode_01_*`
(underscore). Do not change either without changing every module that reads them.

---

## Security

- **`.env` is never committed.** It is ignored by `.env` and `.env.*` in `.gitignore`.
- `.env.example` is the only env file tracked, and it holds placeholders only. It is
  un-ignored by an explicit `!.env.example` line.
- Never paste a real key into a tracked file, a doc, a commit message, or a log.
- `credentials.json`, `service_account*.json` and `*_credentials.json` are ignored — keep
  Google credentials out of the repo.
- If a key does leak into a commit, rotate it. Removing the file in a later commit does not
  remove it from history.

---

## Phase 2 and hosting are parked

Phase 2 (image generation, character continuity, automated QC) and any hosting or
deployment work are **parked**. They are not in scope, not scheduled, and not being
estimated. See `docs/DECISIONS.md` DEC-001.

The current focus is making the existing pipeline honest about what it does and getting one
real episode through it. See `docs/ROADMAP.md`.
