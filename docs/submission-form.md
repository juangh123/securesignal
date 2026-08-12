# DoraHacks Submission Form — Copy-Paste Sheet (SecureSignal)

Form: "Create a new BUIDL and submit" (Flare Summer Signal · Bounty 2: Confidential Compute Apps)

---

## 1. BUIDL (project) name *
`SecureSignal`

## 2. BUIDL logo *
File: `deliverables/SecureSignal_logo_480.png` (480×480 PNG, ~12 KB — meets the <2 MB / 480×480 guideline)

## 3. Vision *
> Describe the problem which this project solves

Paste this (241 chars, limit 256):

Get personalized crypto advice without exposing your portfolio. SecureSignal runs analysis inside a Flare TEE: holdings encrypted in-browser, decrypted only in the enclave, results signed and verified on-chain. Not even we can see your data.

## 4. Category *
`Crypto / Web3`

## 5. GitHub/Gitlab/Bitbucket (optional)
`https://github.com/juangh123/securesignal`

## 6. Project website (optional)
`https://securesignal.vercel.app`

## 7. Demo video (optional)
**YouTube (uploaded):** `https://youtu.be/1V5yuxIENvc`

Fallback direct link (works, but not embedded):
`https://github.com/juangh123/securesignal/raw/main/video/dist/SecureSignal_demo_1080p_v3.mp4`

## 8. Social links (at least one link) *
X/Twitter (project account): `https://x.com/christalma2t3`
Fallback: GitHub profile `https://github.com/juangh123`
---

## Other useful links for the submission text
- TEE backend: `https://securesignal-tee.onrender.com` (`/public-key`)
- Contracts (Coston2, chainId 114): AnalysisRegistry `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482` · FtsoV2Reader `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a`
- Registered TEE address: `0xEe4975C290FBF46757A1D90F02c3CF555163556E`
- Example on-chain result (Coston2 tx): `0xe2d4321b7d49aaf5bd1bc9995c6cf0f12a936b3ae424b6460ace3bad60d457b3`
- Full narrative: `SUBMISSION.md`

## 9. Details (rich-text field) — paste into the form's Markdown editor

# SecureSignal — Privacy-Preserving AI Portfolio Advisor on Flare

**Bounty 2: Confidential Compute Apps** · Flare Summer Signal Hackathon

### The Problem
Getting personalized crypto portfolio advice today means handing your full holdings and strategy to a centralized service you cannot audit — exposing users to front-running, privacy breaches, and targeted attacks.

### The Solution
SecureSignal runs the analysis engine inside a TEE (Trusted Execution Environment) on Flare. Your holdings are encrypted in the browser with a one-time ECIES session key and decrypted **only inside the enclave**. Every result is signed with the registered TEE key and anchored on-chain, so anyone can verify it came from the enclave. **Not even we can see your data.**

### How It Works
1. **Client-side encryption** — the browser encrypts holdings with an ECIES session key (eciesjs ↔ eciespy, byte-compatible).
2. **On-chain registration** — the app registers the analysis task on Flare Coston2.
3. **TEE analysis** — the enclave decrypts, reads live prices from the official FtsoV2 contract, and runs risk analysis (LLM when configured, deterministic rule engine otherwise).
4. **Verifiable result** — the signed attestation + result hash are submitted on-chain; the contract verifies the signature with `ecrecover` against the registered TEE key.

### Why Flare
- **Confidential Compute** — on-chain attestation makes the TEE verifiable without trusting us.
- **FTSO (live)** — free, decentralized price feeds consumed directly inside the analysis engine.
- **EVM compatibility** — standard wallet UX; no disjoint key management.

### What We Built During the Hackathon
- **TEE engine** (Python/FastAPI): ECIES decryption, portfolio risk scoring, FTSO pricing, attestation signing, result relaying.
- **Smart contracts** (Solidity/Hardhat): `AnalysisRegistry` + `FtsoV2Reader`, deployed and verified end-to-end on Coston2.
- **Web 3.0 app** (Next.js 16): wallet connect, client-side encryption, decrypted result & on-chain verification UI.
- **Live deployment**: TEE backend on Render, frontend on Vercel.

### Demo
Video (2:19): https://youtu.be/1V5yuxIENvc

- Live App: https://securesignal.vercel.app (connect wallet on Flare Coston2 testnet)
- TEE Backend: https://securesignal-tee.onrender.com (`/public-key`)
- Source: https://github.com/juangh123/securesignal

### Contracts (Coston2 Testnet, chainId 114)
| Contract | Address |
|---|---|
| AnalysisRegistry | `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482` |
| FtsoV2Reader | `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a` |
| Registered TEE address | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |

**Verification:** production smoke test 12/12 — real FTSO prices, attestation `ecrecover` == TEE address, on-chain status = Verified. Example on-chain result (Coston2 tx): `0xe2d4321b7d49aaf5bd1bc9995c6cf0f12a936b3ae424b6460ace3bad60d457b3`.

### Honest Engineering Notes
- Attestation today is a dev-simulated vTPM (structured JSON + real secp256k1 signing + on-chain `ecrecover`); production-grade GCP Confidential Space vTPM with on-chain image-digest anchoring is the documented upgrade path.
- The analysis engine calls an OpenAI-compatible LLM when configured and falls back to a deterministic rule engine otherwise; the live demo ran the rule engine (English output).

### Roadmap
- Q3 2026: real vTPM attestation, wallet auto-import of holdings, FAssets (FXRP) analysis.
- Q4 2026: DAO treasury multi-sig report mode.
- 2027: Flare ecosystem grant; open the TEE analysis API to other builders; launch a Confidential Oracle service on Flare Mainnet.

## 10. Team page
- Members: the logged-in DoraHacks account is added automatically; use "Invite new members" to add co-builders by DoraHacks handle/nickname/email (if any).
- Team information * (short description, paste this):

Independent full-stack builder. Built the complete stack from scratch during the hackathon — Solidity contracts, Python TEE engine, and Next.js DApp — with end-to-end verification on Flare Coston2.

## 11. Contact page
- Contact info is visible only to DoraHacks staff (BUIDL verification / outreach).
- Telegram (primary contact): fill your Telegram username, e.g. `@your_handle`.
- Backup contact *: pick ONE of Discord username / WhatsApp number / WeChat ID that you actually use.
- If you have no Telegram yet, registering one takes ~1 minute (https://telegram.org) and is the simplest option.
