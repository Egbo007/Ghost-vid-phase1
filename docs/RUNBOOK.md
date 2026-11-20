# Ashes of the Chosen — Runbook

Exact commands for any episode `N`. Run from the repo root in Command Prompt.
Each step says what good looks like and the first thing to try when it fails.

The image step is optional and offline-testable. Use `--provider fake` to
exercise the whole pipeline for free; use `--provider cloudflare` for real
images (spends the free daily allowance).

---

## 0. Setup (once)

```cmd
python -m pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env` in an editor. Never commit it, paste it, or print its values.
FFmpeg and ffprobe must be on PATH; FFmpeg needs `--enable-libass`.

Good: `python --version` shows 3.11.x; `ffmpeg -version` shows libass.
If it fails: re-run the installer and check PATH, then reopen the terminal.

---

## 1. Script (M1)

```cmd
python generate_script.py --episode N
```

Good: `episode_NN_script.txt` exists with a `TITLE:` line and `[SCENE ...]` headers.
If it fails: check `GEMINI_API_KEY` in `.env`; do not paste the key anywhere.

## 2. Voice (M2)

```cmd
python generate_voice.py --episode N
python check_episode.py --episode N --stage voice
```

Good: the check ends `0 fail`; word count equals timestamp count.
If it fails: re-run with `--force`; check `episode_NN_timestamps.json` is not empty.

## 3. Prompts (M3)

```cmd
python generate_prompts.py --episode N
```

Good: "Wrote 7 image prompts to ...episode_NN_image_prompts.json".
If it fails: confirm the script has `[SCENE ...]` headers and a blank line after
the last scene header.

## 4. Image dry run (free)

```cmd
python generate_images.py --episode N --provider cloudflare --dry-run
```

Good: lists 7 labels with `refs=` counts and no network request.
If it fails: confirm the prompts JSON exists (step 3).

## 5. Real images (spends quota)

```cmd
python generate_images.py --episode N --provider cloudflare
```

Good: summary table shows `rendered: 7`, `failed: 0`.
If it fails: check `ashes-of-the-chosen/phase-b-tests/usage_log.jsonl` for today's
request count; on HTTP 429 the command stops cleanly and you resume by running
the same command later. Files already saved are skipped, not overwritten.

Offline alternative (no quota):

```cmd
python generate_images.py --episode N --provider fake --force
```

## 6. Review images

```cmd
python review_images.py --episode N --open
```

Good: a table of 7 labels, a contact sheet `episode_NN_contact_sheet.jpg`, and
flags of `-` for clean images. Flags are advisory; they never block approval.
If it fails: confirm images exist with `dir ashes-of-the-chosen\episode-NN`.

## 7. Approve or regenerate

```cmd
python review_images.py --episode N --approve
python review_images.py --episode N --approve 1a 3
python review_images.py --episode N --regenerate 2 4
python check_episode.py --episode N --stage images
```

Good: the check ends `0 fail`; every image approved.
If it fails: regenerate flagged labels, then approve again. Regeneration clears
that label's approval so you must re-approve it.

## 8. Assemble (M4)

```cmd
python assemble_video.py --episode N --force
python check_episode.py --episode N --stage raw
```

Good: `episode_NN_raw.mp4` exists; the check ends `0 fail`.
If it fails: confirm images are named `scene_01a.png`, `scene_01b.png`,
`scene_02.png` ... `scene_06.png` in the episode folder.

## 9. Captions (M5)

```cmd
python burn_subtitles.py --episode N --force
python check_episode.py --episode N --stage final
```

Good: `episode_NN_final.mp4` exists; the check ends `0 fail`.
If it fails: confirm `episode_NN_timestamps.json` is populated (step 2).

## 10. Package (M6)

```cmd
python organise_output.py --episode N --skip-drive
python check_episode.py --episode N --stage package
```

Good: upload folders under `upload-ready/`; the check ends `0 fail`.
If it fails: check the upload directory path; `--skip-drive` avoids needing
Google credentials.

---

## Windows traps

- **Hidden double extensions.** Windows may hide `.jpg` in `photo.jpg.jpg`.
  Turn on "File name extensions" in Explorer, or use `dir /b` in cmd.
- **Git pager.** Use `git --no-pager log` and `git --no-pager diff` so output
  does not open a pager and hang.
- **UTF-8 BOM.** Notepad / `Out-File -Encoding utf8` write a byte-order mark.
  Prompt files are read with `utf-8-sig`, so a BOM is stripped, but prefer
  "Save as UTF-8 without BOM" in an editor.
- **Never paste secrets.** `.env`, the Cloudflare account ID and token stay out
  of chat, docs, commits and logs. Scripts read them from the environment.
- **Cloudflare daily allowance.** Check
  `ashes-of-the-chosen/phase-b-tests/usage_log.jsonl` before a large batch.
  `generate_images.py` warns above 60 requests in one day. HTTP 429 stops the
  run cleanly; resume with the same command later.
