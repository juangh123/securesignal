# SecureSignal 🔐📊

> Privacy-preserving AI portfolio advisor — powered by Flare Confidential Compute

**Flare Summer Signal Hackathon — Bounty 2: Confidential Compute Apps**

*中文 README 见 [README.md](README.md)*

## Overview
Getting personalized crypto portfolio advice today means handing your full holdings and strategy to a centralized service you cannot audit. This exposes users to front-running, privacy breaches, and targeted attacks.

SecureSignal runs the analysis engine inside a TEE (Trusted Execution Environment) securely on Flare. The holdings are encrypted on the client side using a one-time session key, and decrypted ONLY inside the secure enclave during the execution. Finally, every result ships with an attestation verifying the specific task matching the registered TEE key, ensuring that **not even we can see your data.**

## What we built for the hackathon
For this program, we built the entire stack from zero to prototype:
1. **The TEE Engine:** Python-based risk analysis logic (LLM + deterministic rule engine). The source supports GCP Confidential Space and AWS Nitro Enclaves hardware attestation. The production path is deployed and verified on a non-debug AWS Nitro Enclave; the public Render demo can still label itself `dev-simulated` for demo mode.
2. **The Smart Contracts:** Deployed a task registry contract on Coston2 to handle TEE public key storage and signature verification.
3. **The Web 3.0 App:** Setup a full-stack Next.js app to handle the E2E encryption curve, allowing users to send confidential requests directly via their wallets. 
4. **Cloud Infrastructure:** Orchestrated the full pipeline to production — TEE backend on Render, frontend on Vercel — and verified the live demo end-to-end on Coston2.

## Implementation status (honest)
- Real and verified: on-chain `ecrecover` attestation check, ECIES end-to-end encryption, on-chain result relayer, live FTSO price reads (`price_source="coston2-ftso"`), full web flow on Coston2.
- Public Render demo: the attestation can be a structured JSON token with `mode: "dev-simulated"`. The separately deployed AWS Nitro Enclaves path is real hardware attestation, verified against the pinned AWS root, ES384 signature chain, nonce, user data, ECIES key, and PCR0.
- Analysis mode: `llm` when `LLM_API_KEY` is configured, otherwise `rule-fallback`. The response always labels which path ran.

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
- /tee-service: Python FastAPI service; plain process in dev, AWS Nitro Enclaves in the verified production path.

## Demo
Try the Live App: https://securesignal.vercel.app/
(Ensure you are connected to the Flare Coston2 Testnet)
Live TEE Endpoint (used by the app, real hardware attestation):
https://d1tubqcwiwwev5.cloudfront.net (`/health` reports
`attestation_mode=aws-nitro-enclaves` and the verified PCR0)
Public dev-simulated demo (DeepSeek LLM): https://securesignal-tee.onrender.com
One-command read-only status check: `node tools/ops-status.mjs`
Demo Video (2:19, English): https://youtu.be/1V5yuxIENvc

Direct video fallback: https://github.com/juangh123/securesignal/raw/main/video/dist/SecureSignal_demo_1080p_v3.mp4

## Deployed Contracts (Coston2 Testnet, chainId 114)
- AnalysisRegistry: [`0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482`](https://coston2-explorer.flare.network/address/0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482) — TEE key registry, attestation verification, result logging
- FtsoV2Reader: [`0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a`](https://coston2-explorer.flare.network/address/0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a) — live FTSO price feeds consumed inside the TEE engine
- Registered TEE address: `0xEe4975C290FBF46757A1D90F02c3CF555163556E`

## Future Roadmap
- Completed (2.7.0): on-chain `keccak256(PCR0)` image-digest anchoring plus PCR-conditioned KMS key release; the parent instance only handles KMS ciphertext and can never read the TEE or relayer private keys.
- Extend hardware attestation to additional TEE providers and add automatic key rotation.
- Provide a Zero-Knowledge proof mechanism for users to demonstrate their 'Risk Score' to credit protocols without showing absolute balances.
- Generalize the product to offer a Confidential Oracle service for third-party dApps on Flare Mainnet.

## License

MIT. See [LICENSE](LICENSE).
