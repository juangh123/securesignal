# SecureSignal — Flare Summer Signal Hackathon Submission

**Bounty:** Bounty 2 — Confidential Compute Apps
**Event:** Flare Summer Signal (online hackathon) · deadline 2026-08-14 19:59 UTC+8
**GitHub:** https://github.com/juangh123/securesignal

---

## 1. Project Name
**SecureSignal** — Privacy-preserving AI portfolio advisor powered by Flare Confidential Compute

## 2. Bounty
**Bounty 2: Confidential Compute Apps.** SecureSignal demonstrates a complete confidential-compute app stack on Flare: encrypted-in-browser portfolio data, analysis inside a TEE, attestation-anchored results on-chain, and live FTSO pricing — all working end-to-end on Coston2.

## 3. Product Intro
SecureSignal lets users get personalized crypto portfolio risk analysis and rebalancing advice **without ever exposing their holdings**. Positions are encrypted in the browser with a one-time ECIES session key and decrypted **only inside the TEE enclave** during execution. The result ships with an attestation signature that is verified on-chain against the registered TEE key — so anyone can audit that the answer really came from the enclave, and **not even we can see your data.**

## 4. Target Users
- Institutional investors and DAO treasuries that want portfolio risk analysis without disclosing positions or strategy.
- Retail users who want personalized, multi-chain rebalancing advice while keeping their balances private.
- Developers and teams looking for a reference implementation of confidential compute apps on Flare (TEE + ECIES + on-chain attestation + FTSO).

## 5. Demo & Links
- **Live App:** https://securesignal.vercel.app (connect a wallet on the Flare Coston2 testnet, chainId 114)
- **TEE Backend (used by the app, real hardware attestation):** https://d1tubqcwiwwev5.cloudfront.net (`/health` returns `attestation_mode=aws-nitro-enclaves` and the verified PCR0)
- **Public dev-simulated demo (DeepSeek LLM):** https://securesignal-tee.onrender.com (`/health` returns `attestation_mode=dev-simulated`, `llm_configured=true`)
- **Demo Video (2:19, English voiceover + subtitles):** https://youtu.be/1V5yuxIENvc
- **Direct video fallback:** https://github.com/juangh123/securesignal/raw/main/video/dist/SecureSignal_demo_1080p_v3.mp4
- **Source:** https://github.com/juangh123/securesignal (contracts/, tee-service/, frontend/, docs/)

## 6. How We Use Flare
1. **Confidential Compute (TEE):** The analysis engine runs inside a real, non-debug AWS Nitro Enclave in production. The enclave produces an AWS NSM COSE/CBOR attestation document bound to the task, result hash, nonce, and ECIES public key. The TEE public key is registered on-chain; every result also carries the EIP-191 signature verified by `ecrecover`. Before decrypting, the TEE re-derives `keccak256(encrypted_data)` and requires it to equal the on-chain `inputDataHash`, so a payload cannot be substituted for another task. The enclave uses a dedicated gas-only relayer account; the contract owner key is kept out of the enclave.
2. **Coston2 Smart Contracts:** `AnalysisRegistry` stores the TEE key/address, verifies attestations, rejects forged signatures, and logs every task result (`ResultSubmitted`), so results are publicly auditable. A companion `FtsoV2Reader` wraps Flare’s official price feed contract.
3. **FTSO (live):** The TEE engine reads real-time, decentralized price feeds directly from the official FtsoV2 contract on Coston2 inside the analysis flow — no centralized price source.
4. **EVM compatibility:** Users interact through standard MetaMask-style wallets; ECIES session keys are exchanged over the normal wallet UX without separate key-pair management.

## 7. What We Built During the Hackathon
Pre-hackathon state: none — this is a new project, not an existing product.
The hackathon deliverable (built July–August 2026, before the 2026-08-14 deadline):
- **TEE analysis engine** (Python/FastAPI): ECIES decryption, portfolio risk scoring (LLM-ready with deterministic rule-engine fallback), live FTSO pricing, result relaying to the chain.
- **Smart contracts** (Solidity/Hardhat): `AnalysisRegistry` + `FtsoV2Reader`, deployed to Coston2 and verified end-to-end (12/12 production smoke test, 2026-07-19).
- **Web 3.0 app** (Next.js 16): wallet connect, client-side ECIES encryption, encrypted request submission, decrypted result display, and on-chain verification UI — fully in English.
- **Cloud deployment:** frontend on Vercel; public demo TEE on Render.

Post-deadline hardening (September 29 – October 3, 2026; listed for completeness, not
part of the original hackathon submission):
- AWS Nitro Enclaves hardware attestation (non-debug; NSM COSE/CBOR, PCR0) with a verified Coston2 result (initial task 18, rebuilt through 2.5.0/2.6.0, current 2.7.0 task 22), published over CloudFront HTTPS, plus an on-chain `keccak256(PCR0)` measurement commitment.
- PCR-conditioned KMS key release: the parent instance receives only KMS ciphertext and `kms:Decrypt` is restricted to the enclave's attested PCR0/PCR1/PCR2, so the TEE signing key and the dedicated relayer key are released only inside the enclave.
- Live DeepSeek `deepseek-flash` LLM configuration; `/analyze` reports `analysis_mode="llm"` with rule-engine fallback.
- FTSO asset coverage expanded from 3 to 31 feeds; on-chain task gate and task-expiry hardening on `/analyze`.

## 8. Contract & Deployment Details
**Network:** Flare Coston2 Testnet (chainId 114)

| Item | Value |
|---|---|
| AnalysisRegistry | `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482` |
| FtsoV2Reader | `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a` |
| Registered TEE address | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |
| TEE public key | `04088c6f6e685b84d396521b59d8b8ff794f4d6a27d47d487b716eced258fa76644e36bee0f46525f9920c9b6dd9f9ef1773d6aff610b0f944d29b0624f4cc10b6` |
| AWS Nitro Enclave PCR0 | `f081c1daa049abc23db7b64f82d674d8d3d52230e4526d7916dc4e49ee073453a3856b678db227a9e6591fceb8a61212` |
| AWS Nitro Enclave PCR1 | `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493` |
| AWS Nitro Enclave PCR2 | `f6e06e398c9fbfe2f203c1991ef62b1ee0666245d9750a9b7c360cbe2be9a7a6a264e521f6e6a95acbbf33367fe99a36` |
| KMS key | `arn:aws:kms:us-east-1:615854521686:key/9206fce2-2bbc-42d9-95c4-8b8958213897` (Decrypt conditioned on the PCRs above) |
| Verified result tx (task 22, 2.7.0) | `0x09fa733083965ef579aea8e0a9b9da08e0b570c49ebe690662e670bf2a54d13a` (requestAnalysis) / `0xf25433a4e57611271379c429c63455fcbe319f0df08ccd3b8e42a950a70d8ba8` (ResultSubmitted) |
| On-chain measurement commitment | `keccak256(PCR0)` = `0xc9ff301894c99edbab0f2e673c0e7363c8de67b481f7466fc43a0333207a7331` (`rotateTeeKey` tx `0x915bf9033f65700edd341aeeb86a59bf604be58515e8a8f0502482d955288935`) |
| Attestation evidence | `deliverables/aws-nitro-attestation-22.json` + verification JSON |

**Verification:** `frontend/e2e/e2e-coston2.mjs` production smoke test passes 12/12 against live Coston2 — real FTSO prices through the enclave-local RPC bridge, attestation `ecrecover` matches the TEE address, on-chain status = Verified. The NSM COSE/CBOR document from that run was verified separately against the pinned AWS Nitro root (certificate chain, ES384 signature, nonce, user data, ECIES public key, PCR0); task 22's raw evidence and verification output are in `deliverables/aws-nitro-attestation-22.json` and `deliverables/aws-nitro-attestation-22-verification.json`. On this build the runtime bundle contains only KMS ciphertext, and the KMS key policy allows `Decrypt` only for attestations matching PCR0/PCR1/PCR2, so the parent instance never sees the TEE or relayer private keys.

**Testing & distribution status (honest):** 12/12 production smoke assertions on Coston2, 23/23 local end-to-end assertions, and a 2:19 recorded live demo. No external pilot users, paid distribution, or partnership commitments yet; the public live app and open-source repo are the current distribution channels.

## 9. Honest Engineering Notes
- **Attestation:** the verified production path is a real AWS Nitro Enclave. We validate the NSM COSE/CBOR document against the pinned AWS Nitro root certificate, the certificate chain, ES384 signature, nonce, user data, ECIES public key, and measured PCR0. The public Render endpoint can still choose the explicitly labelled `dev-simulated` mode for demos.
- **LLM:** the engine calls an OpenAI-compatible LLM when configured; otherwise it falls back to a deterministic rule engine. The recorded live demo ran the rule engine (output fully in English); as of 2026-09-30 the live deployment is configured with DeepSeek `deepseek-flash`, so `/analyze` now reports `analysis_mode="llm"` with the rule engine still in place as fallback.
- **Mainnet:** contracts are deployed on Coston2 testnet; mainnet deployment is part of the roadmap.

## 10. Roadmap
- **Completed:** real hardware-rooted TEE attestation on AWS Nitro Enclaves with verified PCR0 and on-chain result anchoring.
- **Q3–Q4 2026:** wallet auto-import of holdings, FAssets (FXRP) analysis.
- **Q4 2026:** DAO treasury multi-sig report mode.
- **2027:** Flare ecosystem grant; open the TEE analysis API to other builders and launch a Confidential Oracle service on Flare Mainnet.
