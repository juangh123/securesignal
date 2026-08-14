# SecureSignal 🔐📊

> Privacy-preserving AI portfolio advisor — powered by Flare Confidential Compute

**Flare Summer Signal Hackathon — Bounty 2: Confidential Compute Apps**

*中文 README 见 [README.md](README.md)*

## Overview
Getting personalized crypto portfolio advice today means handing your full holdings and strategy to a centralized service you cannot audit. This exposes users to front-running, privacy breaches, and targeted attacks.

SecureSignal runs the analysis engine inside a TEE (Trusted Execution Environment) securely on Flare. The holdings are encrypted on the client side using a one-time session key, and decrypted ONLY inside the secure enclave during the execution. Finally, every result ships with an attestation verifying the specific task matching the registered TEE key, ensuring that **not even we can see your data.**

## What we built for the hackathon
For this program, we built the entire stack from zero to prototype:
1. **The TEE Engine:** Developed the python based AI risk analysis logic that operates within Gramine/TDX.
2. **The Smart Contracts:** Deployed a task registry contract on Coston2 to handle TEE public key storage and signature verification.
3. **The Web 3.0 App:** Setup a full-stack Next.js app to handle the E2E encryption curve, allowing users to send confidential requests directly via their wallets. 
4. **Cloud Infrastructure:** Orchestrated the full pipeline to production — TEE backend on Render, frontend on Vercel — and verified the live demo end-to-end on Coston2.

## Why Flare
1. **Confidential Compute:** The core engine leverages Flare's confidential computing offering to ensure end-to-end encryption.
2. **EVM Compatibility:** Smooth user experience interacting with metamask without needing to generate disjoint key pairs.
3. **FTSO (live):** The TEE engine already reads real-time prices directly from Flare's FtsoV2 contract on Coston2 — no centralized price source.

## Target Audience
- Institutional investors wanting portfolio risk analysis without disclosing their positions.
- Retail users seeking personalized multi-chain rebalancing strategies while maintaining privacy. 

## Structure
- /contracts: Solidity contracts for verifying attestations and logging Tasks. (Deployed on Coston2)
- /frontend: Next.js DApp utilizing ECIES encryption for TEE-wallet interactions.
- /tee-service: Python FastAPI layer configured to run in Gramine TEE. 

## Demo
Try the Live App: https://securesignal.vercel.app/
(Ensure you are connected to the Flare Coston2 Testnet)
Live TEE Endpoint: https://securesignal-tee.onrender.com
Demo Video (2:19, English): https://youtu.be/1V5yuxIENvc

Direct video fallback: https://github.com/juangh123/securesignal/raw/main/video/dist/SecureSignal_demo_1080p_v3.mp4

## Deployed Contracts (Coston2 Testnet, chainId 114)
- AnalysisRegistry: [`0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482`](https://coston2-explorer.flare.network/address/0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482) — TEE key registry, attestation verification, result logging
- FtsoV2Reader: [`0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a`](https://coston2-explorer.flare.network/address/0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a) — live FTSO price feeds consumed inside the TEE engine
- Registered TEE address: `0xEe4975C290FBF46757A1D90F02c3CF555163556E`

## Future Roadmap 
- Production-grade vTPM attestation on GCP Confidential Space (Gramine/TDX) with on-chain image-digest anchoring.
- Provide a Zero-Knowledge proof mechanism for users to demonstrate their 'Risk Score' to credit protocols without showing absolute balances.
- Generalize the product to offer a Confidential Oracle service for third-party dApps on Flare Mainnet. 
