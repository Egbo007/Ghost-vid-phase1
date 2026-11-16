#!/usr/bin/env python3
"""
Episode checker — Ashes of the Chosen

Read-only verification of one episode at a chosen pipeline stage. Prints PASS /
FAIL / WARN lines with the measured numbers and exits 1 if anything FAILs.

Stages:
  voice   — the two M2 JSONs agree with each other and with the spoken text
  raw     — the M4 video is the right format, length and loudness
  final   — the M5 video matches the raw one and its subtitles are sane
  package — the M6 upload copies exist and match the final video
"""

import os
import re
import sys
import json
import logging
import argparse
import subprocess
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("check_episode")

DEFAULT_OUTPUT_DIR = "./ashes-of-the-chosen"
DEFAULT_UPLOAD_DIR = "./upload-ready"

# Tolerances, in seconds unless noted.
# The joined audio must equal sum(durations) + buffers: both sides are measured
# by M2, so this is tight and symmetric.
TIMELINE_TOLERANCE = 0.05
DURATION_TOLERANCE = 0.10
# A word may sit this far outside its scene's window before it counts as misplaced.
SCENE_WINDOW_SLACK = 0.10
FIRST_WORD_MAX_START = 0.5
LAST_WORD_SLACK = 0.2
LUFS_MIN = -15.0
LUFS_MAX = -13.0
TRUE_PEAK_MAX = -1.0

EXPECTED_WIDTH = 1080
EXPECTED_HEIGHT = 1920
EXPECTED_FPS = 25
EXPECTED_SAMPLE_RATE = 48000

PLATFORMS = ["tiktok", "facebook", "youtube"]


class Report:
    """Collects PASS/FAIL/WARN lines and decides the exit code."""

    def __init__(self):
        self.lines = []
        self.failures = 0
        self.warnings = 0

    def _add(self, status, message):
        self.lines.append(f"{status}: {message}")
        print(f"{status}: {message}")

    def passed(self, message):
        self._add("PASS", message)

    def failed(self, message):
        self.failures += 1
        self._add("FAIL", message)

    def warned(self, message):
        self.warnings += 1
        self._add("WARN", message)

    def check(self, condition, ok_message, fail_message):
        if condition:
            self.passed(ok_message)
        else:
            self.failed(fail_message)
        return condition

    def summary(self):
        print(
            f"\n{len(self.lines)} check(s): "
            f"{len(self.lines) - self.failures - self.warnings} pass, "
            f"{self.warnings} warn, {self.failures} fail"
        )
        return 1 if self.failures else 0


# ---------------------------------------------------------------- ffprobe helpers

def probe_json(path, entries, stream_selector=None):
    """Runs ffprobe and returns parsed JSON, or None on failure."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_entries", entries]
    if stream_selector:
        cmd += ["-select_streams", stream_selector]
    cmd.append(str(path))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, errors="replace")
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def probe_duration(path):
    data = probe_json(path, "format=duration")
    if not data:
        return None
    try:
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


def probe_video_stream(path):
    data = probe_json(path, "stream=codec_name,width,height,pix_fmt,r_frame_rate,avg_frame_rate",
                      stream_selector="v:0")
    if not data or not data.get("streams"):
        return None
    return data["streams"][0]


def probe_audio_stream(path):
    data = probe_json(path, "stream=codec_name,sample_rate,channels", stream_selector="a:0")
    if not data or not data.get("streams"):
        return None
    return data["streams"][0]


def parse_fraction(text):
    """Parses ffprobe's '25/1' into 25.0; returns None if it is not a fraction."""
    if not text or "/" not in str(text):
        return None
    num, _, den = str(text).partition("/")
    try:
        num_f, den_f = float(num), float(den)
    except ValueError:
        return None
    if den_f == 0:
        return None
    return num_f / den_f


def measure_loudness(path):
    """
    Runs ffmpeg's ebur128 filter over the file and parses the summary.
    Returns {"integrated": float, "true_peak": float} or None if unparseable.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
           "-filter_complex", "ebur128=peak=true", "-f", "null", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, errors="replace")
    if proc.returncode != 0:
        return None
    stderr = proc.stderr or ""

    # ebur128 prints a final "Summary:" block; read only that section.
    summary_idx = stderr.rfind("Summary:")
    section = stderr[summary_idx:] if summary_idx != -1 else stderr

    integrated = None
    true_peak = None
    for line in section.splitlines():
        stripped = line.strip()
        match_i = re.match(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", stripped)
        if match_i and integrated is None:
            integrated = float(match_i.group(1))
        match_p = re.match(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", stripped)
        if match_p and true_peak is None:
            true_peak = float(match_p.group(1))

    if integrated is None or true_peak is None:
        return None
    return {"integrated": integrated, "true_peak": true_peak}


# ---------------------------------------------------------------- data loaders

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_spoken_word_total(path):
    """Reads the 'TOTAL SPOKEN WORDS: N' line from episode_XX_spoken.txt."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.search(r"TOTAL SPOKEN WORDS:\s*(\d+)", line, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def parse_ass_events(ass_text):
    """
    Returns a list of {"start", "end"} in seconds for every Dialogue line.
    ASS timestamps look like H:MM:SS.cc
    """
    events = []
    for line in ass_text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        try:
            events.append({
                "start": ass_time_to_seconds(parts[1].strip()),
                "end": ass_time_to_seconds(parts[2].strip()),
            })
        except ValueError:
            continue
    return events


def ass_time_to_seconds(text):
    match = re.match(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$", text)
    if not match:
        raise ValueError(f"Bad ASS timestamp: {text!r}")
    hours, minutes, seconds, centis = (int(g) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds + centis / 100.0


# ---------------------------------------------------------------- stage checks

def check_voice(report, ep_dir, ep_str):
    durations_path = ep_dir / f"episode_{ep_str}_scene_durations.json"
    timestamps_path = ep_dir / f"episode_{ep_str}_timestamps.json"
    spoken_path = ep_dir / f"episode_{ep_str}_spoken.txt"

    if not report.check(durations_path.exists(), f"{durations_path.name} present",
                        f"{durations_path.name} missing"):
        return
    if not report.check(timestamps_path.exists(), f"{timestamps_path.name} present",
                        f"{timestamps_path.name} missing"):
        return

    durations = load_json(durations_path)
    timestamps = load_json(timestamps_path)

    scenes = durations.get("scenes", [])
    buffer_seconds = float(durations.get("buffer_seconds_between_scenes", 0.2))
    total_duration = float(durations.get("total_duration_seconds", 0.0))
    words = timestamps.get("words", [])

    if not report.check(bool(scenes), f"{len(scenes)} scene(s) listed",
                        "no scenes listed in durations JSON"):
        return
    if not report.check(bool(words), f"{len(words)} word timestamp(s)",
                        "no word timestamps found"):
        return

    # Timeline: scene i starts at the sum of earlier (duration + buffer), so the
    # joined audio must be sum(durations) + buffers. Both sides of this are now
    # measured values (M2 measures the silence it created), so the tolerance is
    # tight and symmetric. Brief 004 allowed an asymmetric excess here and blamed
    # it on per-scene MP3 padding; that was wrong — the excess came from the
    # concat step, and allowing it hid a real ~0.3s drift.
    expected_total = sum(float(s["duration_seconds"]) for s in scenes) + buffer_seconds * (len(scenes) - 1)
    delta = total_duration - expected_total
    report.check(
        abs(delta) <= TIMELINE_TOLERANCE,
        f"total_duration {total_duration:.3f}s vs scene sum {expected_total:.3f}s "
        f"(delta {delta:+.3f}s, tolerance +/-{TIMELINE_TOLERANCE}s)",
        f"total_duration {total_duration:.3f}s vs scene sum {expected_total:.3f}s "
        f"(delta {delta:+.3f}s, exceeds +/-{TIMELINE_TOLERANCE}s)"
    )

    # Ground truth: the joined audio file itself must match total_duration_seconds.
    voice_path = ep_dir / f"episode_{ep_str}_voice.mp3"
    if voice_path.exists():
        voice_duration = probe_duration(voice_path)
        if voice_duration is None:
            report.failed(f"could not read duration of {voice_path.name}")
        else:
            voice_delta = abs(total_duration - voice_duration)
            report.check(
                voice_delta <= DURATION_TOLERANCE,
                f"total_duration {total_duration:.3f}s matches {voice_path.name} "
                f"{voice_duration:.3f}s (delta {voice_delta:.3f}s)",
                f"total_duration {total_duration:.3f}s vs {voice_path.name} "
                f"{voice_duration:.3f}s (delta {voice_delta:.3f}s > {DURATION_TOLERANCE}s)"
            )
    else:
        report.warned(f"{voice_path.name} missing, cannot verify total_duration against the audio")

    first_start = float(words[0]["start"])
    report.check(
        first_start < FIRST_WORD_MAX_START,
        f"first word starts at {first_start:.3f}s (< {FIRST_WORD_MAX_START}s)",
        f"first word starts at {first_start:.3f}s (>= {FIRST_WORD_MAX_START}s)"
    )

    starts = [float(w["start"]) for w in words]
    bad_pairs = [
        (i, starts[i], starts[i + 1])
        for i in range(len(starts) - 1)
        if not starts[i] < starts[i + 1]
    ]
    report.check(
        not bad_pairs,
        f"word starts strictly increasing across {len(starts)} words",
        f"word starts not strictly increasing at {len(bad_pairs)} position(s), "
        f"first at index {bad_pairs[0][0]} ({bad_pairs[0][1]:.3f}s -> {bad_pairs[0][2]:.3f}s)"
        if bad_pairs else "word starts not strictly increasing"
    )

    last_end = float(words[-1]["end"])
    report.check(
        last_end <= total_duration + LAST_WORD_SLACK,
        f"last word ends at {last_end:.3f}s (<= {total_duration + LAST_WORD_SLACK:.3f}s)",
        f"last word ends at {last_end:.3f}s (> {total_duration + LAST_WORD_SLACK:.3f}s)"
    )

    # Every word must fall inside its own scene's window on the joined timeline.
    # This is what catches a timing drift that the totals alone would hide: the
    # joined file can be the right length while scene 6's words sit 0.3s early.
    words_by_scene = {}
    for w in words:
        words_by_scene.setdefault(w.get("scene"), []).append(w)

    scene_start = 0.0
    for sc in scenes:
        sc_num = sc["scene"]
        sc_dur = float(sc["duration_seconds"])
        sc_words = words_by_scene.get(sc_num, [])
        window_start = scene_start - SCENE_WINDOW_SLACK
        window_end = scene_start + sc_dur + SCENE_WINDOW_SLACK

        if not sc_words:
            report.warned(f"scene {sc_num}: no words timestamped")
        else:
            first = float(min(w["start"] for w in sc_words))
            last = float(max(w["end"] for w in sc_words))
            report.check(
                first >= window_start and last <= window_end,
                f"scene {sc_num}: words span {first:.3f}-{last:.3f}s, "
                f"inside window {scene_start:.3f}-{scene_start + sc_dur:.3f}s",
                f"scene {sc_num}: words span {first:.3f}-{last:.3f}s, outside window "
                f"{window_start:.3f}-{window_end:.3f}s (scene starts at {scene_start:.3f}s)"
            )

        scene_start += sc_dur + buffer_seconds

    spoken_total = read_spoken_word_total(spoken_path)
    if spoken_total is None:
        report.failed(f"could not read 'TOTAL SPOKEN WORDS' from {spoken_path.name}")
    else:
        word_count = int(timestamps.get("total_words", len(words)))
        report.check(
            word_count == spoken_total,
            f"timestamped words {word_count} equals spoken words {spoken_total}",
            f"timestamped words {word_count} != spoken words {spoken_total}"
        )


def check_video_format(report, path, label):
    """Shared format checks for raw and final videos."""
    stream = probe_video_stream(path)
    if stream is None:
        report.failed(f"{label}: could not read video stream")
        return None

    report.check(stream.get("codec_name") == "h264",
                 f"{label}: video codec h264",
                 f"{label}: video codec is {stream.get('codec_name')}, expected h264")
    report.check(
        stream.get("width") == EXPECTED_WIDTH and stream.get("height") == EXPECTED_HEIGHT,
        f"{label}: resolution {stream.get('width')}x{stream.get('height')}",
        f"{label}: resolution {stream.get('width')}x{stream.get('height')}, "
        f"expected {EXPECTED_WIDTH}x{EXPECTED_HEIGHT}"
    )
    fps = parse_fraction(stream.get("avg_frame_rate")) or parse_fraction(stream.get("r_frame_rate"))
    report.check(
        fps is not None and abs(fps - EXPECTED_FPS) < 0.01,
        f"{label}: {fps:.2f} fps" if fps else f"{label}: frame rate readable",
        f"{label}: frame rate is {stream.get('avg_frame_rate')}, expected {EXPECTED_FPS}"
    )
    report.check(stream.get("pix_fmt") == "yuv420p",
                 f"{label}: pixel format yuv420p",
                 f"{label}: pixel format is {stream.get('pix_fmt')}, expected yuv420p")

    audio = probe_audio_stream(path)
    if audio is None:
        report.failed(f"{label}: no audio stream found")
        return stream

    report.check(audio.get("codec_name") == "aac",
                 f"{label}: audio codec aac",
                 f"{label}: audio codec is {audio.get('codec_name')}, expected aac")
    try:
        sample_rate = int(audio.get("sample_rate"))
    except (TypeError, ValueError):
        sample_rate = None
    report.check(
        sample_rate == EXPECTED_SAMPLE_RATE,
        f"{label}: audio {sample_rate} Hz",
        f"{label}: audio sample rate is {audio.get('sample_rate')} Hz, "
        f"expected {EXPECTED_SAMPLE_RATE} Hz"
    )
    return stream


def check_loudness(report, path, label):
    loudness = measure_loudness(path)
    if loudness is None:
        report.failed(f"{label}: could not measure loudness with ebur128")
        return
    integrated = loudness["integrated"]
    true_peak = loudness["true_peak"]
    report.check(
        LUFS_MIN <= integrated <= LUFS_MAX,
        f"{label}: integrated loudness {integrated:.1f} LUFS "
        f"(within {LUFS_MIN}..{LUFS_MAX})",
        f"{label}: integrated loudness {integrated:.1f} LUFS "
        f"(outside {LUFS_MIN}..{LUFS_MAX})"
    )
    report.check(
        true_peak <= TRUE_PEAK_MAX,
        f"{label}: true peak {true_peak:.1f} dBTP (<= {TRUE_PEAK_MAX})",
        f"{label}: true peak {true_peak:.1f} dBTP (> {TRUE_PEAK_MAX})"
    )


def check_raw(report, ep_dir, ep_str):
    raw_path = ep_dir / f"episode_{ep_str}_raw.mp4"
    voice_path = ep_dir / f"episode_{ep_str}_voice.mp3"
    durations_path = ep_dir / f"episode_{ep_str}_scene_durations.json"

    if not report.check(raw_path.exists(), f"{raw_path.name} present",
                        f"{raw_path.name} missing (run Module 4)"):
        return

    check_video_format(report, raw_path, "raw")

    raw_duration = probe_duration(raw_path)
    if raw_duration is None:
        report.failed("raw: could not read duration")
        return
    report.passed(f"raw: duration {raw_duration:.3f}s")

    if voice_path.exists():
        voice_duration = probe_duration(voice_path)
        if voice_duration is None:
            report.failed("raw: could not read voice mp3 duration")
        else:
            delta = abs(raw_duration - voice_duration)
            report.check(
                delta <= DURATION_TOLERANCE,
                f"raw: duration {raw_duration:.3f}s vs voice {voice_duration:.3f}s "
                f"(delta {delta:.3f}s)",
                f"raw: duration {raw_duration:.3f}s vs voice {voice_duration:.3f}s "
                f"(delta {delta:.3f}s > {DURATION_TOLERANCE}s)"
            )
    else:
        report.failed(f"raw: {voice_path.name} missing, cannot compare durations")

    if durations_path.exists():
        total_duration = float(load_json(durations_path).get("total_duration_seconds", 0.0))
        delta = abs(raw_duration - total_duration)
        report.check(
            delta <= DURATION_TOLERANCE,
            f"raw: duration {raw_duration:.3f}s vs timeline {total_duration:.3f}s "
            f"(delta {delta:.3f}s)",
            f"raw: duration {raw_duration:.3f}s vs timeline {total_duration:.3f}s "
            f"(delta {delta:.3f}s > {DURATION_TOLERANCE}s)"
        )

    check_loudness(report, raw_path, "raw")


def check_final(report, ep_dir, ep_str):
    final_path = ep_dir / f"episode_{ep_str}_final.mp4"
    raw_path = ep_dir / f"episode_{ep_str}_raw.mp4"
    ass_path = ep_dir / f"episode_{ep_str}_subtitles.ass"

    if not report.check(final_path.exists(), f"{final_path.name} present",
                        f"{final_path.name} missing (run Module 5)"):
        return

    # The raw checks apply to the final video too.
    check_video_format(report, final_path, "final")
    check_loudness(report, final_path, "final")

    final_duration = probe_duration(final_path)
    if final_duration is None:
        report.failed("final: could not read duration")
        return
    report.passed(f"final: duration {final_duration:.3f}s")

    if raw_path.exists():
        raw_duration = probe_duration(raw_path)
        if raw_duration is None:
            report.failed("final: could not read raw duration")
        else:
            delta = abs(final_duration - raw_duration)
            report.check(
                delta <= DURATION_TOLERANCE,
                f"final: duration {final_duration:.3f}s vs raw {raw_duration:.3f}s "
                f"(delta {delta:.3f}s)",
                f"final: duration {final_duration:.3f}s vs raw {raw_duration:.3f}s "
                f"(delta {delta:.3f}s > {DURATION_TOLERANCE}s)"
            )
    else:
        report.failed(f"final: {raw_path.name} missing, cannot compare durations")

    if not report.check(ass_path.exists(), f"{ass_path.name} present",
                        f"{ass_path.name} missing"):
        return

    events = parse_ass_events(ass_path.read_text(encoding="utf-8"))
    if not report.check(bool(events), f"{len(events)} subtitle event(s)",
                        "no Dialogue events in the .ass file"):
        return

    starts = [e["start"] for e in events]
    report.check(
        all(starts[i] <= starts[i + 1] for i in range(len(starts) - 1)),
        "subtitle event starts are non-decreasing",
        "subtitle event starts are not in order"
    )

    overlaps = [
        (i, events[i]["end"], events[i + 1]["start"])
        for i in range(len(events) - 1)
        if events[i]["end"] > events[i + 1]["start"] + 1e-6
    ]
    report.check(
        not overlaps,
        "no subtitle event overlaps the next",
        f"{len(overlaps)} subtitle event(s) overlap the next, first at index "
        f"{overlaps[0][0]} ({overlaps[0][1]:.2f}s > {overlaps[0][2]:.2f}s)"
        if overlaps else "subtitle events overlap"
    )

    last_end = events[-1]["end"]
    report.check(
        last_end < final_duration,
        f"last subtitle ends at {last_end:.2f}s, before the video ends at {final_duration:.2f}s",
        f"last subtitle ends at {last_end:.2f}s, at or after the video end {final_duration:.2f}s"
    )


def check_package(report, ep_dir, ep_str, upload_root, base_out_dir):
    final_path = ep_dir / f"episode_{ep_str}_final.mp4"
    if not report.check(final_path.exists(), f"{final_path.name} present",
                        f"{final_path.name} missing"):
        return

    final_size = final_path.stat().st_size
    for plat in PLATFORMS:
        dest = upload_root / plat / f"ep{ep_str}.mp4"
        if not dest.exists():
            report.failed(f"package: {dest} missing")
            continue
        size = dest.stat().st_size
        report.check(
            size == final_size,
            f"package: {plat}/ep{ep_str}.mp4 matches final ({size} bytes)",
            f"package: {plat}/ep{ep_str}.mp4 is {size} bytes, final is {final_size} bytes"
        )

    log_path = base_out_dir / "episode_log.json"
    if not report.check(log_path.exists(), f"{log_path.name} present",
                        f"{log_path.name} missing"):
        return

    try:
        log_data = load_json(log_path)
    except (json.JSONDecodeError, OSError) as e:
        report.failed(f"package: could not read {log_path.name}: {e}")
        return

    entry = next(
        (e for e in log_data.get("episodes", []) if e.get("episode") == ep_str_int(ep_str)),
        None
    )
    if entry is None:
        report.failed(f"package: no entry for episode {ep_str} in {log_path.name}")
        return

    report.check(
        entry.get("status") == "upload_ready",
        f"package: episode {ep_str} status is upload_ready",
        f"package: episode {ep_str} status is {entry.get('status')!r}, expected 'upload_ready'"
    )


def ep_str_int(ep_str):
    try:
        return int(ep_str)
    except ValueError:
        return ep_str


# ---------------------------------------------------------------- entry point

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Verify one episode of Ashes of the Chosen at a pipeline stage"
    )
    parser.add_argument("--episode", "-e", type=int, required=True, help="Episode number (e.g. 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Base output directory")
    parser.add_argument("--upload-dir", type=str, default=DEFAULT_UPLOAD_DIR,
                        help="Upload directory root (used by --stage package)")
    parser.add_argument("--stage", type=str, required=True,
                        choices=["voice", "raw", "final", "package"],
                        help="Pipeline stage to verify")
    return parser.parse_args()


def main():
    args = parse_arguments()
    base_out_dir = Path(args.output_dir or os.getenv("EPISODE_OUTPUT_DIR", DEFAULT_OUTPUT_DIR))
    ep_str = f"{args.episode:02d}"
    ep_dir = base_out_dir / f"episode-{ep_str}"
    upload_root = Path(args.upload_dir)

    print(f"Checking Episode {args.episode} at stage '{args.stage}' in {ep_dir}\n")

    if not ep_dir.exists():
        print(f"FAIL: episode directory not found: {ep_dir}")
        sys.exit(1)

    report = Report()
    if args.stage == "voice":
        check_voice(report, ep_dir, ep_str)
    elif args.stage == "raw":
        check_raw(report, ep_dir, ep_str)
    elif args.stage == "final":
        check_final(report, ep_dir, ep_str)
    elif args.stage == "package":
        check_package(report, ep_dir, ep_str, upload_root, base_out_dir)

    sys.exit(report.summary())


if __name__ == "__main__":
    main()
