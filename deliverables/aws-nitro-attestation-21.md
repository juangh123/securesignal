# AWS Nitro Enclaves attestation evidence (task 21)

Final Coston2 evidence for the **2.6.0** AWS Nitro Enclave. This build adds
on-chain `inputDataHash` binding, a dedicated gas-only relayer account, and
serialized relayer nonce allocation. The TEE key is unchanged, so the on-chain
`teeAddress` and `activeTeePublicKey` remain valid; the EIF changed, so PCR0 is
new.

## Result

| Item | Value |
|---|---|
| Coston2 task | `21` |
| On-chain status | `Verified` |
| `requestAnalysis` tx | `0xd9a13658c78380d0f3ad0611a25a38f48aaa369760437d9dec64cb00a703fe4b` |
| `ResultSubmitted` tx | `0x9c63ae3700b969deb9bf106402fb6b6e49b73cfea88b3a88bd3fcfc010653a62` |
| Result hash | `0xdbb8429e1406c486303f89b81e6b4d3f2f2fd5f10923cda3c136641ce9df3023` |
| TEE address | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |
| Dedicated relayer | `0x0A3452C5B96396F186bD2d7ed793F8701A88fF72` (gas only; not the contract owner) |
| Enclave ID | `i-0ded6c8853f4cb1ae-enc1a0fcc974aaa8a9` |
| Service version | `2.6.0` |
| Analysis mode | `rule-fallback` (the enclave intentionally has no LLM key) |
| PCR0 | `d114b727e0bd5856b3a9d6c5295a498c786d2309e7dc960d215135d2389063e4ef643b00483de37c74e0f877121d701f` |
| PCR1 | `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493` |
| PCR2 | `c11ae9c267d8614207307f7b4da9371b98b8057c616d2702972809e46817a7ca2b5e0936ee91d49534b9a1a794cb7d07` |
| Attestation SHA-256 | `0cbd10a9be7918ce5460c1f15f12600cfe2bd26b189ff0c7a115a4bfa995220b` |
| On-chain commitment | `keccak256(PCR0)` = `0x92ba6b1956182011f2cf46fa032d9c45b64f779e66697775439e21b93614be0f` (`rotateTeeKey` tx `0xe353838836c44053aa3372110b56db307ac48016bbe9637e66f90996c7db4cde`) |

## Files

- `aws-nitro-attestation-21.json` — decrypted result, TEE signature, raw
  base64 NSM document, request tx, relayer tx, on-chain status, and the full
  assertion list from `frontend/e2e/e2e-coston2.mjs` (12/12).
- `aws-nitro-attestation-21-verification.json` — relying-party verification
  output: pinned AWS Nitro root chain, ES384 signature, nonce, user data,
  ECIES public key, and PCR0.

## Reproduce

From the repository root:

```powershell
python tee-service\tools\verify_aws_nitro_attestation.py `
  --nonce-hex a5f894e274665c26521a6737a5e89afc84d17a05a3a490c9cf60f628aced6f4a `
  --user-data-hex 0000000000000000000000000000000000000000000000000000000000000015dbb8429e1406c486303f89b81e6b4d3f2f2fd5f10923cda3c136641ce9df3023 `
  --public-key-hex 04088c6f6e685b84d396521b59d8b8ff794f4d6a27d47d487b716eced258fa76644e36bee0f46525f9920c9b6dd9f9ef1773d6aff610b0f944d29b0624f4cc10b6 `
  --pcr0 d114b727e0bd5856b3a9d6c5295a498c786d2309e7dc960d215135d2389063e4ef643b00483de37c74e0f877121d701f `
  --max-age-seconds 31536000 `
  "<base64 nsm_document from aws-nitro-attestation-21.json>"
```

The `--max-age-seconds` override only relaxes the timestamp freshness check for
this archived document (the live default is 600 seconds); every cryptographic
binding is still enforced by the command.
