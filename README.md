# Ashes of the Chosen

A Python pipeline that turns an episode number into a finished 9:16 vertical
short-drama video (60–90 s) for TikTok, Facebook and YouTube. It writes a
script, voices it in Nigerian English, builds image prompts, generates scene
images (Cloudflare Workers AI or an offline fake), assembles the scenes with
FFmpeg, burns word-synced subtitles, and files the result into per-platform
upload folders.

Repo: `github.com/Egbo007/Ghost-vid-phase1` — flat folder, no packages, no
`src/` layout. Windows, Python 3.11.9, FFmpeg with libass, ffprobe.

**Status: pre-production.** Phase A (script, voice, assembly, captions,
packaging) is coded and tested. Phase B adds script-driven image prompts, batch
generation, review/approval and an offline fake provider. Nothing is marked
verified until a real full episode has been run end to end.

## Pipeline

```
episode number
  -> generate_script.py   (M1, Gemini)      episode_NN_script.txt
  -> generate_voice.py    (M2, Edge-TTS)    voice.mp3 + scene durations + word timestamps
  -> generate_prompts.py  (M3)              episode_NN_image_prompts.json (7 images)
  -> generate_images.py   (provider)        scene_01a/b.png, scene_02..06.png
  -> review_images.py     (checks + sheet)  episode_NN_image_review.json (approval)
  -> assemble_video.py    (M4, FFmpeg)      episode_NN_raw.mp4
  -> burn_subtitles.py    (M5, libass)      episode_NN_final.mp4
  -> organise_output.py   (M6)              upload-ready/{tiktok,facebook,youtube}/
```

A `check_episode.py --stage <voice|images|raw|final|package>` gate sits after
each stage.

## Folder map

```
generate_script.py / generate_voice.py / generate_prompts.py
generate_images.py / image_providers.py / try_cloudflare_image.py
review_images.py / check_episode.py
assemble_video.py / burn_subtitles.py / organise_output.py
series_config.json / mood_templates.json / character_cards.json / negative_prompts.json
requirements.txt / .env.example / README.md
docs/            RUNBOOK.md, PROJECT_LOG.md, ROADMAP.md, DECISIONS.md, AGENT_RULES.md
assets/characters/ada/   ref_1..ref_6.jpg (identity references)
ashes-of-the-chosen/     working output, gitignored (episode-NN folders)
```

Naming is load-bearing: folders `episode-NN` (hyphen), files `episode_NN_*`
(underscore). Scene 1 has two images (`scene_01a.png`, `scene_01b.png`);
scenes 2–6 have one each. Do not change these without updating every module.

## Setup

```cmd
python -m pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env`. Never commit it or paste its values anywhere. FFmpeg and
ffprobe must be on PATH, with libass enabled.

## Running an episode

The exact commands, in order, with "what good looks like" and failure recovery
are in **[docs/RUNBOOK.md](docs/RUNBOOK.md)**. Short version:

```cmd
python generate_script.py --episode N
python generate_voice.py --episode N
python check_episode.py --episode N --stage voice
python generate_prompts.py --episode N
python generate_images.py --episode N --provider cloudflare --dry-run
python generate_images.py --episode N --provider cloudflare
python review_images.py --episode N --open
python review_images.py --episode N --approve
python check_episode.py --episode N --stage images
python assemble_video.py --episode N --force
python check_episode.py --episode N --stage raw
python burn_subtitles.py --episode N --force
python check_episode.py --episode N --stage final
python organise_output.py --episode N --skip-drive
python check_episode.py --episode N --stage package
```

Use `--provider fake` instead of `cloudflare` to exercise the whole chain
offline for free. See the RUNBOOK for approval, regeneration and quota handling.

## Security

`.env` is never committed (ignored by `.env` and `.env.*`); `.env.example`
holds placeholders only. Never paste a real key into a tracked file, doc,
commit message or log. If a key leaks, rotate it — removing it in a later
commit does not remove it from history.
