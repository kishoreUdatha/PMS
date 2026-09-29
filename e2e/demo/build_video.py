"""Lay the narration under each recorded video and join them into one MP4.

Reads out/manifest.json written by demo.js: for each browser session, the
.webm Playwright recorded and the narration clips with the second each one
started at. Builds one mono track per session by placing every clip at its
offset, muxes it with the video, and concatenates the sessions.

    python e2e/demo/build_video.py [out_dir] [final.mp4]
"""
from __future__ import annotations

import array
import json
import os
import subprocess
import sys
import wave

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:  # a system ffmpeg is just as good
    FFMPEG = "ffmpeg"

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "out")
FINAL = sys.argv[2] if len(sys.argv) > 2 else os.path.join(OUT, "pms_e2e_demo.mp4")
RATE = 16000  # overwritten from the first clip


def duration(path: str) -> float:
    """Seconds, from ffmpeg's own banner -- no ffprobe needed."""
    err = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True).stderr
    for line in err.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            h, m, s = line.split(",")[0].split()[1].split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"no duration for {path}")


def track(clips: list[dict], seconds: float, dest: str) -> None:
    samples = array.array("h", bytes(2 * int((seconds + 1) * RATE)))
    for c in clips:
        with wave.open(c["wav"], "rb") as w:
            assert w.getframerate() == RATE and w.getsampwidth() == 2
            data = array.array("h", w.readframes(w.getnframes()))
        start = int(c["t"] * RATE)
        end = min(len(samples), start + len(data))
        samples[start:end] = data[: end - start]
    with wave.open(dest, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(samples.tobytes())


def main() -> None:
    global RATE
    manifest = json.load(open(os.path.join(OUT, "manifest.json")))
    first = next((c["wav"] for seg in manifest for c in seg["clips"]), None)
    if first:
        with wave.open(first, "rb") as w:
            RATE = w.getframerate()
    parts = []
    for i, seg in enumerate(manifest):
        secs = duration(seg["video"])
        audio = os.path.join(OUT, f"narration_{seg['label']}.wav")
        track(seg["clips"], secs, audio)
        part = os.path.join(OUT, f"part_{i}_{seg['label']}.mp4")
        subprocess.run([
            FFMPEG, "-y", "-loglevel", "error", "-i", seg["video"], "-i", audio,
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p", "-r", "25",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-shortest", part,
        ], check=True)
        parts.append(part)
        print(f"{seg['label']}: {secs:.0f}s video, {len(seg['clips'])} narration lines -> {part}")
    listing = os.path.join(OUT, "parts.txt")
    with open(listing, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", listing, "-c", "copy", "-movflags", "+faststart", FINAL], check=True)
    print(f"final: {FINAL} ({duration(FINAL) / 60:.1f} min)")


if __name__ == "__main__":
    main()
