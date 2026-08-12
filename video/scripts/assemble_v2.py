# -*- coding: utf-8 -*-
"""Assemble SecureSignal v2: xfade transitions + ducked BGM + SFX + styled subs."""
import os, re, subprocess, sys

ROOT = r"F:\AI WORK\Flare Confidential Compute"
os.chdir(ROOT)

FF = r"C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"

BLACK = 1.5
SEGS = [("seg1_title", 8.0), ("seg2_problem", 32.0), ("seg3_arch", 31.0),
        ("seg4_demo", 32.5), ("seg5_result", 22.5), ("seg6_outro", 15.0)]
TRANS = ["fade", "fade", "smoothup", "fade", "smoothleft", "fade"]
TD = 0.6

starts, cur = [], BLACK
for i, (_, d) in enumerate(SEGS):
    cur -= TD
    starts.append(cur)
    cur += d
TOTAL = starts[-1] + SEGS[-1][1]
print("segment starts:", [round(s, 2) for s in starts], "total", round(TOTAL, 2))

VO_LOCAL = [0.8, 0.4, 0.4, 0.4, 0.4, 0.4]
VO_START = [round(starts[i] + VO_LOCAL[i], 3) for i in range(6)]
print("VO_START:", VO_START)

def run(cmd, allow_fail=False):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0 and not allow_fail:
        print("CMD FAILED:", " ".join(cmd)[:300])
        print(r.stderr[-3000:])
        sys.exit(1)
    return r

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

combined, idx = [], 1
for i, (name, dur) in enumerate(SEGS):
    for s, e, t in parse_srt(f"video/subs/voice_{i+1}.srt"):
        combined.append((s + VO_START[i]*1000, e + VO_START[i]*1000, t))
combined.sort()
with open("video/subs/combined.srt", "w", encoding="utf-8") as f:
    for s, e, t in combined:
        f.write(f"{idx}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n\n")
        idx += 1
print("subtitles:", len(combined))

os.makedirs("video/tmp/v2", exist_ok=True)
VF = "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p,settb=AVTB"
clips = []
run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:r=30:d={BLACK}",
     "-vf", VF, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-t", str(BLACK),
     "video/tmp/v2/black.mp4"])
clips.append("video/tmp/v2/black.mp4")
for i, (name, dur) in enumerate(SEGS):
    real = f"video/raw/{name}.real.mp4"
    src = real if os.path.exists(real) else f"video/raw/{name}.webm"
    out = f"video/tmp/v2/{name}.mp4"
    run([FF, "-y", "-loglevel", "error", "-i", src, "-t", str(dur), "-vf", VF,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-an", out])
    clips.append(out)
    print("clip ok", name, "src:", os.path.basename(src))

fc = []
prev = "[0:v]"
for i in range(len(SEGS)):
    outl = f"[x{i}]"
    off = round(starts[i], 3)
    fc.append(f"{prev}[{i+1}:v]xfade=transition={TRANS[i]}:duration={TD}:offset={off}{outl}")
    prev = outl
fc.append(prev + "settb=AVTB,format=yuv420p[vout]")

vid_cmd = [FF, "-y", "-loglevel", "error"] + sum([["-i", c] for c in clips], []) + \
          ["-filter_complex", ";".join(fc), "-map", "[vout]", "-c:v", "libx264",
           "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
           "video/tmp/v2/video_concat.mp4"]
run(vid_cmd)
print("video concat ok")

ain = ["-i", "video/tmp/v2/video_concat.mp4"]
for i in range(6):
    ain += ["-i", f"video/raw/voice_{i+1}.mp3"]
ain += ["-i", "video/raw/bgm.wav", "-i", "video/raw/sfx/sfx_mix.wav"]

afc = []
for i in range(6):
    ms = int(round(VO_START[i] * 1000))
    afc.append(f"[{i+1}:a]aresample=48000,adelay={ms}|{ms},apad[v{i+1}]")
afc.append("[" + "][".join(f"v{i+1}" for i in range(6)) + "]amix=inputs=6:duration=longest:normalize=0,volume=1.0,aformat=channel_layouts=stereo[vo]")
afc.append(f"[7:a]aresample=48000,volume=0.16,atrim=0:{TOTAL},afade=t=in:st=0:d=2,afade=t=out:st={TOTAL-0.8:.3f}:d=0.8,aformat=channel_layouts=stereo[bg]")
afc.append("[vo]asplit[voA][voB]")
afc.append("[bg][voA]sidechaincompress=threshold=0.02:ratio=8:attack=12:release=500[bgduck]")
afc.append("[voB][bgduck]amix=inputs=2:duration=first:normalize=0,atrim=0:%.3f[va]" % TOTAL)
afc.append("[8:a]aresample=48000,aformat=channel_layouts=stereo[sfx]")
afc.append("[va][sfx]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[aout]")

substyle = ("PlayResX=1920,PlayResY=1080,FontName=Segoe UI,FontSize=43,Bold=1,"
            "PrimaryColour=&H00FFFFFF,BackColour=&H70050A14,BorderStyle=4,Outline=0,Shadow=0,"
            "Alignment=2,MarginV=50")
fc2 = ";".join(afc)
out_cmd = [FF, "-y", "-loglevel", "warning"] + ain + [
    "-filter_complex", fc2 + f";[0:v]subtitles=video/subs/combined.srt:force_style='{substyle}'[vout2]",
    "-map", "[vout2]", "-map", "[aout]",
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
    "-movflags", "+faststart",
    "video/dist/SecureSignal_demo_1080p_v2.mp4"]
run(out_cmd)
sz = os.path.getsize("video/dist/SecureSignal_demo_1080p_v2.mp4")
print(f"OK {TOTAL:.1f}s {sz/1e6:.1f} MB -> video/dist/SecureSignal_demo_1080p_v2.mp4")
