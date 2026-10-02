# AWS Nitro Enclaves attestation evidence (task 18)

This directory contains the final Coston2 evidence for the verified AWS Nitro
Enclaves deployment.

## Result

| Item | Value |
|---|---|
| Coston2 task | `18` |
| On-chain status | `Verified` |
| `requestAnalysis` tx | `0xb05f1b428d327aad263795267120423012740531c0d643aa509a895372bbf9eb` |
| `ResultSubmitted` tx | `0xb92493184d1c802128c86caeab4b909b057928fe5203658fa044a89eda398f04` |
| Result hash | `0x18779e4aaf8051e5404a642de822de041a79198b0f34330f692ed037df129c42` |
| TEE address | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |
| PCR0 | `853316351f15ac48236389561075a8b3d4756d20ac14c77df05a1e8727fdf1ab448422fc91f2d22001bff6ff4562637b` |
| PCR1 | `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493` |
| Attestation SHA-256 | `7c88dedd9bb53097d516dc4b5dd08151d57185683fc0759c41a5802058e21df4` |

## Files

- `aws-nitro-attestation-18.json` — decrypted result, TEE signature, raw base64
  NSM document, request tx, relayer tx, and on-chain status.
- `aws-nitro-attestation-18-verification.json` — relying-party verification
  output: AWS root chain, ES384 signature, nonce, user data, ECIES public key,
  PCR0, and timestamp.

## Reproduce

From the repository root:

```powershell
python tee-service\tools\verify_aws_nitro_attestation.py `
  --nonce-hex ea520feb534a972e416a0575512f98783a016a46bcc238c3168e841ab38a36dc `
  --user-data-hex 000000000000000000000000000000000000000000000000000000000000001218779e4aaf8051e5404a642de822de041a79198b0f34330f692ed037df129c42 `
  --public-key-hex 04088c6f6e685b84d396521b59d8b8ff794f4d6a27d47d487b716eced258fa76644e36bee0f46525f9920c9b6dd9f9ef1773d6aff610b0f944d29b0624f4cc10b6 `
  --pcr0 853316351f15ac48236389561075a8b3d4756d20ac14c77df05a1e8727fdf1ab448422fc91f2d22001bff6ff4562637b `
  "<base64 nsm_document from aws-nitro-attestation-18.json>"
```

The verifier pins the AWS Nitro Enclaves root certificate, checks the COSE_Sign1
ES384 signature and certificate chain, then compares the nonce, user data,
ECIES public key, PCR0, and freshness window.
