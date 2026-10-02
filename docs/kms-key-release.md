# KMS Key Release for the Nitro Enclave

## Status

Deployed and verified on 2026-10-03 as service version **2.7.0**. The running
non-debug enclave (`i-0ac0b18a850a8e334-enc01a0fd9b6a175d0e`, PCR0
`f081c1da…`) released the TEE and relayer keys through
`arn:aws:kms:us-east-1:615854521686:key/9206fce2-2bbc-42d9-95c4-8b8958213897`,
and Coston2 task 22 reached `Verified` with the sealed-bundle runtime. The
on-chain measurement commitment was rotated to `keccak256(PCR0)` =
`0xc9ff3018…` (`rotateTeeKey` tx `0x915bf903…`). Evidence:
`deliverables/aws-nitro-attestation-22.json`.

## Problem

The first AWS deployment delivered `TEE_PRIVATE_KEY` and the relayer key to the
enclave in a plaintext runtime bundle. The parent instance fetched that bundle
from Secrets Manager and served it over vsock, so the parent (outside the
enclave trust boundary) could read the TEE key. The parent could also change
the on-chain TEE key if it held the contract-owner key.

The relayer key is now a dedicated gas-only account, but the TEE key still must
not be readable by the parent.

## Design

The runtime bundle contains only KMS-sealed ciphertexts:

```
KMS_KEY_RELEASE=1
KMS_KEY_ID=<kms key arn>
KMS_REGION=us-east-1
KMS_VSOCK_PORT=8600
TEE_PRIVATE_KEY_CIPHERTEXT=<base64 kms ciphertext>
PRIVATE_KEY_CIPHERTEXT=<base64 kms ciphertext>
```

At startup the enclave:

1. Generates an ephemeral RSA-2048 key pair.
2. Asks the NSM helper for an attestation document whose `public_key` is the
   DER-encoded RSA public key and whose `user_data` is the KMS key ARN.
3. Sends the attestation document and the sealed ciphertext to the parent's
   KMS proxy over vsock port `8600`.
4. The parent calls `kms:Decrypt` with
   `Recipient={KeyEncryptionAlgorithm: RSAES_OAEP_SHA_256, AttestationDocument: ...}`.
   The KMS key policy only allows `kms:Decrypt` when the document's PCR0, PCR1,
   and PCR2 match the measured EIF.
5. KMS encrypts the released plaintext to the RSA public key from the
   attestation document. The enclave decrypts it locally with RSA-OAEP-SHA256.

The parent sees only ciphertext and can never read the released key.

## Key policy

The key policy is the security boundary:

- the deploy identity may administer the key and call `kms:Encrypt`;
- the parent EC2 role may call `kms:Decrypt` only when
  `kms:RecipientAttestation:PCR0`, `PCR1`, and `PCR2` match the current EIF
  measurements.

The deploy script updates the policy on every rebuild because a source change
changes PCR0.

## Two-phase deployment

The PCR values are only known after the EIF is built, so the deploy script uses
a two-phase flow:

1. The parent instance builds the EIF and uploads `PCR0/PCR1/PCR2` to the
   staging S3 bucket, then waits for the sealed bundle.
2. The local deploy script creates or reuses the KMS key, writes the
   PCR-conditioned key policy, encrypts the local key material with
   `kms:Encrypt`, uploads the sealed bundle to S3, and waits for the enclave
   health endpoint (checked via SSM).

The plaintext key material never leaves the operator machine; the parent and
the enclave only handle KMS ciphertext and attestation-bound KMS responses.

## Rollback

`deploy/aws/deploy-nitro-enclaves.ps1 -DisableKmsKeyRelease` keeps the old
plaintext-bundle path for rollback and debugging. Do not use it for a
production deployment.
