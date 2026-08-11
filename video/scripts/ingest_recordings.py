# -*- coding: utf-8 -*-
"""Normalize user-recorded screen captures into assembly-ready clips.

Produces:
  video/raw/seg2_landing.real.mp4  (from A_landing, optional)
  video/raw/seg4_demo.real.mp4     (from B_demo)
  video/raw/seg5_result.real.mp4   (from C_result)

Usage:
  python video/scripts/ingest_recordings.py --b raw/B_demo.mp4 --b-start 2 --b-dur 32.5
  python video/scripts/ingest_recordings.py --a raw/A_landing.mp4 --b raw/B_demo.mp4 --c raw/C_result.mp4 --c-dur 22.5

Notes:
  - Default durations use the full source length (probed automatically).
  - Clips are center-cropped to 1920x1080, 30fps, yuv420p, audio removed.
"""
import argparse, os, re, subprocess, sys

ROOT = r"E:\AI WORK\Flare Confidential Compute"
FF = r"C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"

def probe_duration(path):
    r = subprocess.run([FF, "-i", path], capture_output=True, text=True, errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s

def norm(src, out, start, dur):
    if not os.path.exists(src):
        print("SKIP (missing):", src)
        return False
    full = probe_duration(src)
    if dur is None:
        dur = full if full else None
    if dur is None:
        print("SKIP (cannot probe):", src)
        return False
    print(f"norm {os.path.basename(src)} -> {out}  start={start} dur={dur:.2f} (full={full:.2f})")
    cmd = [FF, "-y", "-loglevel", "error"]
    if start and start > 0:
        cmd += ["-ss", str(start)]
    cmd += ["-i", src, "-t", str(dur),
            "-vf", "fps=30,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-an", out]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        print("FAILED:", r.stderr[-1500:])
        return False
    print("  ok", out, "%.1f MB" % (os.path.getsize(out) / 1e6))
    return True

def main():
    ap = argparse.ArgumentParser(description="Normalize real screen recordings for SecureSignal assembly")
    ap.add_argument("--a", dest="a", default=None, help="A_landing source (optional)")
    ap.add_argument("--a-start", type=float, default=0)
    ap.add_argument("--a-dur", type=float, default=None)
    ap.add_argument("--b", dest="b", default=None, help="B_demo source")
    ap.add_argument("--b-start", type=float, default=0)
    ap.add_argument("--b-dur", type=float, default=None)
    ap.add_argument("--c", dest="c", default=None, help="C_result source")
    ap.add_argument("--c-start", type=float, default=0)
    ap.add_argument("--c-dur", type=float, default=None)
    args = ap.parse_args()

    raw = os.path.join(ROOT, "video", "raw")
    if args.a:
        norm(args.a, os.path.join(raw, "seg2_landing.real.mp4"), args.a_start, args.a_dur)
    if args.b:
        norm(args.b, os.path.join(raw, "seg4_demo.real.mp4"), args.b_start, args.b_dur)
    if args.c:
        norm(args.c, os.path.join(raw, "seg5_result.real.mp4"), args.c_start, args.c_dur)
    print("DONE")

if __name__ == "__main__":
    main()
