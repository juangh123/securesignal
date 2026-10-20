# Flare Submission Release Gate

This repository targets **Flare Summer Signal, Bounty 2: Confidential Compute Apps**.
The canonical public submission is DoraHacks BUIDL
[`47690`](https://dorahacks.io/build/47690).

## Release Contract

- Network: Flare Coston2, chain ID `114`.
- Production analysis endpoint: `https://d1tubqcwiwwev5.cloudfront.net`.
- Production attestation mode: `aws-nitro-enclaves`.
- Public demo endpoint: `https://securesignal-tee.onrender.com` (`dev-simulated`).
- Frontend: `https://securesignal.vercel.app`.
- Demo video: `https://youtu.be/1V5yuxIENvc`.

The repository contains a historical BLI plan under `legal-hack/`. It is excluded
from the Flare submission narrative and must not be used to describe this BUIDL.

## One-Command Gate

Run from the repository root:

```bash
node tools/flare-release-check.mjs
```

Set `ALLOW_DIRTY=1` only when checking an uncommitted working tree. A release
candidate should pass with a clean working tree.

The gate checks required evidence, shared contract addresses, the public
frontend, the real TEE health contract, on-chain registry state, relayer balance,
and the two demo video links. It performs no transactions and writes no files.

## Dependency Posture

CI enforces `npm audit --omit=dev --audit-level=high` for both frontend and
contracts. The Flare release has zero high/critical production dependency
findings. Historical advisories below that threshold, and development-only
Hardhat/WalletConnect advisories, remain visible in full `npm audit` output but
do not block the release.

## Trust Boundary

The Flare registry verifies the registered TEE result signature on-chain.
Nitro certificate-chain and PCR validation are performed by the off-chain
attestation verifier. The registry's `expectedImageDigest` is committed as
`keccak256(PCR0)`, but the currently deployed legacy registry does not execute
Nitro attestation inside the EVM. This boundary must remain explicit in
submission copy and judge Q&A.

## Before Archiving

1. Ensure `main` CI is green.
2. Ensure `git status --short` is clean.
3. Run `node tools/flare-release-check.mjs`.
4. Confirm the DoraHacks Flare BUIDL still points to the live app, source, and video.
5. Archive the verification output together with the final commit SHA.
