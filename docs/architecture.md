# System Architecture

## Overview

SecureSignal processes user portfolio data inside a Trusted Execution
Environment (TEE) so that no third party — including the operator — can see
the plaintext. The flow below reflects the current implementation; items that
are dev-simulated in the hackathon build are marked as such.

The flow is as follows:

1. **User Client (Browser)**: The user connects their wallet (EVM compatible)
   and inputs their crypto holdings. The data is encrypted locally with the
   TEE's public key using secp256k1 ECIES (`eciesjs`), after cross-checking
   the key served by `GET /public-key` against the on-chain
   `activeTeePublicKey`.
2. **Analysis Registry (On-chain)**: The client registers a task on the Flare
   network (or local Hardhat chain) by calling `requestAnalysis(inputDataHash)`,
   anchoring the hash of the request against tampering. The resulting `taskId`
   is read from the `AnalysisRequested` event.
3. **TEE Service**: The client posts the encrypted payload
   (`{ task_id, encrypted_data }`) to the FastAPI service. In production this
   runs inside GCP Confidential Space; in local dev it is a plain process.
4. **Processing**: Inside the service:
   - The payload is decrypted with the TEE's private key (`eciespy`), which is
     sourced from `TEE_PRIVATE_KEY` and never leaves the service.
   - Prices are read directly from the canonical Coston2 `FtsoV2` contract
     (resolved via FlareContractRegistry); `FtsoV2Reader` is only an on-chain
     reference contract. There is **no silent fallback**: read failures
     raise an explicit error. Fixture prices are used only when
     `ANALYSIS_OFFLINE=1` is set, and are flagged as
     `price_source: "offline-fixture"`.
   - The analysis engine (rule-based + LLM-assisted evaluation) scores the
     portfolio.
   - The result JSON is re-encrypted to the client's session public key.
5. **Attestation & Verification**: The service builds a structured attestation
   token (`{ result_hash, task_id, image_digest, tee_address, timestamp,
   mode }`) and signs `(task_id, result_hash)` with the TEE's secp256k1
   signing key. It then calls `submitResult(taskId, resultHash, attestation)`
   as a relayer. On-chain, `_verifyAttestation` uses `ecrecover` to check that
   the signer equals the registered `teeAddress`; mismatches revert.
   - *Dev-simulated*: in the hackathon build the token carries
     `mode: "dev-simulated"` — it is a TEE-signed document, not a GCP vTPM
     JWT. Swapping in real Confidential Space attestation is a documented TODO
     (`tee-service/attestation/vtpm.py`).
6. **Delivery**: The `/analyze` response returns `{ task_id, encrypted_result,
   attestation, result_hash, onchain_submitted }` directly. The client
   decrypts the result with its session private key and can independently
   verify `result_hash` against the on-chain `ResultSubmitted` event before
   displaying the analysis.

## Key Components

- **Frontend**: Next.js 16 (React 19), wagmi v3 / viem with
  `@web3modal/wagmi` for wallet connection, `eciesjs` for ECIES, TailwindCSS 4.
- **Smart Contracts**: Solidity 0.8.20 (Hardhat). `AnalysisRegistry`
  (OpenZeppelin `Ownable`; task anchoring, TEE key registry via
  `rotateTeeKey`, attestation-verified `submitResult`) and `FtsoV2Reader`
  (minimal `IFtsoV2` interface; `@flarenetwork/flare-periphery-contracts`
  available). `FtsoV2` itself is resolved through the canonical
  FlareContractRegistry (`0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019`).
- **TEE Backend**: Python FastAPI. `eciespy` for secp256k1 ECIES, `web3.py`
  for FTSO reads and relayer submission, structured dev attestation in
  `attestation/vtpm.py`. Production target: GCP Confidential Space with real
  vTPM attestation.
- **Encryption protocol**: secp256k1 ECIES (ECDH → HKDF-SHA256 → AES-256-GCM),
  wire format `65B ephemeral pubkey || 16B nonce || 16B tag || ciphertext`,
  base64-encoded. Byte-compatible between `eciesjs` and `eciespy`; verified
  with cross-decryption test vectors in `frontend/e2e/`.

## Reproducible Build

`tee-service/Dockerfile` pins the base image by digest
(`python:3.11-slim@sha256:...`) and installs dependencies with
`pip install --require-hashes` from `requirements-lock.txt` (every package,
including transitive deps, pinned with sha256 hashes; regenerate procedure in
the lock file header).
