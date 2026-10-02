# AWS Nitro Enclaves attestation evidence (task 22)

Final Coston2 evidence for the **2.7.0** AWS Nitro Enclave with **KMS key
release** enabled. The runtime bundle contains only KMS ciphertexts; the
enclave obtains an NSM attestation bound to an ephemeral RSA key and uses
`kms:Decrypt` through the parent proxy to release the TEE and relayer keys.
The parent never sees the plaintext.

## Result

| Item | Value |
|---|---|
| Coston2 task | `22` |
| On-chain status | `Verified` |
| `requestAnalysis` tx | `0x09fa733083965ef579aea8e0a9b9da08e0b570c49ebe690662e670bf2a54d13a` |
| `ResultSubmitted` tx | `0xf25433a4e57611271379c429c63455fcbe319f0df08ccd3b8e42a950a70d8ba8` |
| Result hash | `0x3f9f3ff84fba197cf2c568b6adbab696b10ba3cb49e2fa6b03095dc9ba9fd613` |
| TEE address | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |
| Dedicated relayer | `0x0A3452C5B96396F186bD2d7ed793F8701A88fF72` (gas only; key released by KMS) |
| Enclave ID | `i-0ac0b18a850a8e334-enc01a0fd9b6a175d0e` |
| Service version | `2.7.0` |
| KMS key | `arn:aws:kms:us-east-1:615854521686:key/9206fce2-2bbc-42d9-95c4-8b8958213897` |
| Analysis mode | `rule-fallback` (the enclave intentionally has no LLM key) |
| PCR0 | `f081c1daa049abc23db7b64f82d674d8d3d52230e4526d7916dc4e49ee073453a3856b678db227a9e6591fceb8a61212` |
| PCR1 | `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493` |
| PCR2 | `f6e06e398c9fbfe2f203c1991ef62b1ee0666245d9750a9b7c360cbe2be9a7a6a264e521f6e6a95acbbf33367fe99a36` |
| Attestation SHA-256 | `109e5962a67bd4eece3160cfb7a773e46a6b27a8e021e912dfb95bfafe271475` |
| On-chain commitment | `keccak256(PCR0)` = `0xc9ff301894c99edbab0f2e673c0e7363c8de67b481f7466fc43a0333207a7331` (`rotateTeeKey` tx `0x915bf9033f65700edd341aeeb86a59bf604be58515e8a8f0502482d955288935`) |

## Files

- `aws-nitro-attestation-22.json` — decrypted result, TEE signature, raw
  base64 NSM document, request tx, relayer tx, on-chain status, and the full
  12/12 assertion list from `frontend/e2e/e2e-coston2.mjs`.
- `aws-nitro-attestation-22-verification.json` — relying-party verification
  output: pinned AWS Nitro root chain, ES384 signature, nonce, user data,
  ECIES public key, and PCR0.

## Reproduce

From the repository root:

```powershell
python tee-service\tools\verify_aws_nitro_attestation.py `
  --nonce-hex ec49a7b0f19cd5ba047d2ccb8ef20c5642fe8880d7036ae23378d4d3eeab903f `
  --user-data-hex 00000000000000000000000000000000000000000000000000000000000000163f9f3ff84fba197cf2c568b6adbab696b10ba3cb49e2fa6b03095dc9ba9fd613 `
  --public-key-hex 04088c6f6e685b84d396521b59d8b8ff794f4d6a27d47d487b716eced258fa76644e36bee0f46525f9920c9b6dd9f9ef1773d6aff610b0f944d29b0624f4cc10b6 `
  --pcr0 f081c1daa049abc23db7b64f82d674d8d3d52230e4526d7916dc4e49ee073453a3856b678db227a9e6591fceb8a61212 `
  --now-utc 2026-10-02T17:17:33Z `
  --max-age-seconds 60 `
  "<base64 nsm_document from aws-nitro-attestation-22.json>"
```

`--now-utc` pins the documented issuance instant because AWS NSM leaf
certificates are only valid for a few hours; the `--max-age-seconds 60` window
is then applied at that instant. Every cryptographic binding is still enforced
by the command, and live verification keeps the normal wall-clock behaviour.
