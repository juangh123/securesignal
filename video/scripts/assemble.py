# -*- coding: utf-8 -*-
"""Assemble SecureSignal hackathon video: scenes + voiceover + BGM + subtitles."""
import os, re, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
os.chdir(ROOT)

FF = r"C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"

# ---- timeline (seconds) -------------------------------------------------
INTRO = 1.5
SEGS = [
    ("seg1_title",  8.0),
    ("seg2_problem",32.0),
    ("seg3_arch",  31.0),
    ("seg4_demo",  32.5),
    ("seg5_result",22.5),
    ("seg6_outro", 15.0),
]
VO_START = [1.6, 10.0, 42.0, 73.0, 105.5, 128.0]

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        print("CMD FAILED:", " ".join(cmd)[:300])
        print(r.stderr[-3000:])
        sys.exit(1)

def srt_time(ms):
    h, rem = divmod(int(ms), 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def parse_srt(path):
    txt = open(path, encoding="utf-8").read().strip().split("\n\n")
    out = []
    for block in txt:
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        if not m:
            continue
        def to_ms(g):
            return int(g[0])*3600000 + int(g[1])*60000 + int(g[2])*1000 + int(g[3])
        out.append((to_ms(m.groups()[:4]), to_ms(m.groups()[4:]), " ".join(lines[2:])))
    return out

# ---- 1) combined subtitles ----------------------------------------------
combined, idx = [], 1
for i, (name, dur) in enumerate(SEGS):
    for s, e, t in parse_srt(f"video/subs/voice_{i+1}.srt"):
        s += VO_START[i] * 1000
        e += VO_START[i] * 1000
        combined.append((s, e, t))
combined.sort()
with open("video/subs/combined.srt", "w", encoding="utf-8") as f:
    for s, e, t in combined:
        f.write(f"{idx}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n\n")
        idx += 1
print("subtitles:", len(combined), "cues")

# ---- 2) black intro ------------------------------------------------------
os.makedirs("video/tmp", exist_ok=True)
run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
     f"color=c=black:s=1920x1080:r=30:d={INTRO}",
     "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
     "video/tmp/black.mp4"])

# ---- 3) per-segment fades ------------------------------------------------
faded = ["video/tmp/black.mp4"]
for i, (name, dur) in enumerate(SEGS):
    src = f"video/raw/{name}.mp4"
    out = f"video/tmp/{name}_fade.mp4"
    if i == 0:
        vf = f"fade=t=in:st=0:d=0.7,fade=t=out:st={dur-0.8}:d=0.8"
    else:
        vf = f"fade=t=in:st=0:d=0.5,fade=t=out:st={dur-0.7}:d=0.7"
    if i == len(SEGS) - 1:
        vf = f"fade=t=in:st=0:d=0.5,fade=t=out:st={dur-1.6}:d=1.6"
    run([FF, "-y", "-loglevel", "error", "-i", src, "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
         "-an", out])
    faded.append(out)

# ---- 4) concat video -----------------------------------------------------
with open("video/tmp/concat.txt", "w", encoding="utf-8") as f:
    for p in faded:
        f.write(f"file '{os.path.abspath(p).replace(chr(39), chr(39)+chr(39)+chr(39))}'\n")
run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
     "-i", "video/tmp/concat.txt", "-c", "copy", "video/tmp/video_only.mp4"])

# ---- 5) audio mix + subtitles + loudnorm ---------------------------------
total = INTRO + sum(d for _, d in SEGS)
inputs = ["-i", "video/tmp/video_only.mp4"]
for i in range(6):
    inputs += ["-i", f"video/raw/voice_{i+1}.mp3"]
inputs += ["-i", "video/raw/bgm.wav"]

fc = []
fc.append("[1:a]adelay=1600|1600,apad[v1]")
fc.append("[2:a]adelay=10000|10000,apad[v2]")
fc.append("[3:a]adelay=42000|42000,apad[v3]")
fc.append("[4:a]adelay=73000|73000,apad[v4]")
fc.append("[5:a]adelay=105500|105500,apad[v5]")
fc.append("[6:a]adelay=128000|128000,apad[v6]")
fc.append("[v1][v2][v3][v4][v5][v6]amix=inputs=6:duration=longest:normalize=0,volume=1.0,aformat=channel_layouts=stereo[vo]")
fc.append("[7:a]volume=0.13,atrim=0:%.3f,aformat=channel_layouts=stereo[bg]" % total)
fc.append("[vo][bg]amix=inputs=2:duration=first:normalize=0,atrim=0:%.3f,afade=t=out:st=%.3f:d=1.5,loudnorm=I=-16:TP=-1.5:LRA=11[aout]" % (total, total - 1.5))
fc.append("[0:v]subtitles=video/subs/combined.srt:force_style='PlayResX=1920,PlayResY=1080,FontName=Arial,FontSize=42,PrimaryColour=&H00FFFFFF,OutlineColour=&HAA000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=44'[vout]")

run([FF, "-y", "-loglevel", "error"] + inputs +
    ["-filter_complex", ";".join(fc),
     "-map", "[vout]", "-map", "[aout]",
     "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", "30",
     "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
     "-movflags", "+faststart",
     "video/dist/SecureSignal_demo_1080p.mp4"])

sz = os.path.getsize("video/dist/SecureSignal_demo_1080p.mp4")
print(f"OK {total:.1f}s  {sz/1e6:.1f} MB -> video/dist/SecureSignal_demo_1080p.mp4")

