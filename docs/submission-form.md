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
- TEE backend (real AWS Nitro Enclave, used by the app): `https://d1tubqcwiwwev5.cloudfront.net` (`/health` reports `attestation_mode=aws-nitro-enclaves`)
- Dev-simulated demo backend (DeepSeek LLM): `https://securesignal-tee.onrender.com`
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
3. **TEE analysis** — the AWS Nitro Enclave decrypts the payload, reads live prices from the official FtsoV2 contract through an enclave-local RPC bridge, and runs risk analysis (LLM when configured, deterministic rule engine otherwise).
4. **Hardware attestation** — each result also carries an AWS NSM COSE/CBOR attestation document bound to `task_id + result_hash + nonce + ECIES public key`. The document is verified against the pinned AWS Nitro root and the measured PCR0.
5. **Verifiable result** — the signed attestation + result hash are submitted on-chain; the contract verifies the signature with `ecrecover` against the registered TEE key.

### Why Flare
- **Confidential Compute** — on-chain attestation makes the TEE verifiable without trusting us.
- **FTSO (live)** — free, decentralized price feeds consumed directly inside the analysis engine.
- **EVM compatibility** — standard wallet UX; no disjoint key management.

### What We Built During the Hackathon
- **TEE engine** (Python/FastAPI): ECIES decryption, portfolio risk scoring, FTSO pricing, attestation signing, result relaying.
- **Smart contracts** (Solidity/Hardhat): `AnalysisRegistry` + `FtsoV2Reader`, deployed and verified end-to-end on Coston2.
- **Web 3.0 app** (Next.js 16): wallet connect, client-side encryption, decrypted result & on-chain verification UI.
- **Live deployment**: frontend on Vercel; the Render service remains the public demo endpoint, while the production attestation path is deployed and verified on AWS Nitro Enclaves (`us-east-1`).

### Demo
Video (2:19): https://youtu.be/1V5yuxIENvc

- Live App: https://securesignal.vercel.app (connect wallet on Flare Coston2 testnet)
- TEE Backend: https://d1tubqcwiwwev5.cloudfront.net (real AWS Nitro Enclave; `/health` reports `attestation_mode=aws-nitro-enclaves`)
- Dev-simulated demo backend: https://securesignal-tee.onrender.com (DeepSeek LLM, explicitly labeled `dev-simulated`)
- Source: https://github.com/juangh123/securesignal

### Contracts (Coston2 Testnet, chainId 114)
| Contract | Address |
|---|---|
| AnalysisRegistry | `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482` |
| FtsoV2Reader | `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a` |
| Registered TEE address | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |

**Verification:** production smoke test 12/12 on Coston2 against the AWS Nitro Enclave — real FTSO prices, attestation `ecrecover` == TEE address, on-chain status = Verified. The NSM COSE/CBOR document was verified separately against the pinned AWS Nitro root (certificate chain, ES384 signature, nonce, user_data, ECIES key, PCR0). Verified result transaction: `0xd7fea8b774b535c1fa61fb417645b624429aed70f699d7e13c27e666d2935a22` (`taskId=20`, current 2.5.0 build). Raw evidence: `deliverables/aws-nitro-attestation-20.json` + verification JSON.

**AWS Nitro Enclaves measurements:**

- PCR0: `c126dc6db19cefcda5c0a412fecd692d5f12d801cf5ea5b262d954424a455cf25e6189ace615ad00be541d8295864279`
- PCR1: `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493`
- PCR2: `ee61bc92db0b07d247c054e0402bea829d6272d7d6427892de7ef678e367081f5ee21c2d3eefdbacf84c71ceaf677bfb`
- On-chain commitment: `keccak256(PCR0)` = `0x139c95b7fe1e269feaa9290b9c8e902553bfeda8875631afe705515d2180ca52` (`rotateTeeKey` tx `0x1de5dcd04bee67f6039d83b1efb139275b2252b1ce909bd374bed4d1fd5064c5`; the contract stores the commitment but only enforces the EIP-191 signature)

### Honest Engineering Notes
- The production attestation path is a real, non-debug AWS Nitro Enclave: the NSM document is checked against the pinned AWS Nitro root certificate, certificate chain, ES384 signature, nonce, user data, ECIES public key, and PCR0.
- The public Render demo can still label itself `dev-simulated`; this is a deployment-mode distinction, not a claim that the AWS deployment is simulated.
- The analysis engine calls an OpenAI-compatible LLM when configured and falls back to a deterministic rule engine otherwise. The verified AWS run exercised the deterministic rule engine with real FTSO prices.

### Roadmap
- Completed 2026-10: real AWS Nitro Enclaves attestation, verified end-to-end on Coston2.
- Q4 2026: wallet auto-import of holdings, FAssets (FXRP) analysis, and DAO treasury multi-sig report mode.
- 2027: Flare ecosystem grant; open the TEE analysis API to other builders; launch a Confidential Oracle service on Flare Mainnet.

## 10. Team page
- Members: the logged-in DoraHacks account is added automatically; use "Invite new members" to add co-builders by DoraHacks handle/nickname/email (if any).
- Team information * (short description, paste this):

Independent full-stack builder. Built the complete stack from scratch during the hackathon — Solidity contracts, Python TEE engine, and Next.js DApp — with end-to-end verification on Flare Coston2.

## 11. Contact page
- Contact info is visible only to DoraHacks staff (BUIDL verification / outreach).
- Telegram (primary contact): fill your Telegram username, e.g. `@your_handle`.
- Backup contact *: pick ONE of Discord username / WhatsApp number / WeChat ID that you actually use.
- Update (2026-08-13): the form ENFORCES the Telegram username ("Please enter Telegram username") and opens a Telegram code/QR verification popup.
  - Complete Telegram once with network access (proxy/VPN commonly used for foreign services), then log in via QR/code and return to the form.
  - If Telegram is truly unavailable, ask DoraHacks staff in the hackathon channel whether another primary contact is acceptable.
- WeChat field wants your WeChat ID (not phone number): WeChat -> Me -> profile shows your WeChat ID (e.g. wxid_xxx); set one in WeChat settings if unset.

## 12. Submit page
- Track *: select **Bounty 2 - Confidential Compute Apps**.
- Yes/No toggle: read the question above it and answer honestly (likely a rules/disclosure confirmation).
- Check "I agree to the Terms of Use Agreement and Participant Agreement".
- Click **Submit for Review**.
- After submission it enters review; edits are usually still possible until the deadline (2026-08-14 19:59).
