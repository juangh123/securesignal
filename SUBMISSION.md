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
1. **Confidential Compute (TEE):** The analysis engine (Python) is built to run inside a Gramine/TDX TEE on Flare’s confidential compute infrastructure. The TEE public key is registered on-chain; every result carries an attestation signature that the contract verifies with `ecrecover`.
2. **Coston2 Smart Contracts:** `AnalysisRegistry` stores the TEE key/address, verifies attestations, rejects forged signatures, and logs every task result (`ResultSubmitted`), so results are publicly auditable. A companion `FtsoV2Reader` wraps Flare’s official price feed contract.
3. **FTSO (live):** The TEE engine reads real-time, decentralized price feeds directly from the official FtsoV2 contract on Coston2 inside the analysis flow — no centralized price source.
4. **EVM compatibility:** Users interact through standard MetaMask-style wallets; ECIES session keys are exchanged over the normal wallet UX without separate key-pair management.

## 7. What We Built During the Hackathon
Pre-hackathon state: none — this is a new project, not an existing product.
Everything below was built from zero to working prototype within the hackathon window:
- **TEE analysis engine** (Python/FastAPI): ECIES decryption, portfolio risk scoring (LLM-ready with deterministic rule-engine fallback), live FTSO pricing, attestation signing, result relaying to the chain.
- **Smart contracts** (Solidity/Hardhat): `AnalysisRegistry` + `FtsoV2Reader`, deployed to Coston2 and verified end-to-end (12/12 production smoke tests).
- **Web 3.0 app** (Next.js 16): wallet connect, client-side ECIES encryption, encrypted request submission, decrypted result display, and on-chain verification UI — fully in English.
- **Cloud deployment:** TEE backend live on Render, frontend live on Vercel; final demo video recorded against the live stack with a real Coston2 transaction.

## 8. Contract & Deployment Details
**Network:** Flare Coston2 Testnet (chainId 114)

| Item | Value |
|---|---|
| AnalysisRegistry | `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482` |
| FtsoV2Reader | `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a` |
| Registered TEE address | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |
| TEE public key | `04088c6f6e685b84d396521b59d8b8ff794f4d6a27d47d487b716eced258fa76644e36bee0f46525f9920c9b6dd9f9ef1773d6aff610b0f944d29b0624f4cc10b6` |
| Example on-chain result (Coston2 tx) | `0xe2d4321b7d49aaf5bd1bc9995c6cf0f12a936b3ae424b6460ace3bad60d457b3` (→ AnalysisRegistry, block `0x2065fbd`) |

**Verification:** `frontend/e2e/e2e-coston2.mjs` production smoke test passes 12/12 against live Coston2 — real FTSO prices, attestation `ecrecover` matches the TEE address, on-chain status = Verified.

**Testing & distribution status (honest):** 12/12 production smoke assertions on Coston2, 23/23 local end-to-end assertions, and a 2:19 recorded live demo. No external pilot users, paid distribution, or partnership commitments yet; the public live app and open-source repo are the current distribution channels.

## 9. Honest Engineering Notes
- **Attestation:** today’s attestation is a dev-simulated vTPM (structured JSON + real secp256k1 signing + on-chain `ecrecover`). The production upgrade path to real GCP Confidential Space vTPM with on-chain image-digest anchoring is designed and documented in `docs/deployment.md`; the contract layer already reserves the production interface.
- **LLM:** the engine calls an OpenAI-compatible LLM when configured; otherwise it falls back to a deterministic rule engine. The live demo ran the rule engine (output fully in English).
- **Mainnet:** contracts are deployed on Coston2 testnet; mainnet deployment is part of the roadmap.

## 10. Roadmap
- **Q3 2026:** real vTPM attestation (GCP Confidential Space), wallet auto-import of holdings, FAssets (FXRP) analysis.
- **Q4 2026:** DAO treasury multi-sig report mode.
- **2027:** Flare ecosystem grant; open the TEE analysis API to other builders and launch a Confidential Oracle service on Flare Mainnet.
