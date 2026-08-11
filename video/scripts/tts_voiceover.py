# -*- coding: utf-8 -*-
"""Generate 6 voiceover MP3s + word-split SRT subtitles via edge-tts.

Usage:  python video/scripts/tts_voiceover.py
Output: video/raw/voice_N.mp3, video/subs/voice_N.srt, video/voiceover_meta.json
"""
import asyncio, json, os, re, subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
os.chdir(ROOT)

VOICE = "en-US-AndrewNeural"
RATE = "-8%"
FF = r"C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"

LINES = [
    "This is SecureSignal. Confidential portfolio intelligence, built on Flare Confidential Compute.",
    "Getting personalized crypto advice today means handing your full holdings to a centralized service you cannot audit. That exposes you to front-running, privacy leaks, and targeted attacks. SecureSignal runs the analysis entirely inside a Trusted Execution Environment on Flare. Your data is encrypted in the browser, decrypted only inside the enclave, and every result carries a verifiable on-chain attestation. Not even we can see your data.",
    "Here is how it works. The frontend generates a one-time session key, and encrypts your portfolio with the TEE's public key. A registry contract on the Coston2 testnet records the task and the TEE's attestation key. Inside the enclave, the TEE node decrypts the payload, runs the risk analysis, signs the result, and encrypts it back to your session key. Finally, the app verifies the signature, and the on-chain result hash.",
    "Let's see it live. I connect my wallet on the Coston2 testnet, and enter a sample portfolio. When I click run, the app encrypts everything locally, nothing leaves the browser in plaintext. Now it registers the analysis task on-chain. The encrypted payload goes to the TEE node, which performs the analysis inside the enclave. And here is the result. Risk score, per-asset rebalancing advice, and a summary, decrypted locally, and only visible to me.",
    "At the bottom, the app proves end-to-end integrity. The attestation's result hash matches the decrypted payload, signed by the registered TEE key, and anchored by the registry contract on Flare. Everything you see was built during this hackathon. The TEE engine, the Coston2 contracts, and this encrypted frontend.",
    "Next, we plan to pull FTSO price feeds directly inside the enclave, add zero-knowledge risk proofs for lending protocols, and launch on Flare mainnet. Try the live demo. Links are below. Thank you!",
]

def fmt(ms):
    h, rem = divmod(int(ms), 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def mp3_duration(path):
    out = subprocess.run([FF, "-i", path], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)

def split_line(words, span_ms):
    chunks, cur, cur_len = [], [], 0
    for w in words:
        if len(cur) >= 8 or (cur and cur_len + len(w) + 1 > 52):
            chunks.append(cur); cur, cur_len = [], 0
        cur.append(w); cur_len += len(w) + 1
    if cur:
        chunks.append(cur)
    total_chars = sum(len(w) + 1 for c in chunks for w in c)
    out, acc = [], 0
    for c in chunks:
        w_chars = sum(len(w) + 1 for w in c)
        start = acc / total_chars * span_ms
        acc += w_chars
        end = acc / total_chars * span_ms
        out.append((start, end, " ".join(c)))
    return out

async def gen_one(idx, text):
    import edge_tts
    comm = edge_tts.Communicate(text, voice=VOICE, rate=RATE)
    audio = bytearray()
    sents = []
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
        elif chunk["type"] == "SentenceBoundary":
            sents.append((int(chunk["offset"]) / 10000, int(chunk["duration"]) / 10000, chunk["text"]))
    with open(f"video/raw/voice_{idx}.mp3", "wb") as f:
        f.write(audio)
    if not sents:
        sents = [(0, 1, text)]
    captions = []
    for s_start, s_dur, s_text in sents:
        for cs, ce, ct in split_line(s_text.split(), s_dur):
            captions.append((s_start + cs, s_start + ce, ct))
    srt = "".join(f"{i+1}\n{fmt(s)} --> {fmt(e)}\n{t}\n\n" for i, (s, e, t) in enumerate(captions))
    with open(f"video/subs/voice_{idx}.srt", "w", encoding="utf-8") as f:
        f.write(srt)
    return {"idx": idx, "duration_ms": mp3_duration(f"video/raw/voice_{idx}.mp3") * 1000, "captions": len(captions)}

async def main():
    segs = []
    for i, text in enumerate(LINES, 1):
        r = await gen_one(i, text)
        segs.append(r)
        print(f"voice_{i}: {r['duration_ms']:.0f}ms  captions={r['captions']}")
    with open("video/voiceover_meta.json", "w", encoding="utf-8") as f:
        json.dump({"voice": VOICE, "rate": RATE, "segments": segs}, f, ensure_ascii=False, indent=2)
    print("total:", round(sum(s["duration_ms"] for s in segs) / 1000, 2), "s")

if __name__ == "__main__":
    asyncio.run(main())
