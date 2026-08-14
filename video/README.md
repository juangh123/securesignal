# SecureSignal Video Production Guide

> **Final Video Output (2026-08-12):** `video/dist/SecureSignal_demo_1080p_v3.mp4` (2:19, 1080p30, English voiceover + hard subtitles). Segments 4/5 use a REAL screen recording of the live English UI: wallet connect -> on-chain requestAnalysis -> TEE analysis -> decrypted result + attestation (real Coston2 tx). UI copy fully in English; scene icons fixed (emoji placeholders restored); transition SFX reworked to soft whooshes/chimes.

> **Repo note:** `video/raw/` and `video/tmp/` are gitignored because they are large local production assets. The tracked deliverables are the final `dist` render, storyboard, scenes, subtitles, and `scripts/assemble_v2.py` (which expects local raw assets to re-render).

## Overview

| Track | Details |
|---|---|
| Video | 1920x1080 @ 30fps, H.264 |
| Audio | AAC Stereo, English AI Voiceover |
| Subtitles | Burned-in hard subtitles + raw SRT |

## Scene Breakdown

1. Title & Value Proposition (0:00 - 0:20)
2. Confidential Compute Architecture & TEE Workflow (0:20 - 0:50)
3. Flare Coston2 Smart Contract Verification (0:50 - 1:15)
4. Interactive DApp Demo Run (1:15 - 1:45)
5. Risk Analytics & Rebalancing Result (1:45 - 2:05)
6. Summary & Roadmap (2:05 - 2:22)
