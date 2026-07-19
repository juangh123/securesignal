# SecureSignal 🔐📊

> Privacy-preserving AI portfolio advisor — powered by Flare Confidential Compute

**Flare Summer Signal Hackathon — Bounty 2: Confidential Compute Apps**

*中文 README 见 [README.md](README.md)*

## The Problem
Getting personalized crypto portfolio advice today means handing your full
holdings and strategy to a centralized service you cannot audit.

## The Solution
SecureSignal runs the analysis engine inside a TEE (Trusted Execution
Environment): holdings are encrypted client-side and decrypted only inside the
enclave; every result ships with an attestation whose signer is checked
on-chain against the registered TEE key. **Not even we can see your data.**

## Why Flare (and only Flare)
1. **Confidential Compute**: on-chain attestation verification — trustless
   proof that the TEE runs the published code
2. **FTSO**: free, decentralized real-time price feeds consumed directly by
   the analysis engine
3. **EVM compatibility**: standard wallet UX for the encryption key exchange

## Architecture
Full walkthrough: [docs/architecture.md](docs/architecture.md).
**Deployment manual (env reference / Coston2 step-by-step / GCP Confidential
Space integration / LLM setup / troubleshooting):
[docs/deployment.md](docs/deployment.md).**

| Path | What it is |
|---|---|
| `contracts/` | Hardhat project: `AnalysisRegistry` (task anchoring, TEE key registry, attestation-verified result submission) + `FtsoV2Reader` |
| `tee-service/` | Python FastAPI service: ECIES decryption, portfolio analysis (LLM + rule engine), FTSO price reads, attestation signing, on-chain result relayer |
| `frontend/` | Next.js 16 app: wallet connection, client-side ECIES encryption, result decryption & display |

## Implementation Status (honest labels)

**Real, implemented, and verified:**
- On-chain `ecrecover` attestation verification: `rotateTeeKey` (onlyOwner)
  registers the TEE public key and `teeAddress`; `_verifyAttestation` requires
  the recovered signer == registered address; `submitResult` rejects forged
  signatures (negative-path tests included — `npx hardhat test` is green,
  11/11).
- ECIES end-to-end encryption: `eciesjs` (browser) ↔ `eciespy` (TEE service),
  secp256k1, byte-compatible wire format, verified by cross-decryption test
  vectors in both directions.
- On-chain result relay: the TEE service calls
  `submitResult(taskId, resultHash, attestation)` as a relayer; the client can
  cross-check the `ResultSubmitted` event and `resultHash` on-chain.
- **LLM analysis engine**: OpenAI-compatible API (env `LLM_API_KEY` /
  `LLM_BASE_URL` / `LLM_MODEL`). The LLM only produces judgment fields
  (risk_score / risk_level / rebalance / Chinese summary); all portfolio math
  is computed deterministically in `analysis/engine.py`. One automatic retry,
  then fallback to the rule engine; every response is honestly labeled
  `analysis_mode: "llm" | "rule-fallback"`. Without an API key the rule engine
  is used transparently.
- **Real FTSO price reads**: `analysis/price_provider.py` reads the canonical
  Coston2 `FtsoV2` contract via FlareContractRegistry (bytes21 feed IDs, 60s
  TTL cache, 10s RPC timeout) with **no silent fallback to fake prices** —
  failures raise explicit errors. Live-verified (2026-07-19,
  `ANALYSIS_LIVE_TEST=1 python -m unittest analysis.test_price_provider`;
  full output in `tee-service/ftso-live-test.log`):
  BTC/USD **$64,649.78**, ETH/USD **$1,866.52**, FLR/USD **$0.006560**
  (feed timestamp 2026-07-19 04:03 UTC, `price_source="coston2-ftso"`).
  Dev fixture prices are used only under explicit `ANALYSIS_OFFLINE=1` and are
  labeled `price_source: "offline-fixture"`.
- Local end-to-end integration: **23/23 assertions pass**
  (`frontend/e2e/e2e-local-run.log`): chain up → deploy → encrypt →
  `/analyze` → decrypt → on-chain `resultHash` match → `ecrecover` match.
- Full web flow: connect wallet → `requestAnalysis` → encrypt holdings →
  `POST /analyze` → decrypt result → display.
- `docker compose up --build` boots the whole stack (includes a `deploy`
  init service: health check → deploy → register TEE key).

**dev-simulated (explicitly labeled in code and API responses — not production
claims):**
- The attestation token is a structured JSON (`mode: "dev-simulated"`) plus a
  TEE secp256k1 signature — **not** a GCP Confidential Space vTPM JWT.
  Integration path: TODO in `tee-service/attestation/vtpm.py` and
  [docs/deployment.md](docs/deployment.md) §3.
- Locally the "TEE" is a regular FastAPI process — no real enclave on a dev
  machine.
- Local chains have no FTSO, so localhost defaults to `ANALYSIS_OFFLINE=1`
  fixture prices (labeled).
- The on-chain `expectedImageDigest` in local runs is the placeholder
  `keccak256("dev-image")`.

**External dependencies that cannot be closed locally** (integration guides
in docs/deployment.md): real Coston2 deployment (needs a private key + faucet
funds), GCP Confidential Space (needs a GCP TEE environment), real LLM calls
(needs an API key).

## Quick env setup

Full reference: [docs/deployment.md](docs/deployment.md) §1. Minimal set:

```bash
# tee-service (local minimal; for production point RPC_URL at Coston2 or omit)
export TEE_PRIVATE_KEY=0x<tee-key>      # required in prod; ephemeral key if unset (dev only)
export PRIVATE_KEY=0x<relayer-key>      # optional; onchain_submitted=false if unset
export RPC_URL=http://127.0.0.1:8545    # optional; defaults to Coston2 public RPC
export ANALYSIS_OFFLINE=1               # optional; =1 uses fixture prices (required on local chains)
# Enable the LLM (optional): LLM_API_KEY / LLM_BASE_URL / LLM_MODEL

# contracts (only for Coston2 deployment, put in contracts/.env)
PRIVATE_KEY=0x<deployer-key>

# frontend/.env.local (cp .env.example .env.local)
NEXT_PUBLIC_PROJECT_ID=<WalletConnect Cloud project id>   # required, no fallback
NEXT_PUBLIC_TEE_URL=http://localhost:8000                 # optional
```

## Run locally (five steps)

Prerequisites: Node 20+, Python 3.11+, git bash (Windows) or any POSIX shell.

```bash
# 1. Start a local chain (terminal A)
cd contracts
npm install
npx hardhat node

# 2. Deploy contracts + register the dev TEE key (terminal B)
cd contracts
npx hardhat run scripts/deploy.ts --network localhost
npx hardhat run scripts/setup-tee.ts --network localhost
#    → real addresses written to frontend/src/config/contract-addresses.json
#      and tee-service/config/contract-addresses.json

# 3. Start the TEE service (terminal B)
cd ../tee-service
pip install -r requirements.txt
export TEE_PRIVATE_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
export RPC_URL=http://127.0.0.1:8545
export ANALYSIS_OFFLINE=1
uvicorn main:app --host 0.0.0.0 --port 8000

# 4. Start the frontend (terminal C)
cd ../frontend
npm install
cp .env.example .env.local   # fill in NEXT_PUBLIC_PROJECT_ID (WalletConnect Cloud)
npm run dev

# 5. Open http://localhost:3000
```

> The two private keys above are hardhat's public dev accounts (#1 and #0) —
> **local use only**.

**Docker one-shot**: `docker compose up --build` starts the same stack — the
`deploy` init service waits for the chain health check, runs `deploy.ts` and
`setup-tee.ts`, and only then do `tee-service` and `frontend` start. See the
comments in `docker-compose.yml` (compose file structure-validated; not
launched on this machine — no local docker). Compose defaults to `ANALYSIS_OFFLINE=1`
(fixture prices, labeled). For real FTSO reads:
`ANALYSIS_OFFLINE=0 RPC_URL=https://coston2-api.flare.network/ext/C/rpc docker compose up --build`
(keep the default when running against a local chain so the relayer matches
the configured addresses).

## Verify It Yourself
Production trust model: the TEE image digest is anchored on-chain, and the
attestation proves the enclave runs exactly the published code.

1. Build the image: `cd tee-service && docker build -t securesignal-tee .`
   (reproducible: base image pinned by digest, pip dependencies hash-locked
   via `requirements-lock.txt`)
2. Compare the image digest with the on-chain `expectedImageDigest`.
3. Any mismatch = the enclave is not running the published code.

In the current dev build, steps 2–3 use the placeholder digest registered by
`setup-tee.ts`; real vTPM measurement is the TODO above.

## Live Demo
- **Contracts (Coston2 testnet, chainId 114) — deployed & verified end-to-end (2026-07-19)**:
  - AnalysisRegistry: [`0xfA3126Ca8f6F4CEc3cf3a6266B9cd71d4B7fB531`](https://coston2-explorer.flare.network/address/0xfA3126Ca8f6F4CEc3cf3a6266B9cd71d4B7fB531)
  - FtsoV2Reader: [`0xe60745669C54b66F67ae85Ce031D4bDED4311163`](https://coston2-explorer.flare.network/address/0xe60745669C54b66F67ae85Ce031D4bDED4311163)
  - Registered TEE address: `0xEe4975C290FBF46757A1D90F02c3CF555163556E`
  - Production smoke test **12/12 passing** (`frontend/e2e/e2e-coston2.mjs`): real FTSO prices (`price_source="coston2-ftso"`), attestation ecrecover == TEE address, on-chain status=Verified
- App: https://... *(pending hosting)*
- Video (4 min): https://... *(pending recording)*
- Contracts (Flare Mainnet): *(not deployed)*

## Roadmap
1. **Q3 2026**: real GCP Confidential Space vTPM attestation; wallet
   auto-import of holdings; FAssets (FXRP) analysis
2. **Q4 2026**: DAO treasury mode with multi-sig reporting
3. **2027**: apply for a Flare ecosystem grant; open the TEE analysis API to
   other builders
