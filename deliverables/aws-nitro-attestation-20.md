# AWS Nitro Enclaves attestation evidence (task 20)

Final Coston2 evidence for the rebuilt **2.5.0** AWS Nitro Enclave. This build
contains the batched FTSO reader, the bounded LLM budget, and the API security
headers. The TEE key is unchanged, so the on-chain `teeAddress` and
`activeTeePublicKey` remain valid; the EIF changed, so PCR0 is new.

## Result

| Item | Value |
|---|---|
| Coston2 task | `20` |
| On-chain status | `Verified` |
| `requestAnalysis` tx | `0x56e384dc3e2040e0558579d01986bec08f5a632fa46d71c01f99051c85b4ecdd` |
| `ResultSubmitted` tx | `0xd7fea8b774b535c1fa61fb417645b624429aed70f699d7e13c27e666d2935a22` |
| Result hash | `0xdafec5abee3006d4dc8f56ff666a49a26180da5b5b3dfc1fd487a00039b99b3d` |
| TEE address | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |
| Enclave ID | `i-08c3255e1c96ae343-enc01a0fc912c2bd846` |
| Service version | `2.5.0` |
| Analysis mode | `rule-fallback` (the enclave intentionally has no LLM key) |
| PCR0 | `c126dc6db19cefcda5c0a412fecd692d5f12d801cf5ea5b262d954424a455cf25e6189ace615ad00be541d8295864279` |
| PCR1 | `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493` |
| PCR2 | `ee61bc92db0b07d247c054e0402bea829d6272d7d6427892de7ef678e367081f5ee21c2d3eefdbacf84c71ceaf677bfb` |
| Attestation SHA-256 | `3f6871821703e499f15f263a8ff2e75bd657df4bac41552db3ffb805a8b0f3be` |

## Files

- `aws-nitro-attestation-20.json` — decrypted result, TEE signature, raw
  base64 NSM document, request tx, relayer tx, on-chain status, and the full
  assertion list from `frontend/e2e/e2e-coston2.mjs` (12/12).
- `aws-nitro-attestation-20-verification.json` — relying-party verification
  output: pinned AWS Nitro root chain, ES384 signature, nonce, user data,
  ECIES public key, and PCR0.

## Reproduce

From the repository root:

```powershell
python tee-service\tools\verify_aws_nitro_attestation.py `
  --nonce-hex 95ad2bcf7f9245f517ce367d4d26f2e7495fd92d49f8b7fd0b0e9b77218de1fb `
  --user-data-hex 0000000000000000000000000000000000000000000000000000000000000014dafec5abee3006d4dc8f56ff666a49a26180da5b5b3dfc1fd487a00039b99b3d `
  --public-key-hex 04088c6f6e685b84d396521b59d8b8ff794f4d6a27d47d487b716eced258fa76644e36bee0f46525f9920c9b6dd9f9ef1773d6aff610b0f944d29b0624f4cc10b6 `
  --pcr0 c126dc6db19cefcda5c0a412fecd692d5f12d801cf5ea5b262d954424a455cf25e6189ace615ad00be541d8295864279 `
  --now-utc 2026-10-02T12:25:30Z `
  --max-age-seconds 60 `
  "<base64 nsm_document from aws-nitro-attestation-20.json>"
```

`--now-utc` pins the documented issuance instant because AWS NSM leaf
certificates are only valid for a few hours; the `--max-age-seconds 60` window
is then applied at that instant. Every cryptographic binding is still enforced
by the command, and live verification keeps the normal wall-clock behaviour.
