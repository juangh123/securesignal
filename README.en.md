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
4. **Cloud Infrastructure:** Successfully orchestrated the pipeline to Render to demonstrate the live functionality.

## Why Flare
1. **Confidential Compute:** The core engine leverages Flare's confidential computing offering to ensure end-to-end encryption.
2. **EVM Compatibility:** Smooth user experience interacting with metamask without needing to generate disjoint key pairs.
3. **FTSO (Future implementation):** Directly consuming decentralized pricing oracles inside the enclaves to keep models up to date.

## Target Audience
- Institutional investors wanting portfolio risk analysis without disclosing their positions.
- Retail users seeking personalized multi-chain rebalancing strategies while maintaining privacy. 

## Structure
- /contracts: Solidity contracts for verifying attestations and logging Tasks. (Deployed on Coston2)
- /frontend: Next.js DApp utilizing ECIES encryption for TEE-wallet interactions.
- /tee-service: Python FastAPI layer configured to run in Gramine TEE. 

## Demo
Try the Live App: https://securesignal-app.vercel.app/
(Ensure you are connected to the Flare Coston2 Testnet)
Live TEE Endpoint: https://securesignal-tee.onrender.com

## Future Roadmap 
- Integrate Direct Flare Time Series Oracle (FTSO) into the TEE API for highly secure, real-time asset pricing verification.
- Provide a Zero-Knowledge proof mechanism for users to demonstrate their 'Risk Score' to credit protocols without showing absolute balances.
- Generalize the product to offer a Confidential Oracle service for third-party dApps on Flare Mainnet. 
