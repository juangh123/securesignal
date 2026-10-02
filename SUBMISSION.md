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
- **TEE Backend:** https://securesignal-tee.onrender.com (`/public-key` returns the registered TEE key)
- **Demo Video (2:19, English voiceover + subtitles):** https://youtu.be/1V5yuxIENvc
- **Direct video fallback:** https://github.com/juangh123/securesignal/raw/main/video/dist/SecureSignal_demo_1080p_v3.mp4
- **Source:** https://github.com/juangh123/securesignal (contracts/, tee-service/, frontend/, docs/)

## 6. How We Use Flare
1. **Confidential Compute (TEE):** The analysis engine runs inside a real, non-debug AWS Nitro Enclave in production. The enclave produces an AWS NSM COSE/CBOR attestation document bound to the task, result hash, nonce, and ECIES public key. The TEE public key is registered on-chain; every result also carries the EIP-191 signature verified by `ecrecover`.
2. **Coston2 Smart Contracts:** `AnalysisRegistry` stores the TEE key/address, verifies attestations, rejects forged signatures, and logs every task result (`ResultSubmitted`), so results are publicly auditable. A companion `FtsoV2Reader` wraps Flare’s official price feed contract.
3. **FTSO (live):** The TEE engine reads real-time, decentralized price feeds directly from the official FtsoV2 contract on Coston2 inside the analysis flow — no centralized price source.
4. **EVM compatibility:** Users interact through standard MetaMask-style wallets; ECIES session keys are exchanged over the normal wallet UX without separate key-pair management.

## 7. What We Built During the Hackathon
Pre-hackathon state: none — this is a new project, not an existing product.
Everything below was built from zero to working prototype within the hackathon window:
- **TEE analysis engine** (Python/FastAPI): ECIES decryption, portfolio risk scoring (LLM-ready with deterministic rule-engine fallback), live FTSO pricing, AWS NSM attestation, result relaying to the chain.
- **Smart contracts** (Solidity/Hardhat): `AnalysisRegistry` + `FtsoV2Reader`, deployed to Coston2 and verified end-to-end (12/12 production smoke tests).
- **Web 3.0 app** (Next.js 16): wallet connect, client-side ECIES encryption, encrypted request submission, decrypted result display, and on-chain verification UI — fully in English.
- **Cloud deployment:** frontend on Vercel; public demo TEE on Render; production attestation path deployed and verified on AWS Nitro Enclaves (`us-east-1`).

## 8. Contract & Deployment Details
**Network:** Flare Coston2 Testnet (chainId 114)

| Item | Value |
|---|---|
| AnalysisRegistry | `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482` |
| FtsoV2Reader | `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a` |
| Registered TEE address | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |
| TEE public key | `04088c6f6e685b84d396521b59d8b8ff794f4d6a27d47d487b716eced258fa76644e36bee0f46525f9920c9b6dd9f9ef1773d6aff610b0f944d29b0624f4cc10b6` |
| AWS Nitro Enclave PCR0 | `853316351f15ac48236389561075a8b3d4756d20ac14c77df05a1e8727fdf1ab448422fc91f2d22001bff6ff4562637b` |
| AWS Nitro Enclave PCR1 | `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493` |
| Verified result tx (task 18) | `0xb92493184d1c802128c86caeab4b909b057928fe5203658fa044a89eda398f04` |
| Attestation evidence | `deliverables/aws-nitro-attestation-18.json` + verification JSON |

**Verification:** `frontend/e2e/e2e-coston2.mjs` production smoke test passes 12/12 against live Coston2 — real FTSO prices through the enclave-local RPC bridge, AWS NSM COSE signature verified against the pinned root, attestation `ecrecover` matches the TEE address, on-chain status = Verified. Task 18 also includes a repository evidence bundle under `deliverables/`.

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
