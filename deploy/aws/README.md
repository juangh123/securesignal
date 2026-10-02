# AWS Nitro Enclaves deployment

SecureSignal can run its FastAPI analysis engine inside an AWS Nitro Enclave.
The enclave obtains a signed NSM attestation document for each task/result pair;
the existing EIP-191 signature is still used by the Flare registry contract.

## Current verified deployment

| Item | Value |
|---|---|
| Region / instance | `us-east-1` / `i-0ac0b18a850a8e334` |
| Enclave state | `RUNNING`, `Flags: NONE` (not debug mode) |
| Enclave ID | `i-0ac0b18a850a8e334-enc01a0fd9b6a175d0e` |
| API | `https://d1tubqcwiwwev5.cloudfront.net` (CloudFront HTTPS; origin restricted to CloudFront + operator IP) |
| Attestation mode | `aws-nitro-enclaves` |
| Service version | `2.7.0` (PCR-conditioned KMS key release, inputDataHash binding, dedicated relayer, serialized nonce, batched FTSO, bounded LLM budget, security headers) |
| Dedicated relayer | `0x0A3452C5B96396F186bD2d7ed793F8701A88fF72` (gas only; not the contract owner) |
| PCR0 | `f081c1daa049abc23db7b64f82d674d8d3d52230e4526d7916dc4e49ee073453a3856b678db227a9e6591fceb8a61212` |
| PCR1 | `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493` |
| PCR2 | `f6e06e398c9fbfe2f203c1991ef62b1ee0666245d9750a9b7c360cbe2be9a7a6a264e521f6e6a95acbbf33367fe99a36` |
| KMS key | `arn:aws:kms:us-east-1:615854521686:key/9206fce2-2bbc-42d9-95c4-8b8958213897` (`Decrypt` restricted to enclave PCR0/PCR1/PCR2 via `kms:RecipientAttestation:*`) |
| On-chain commitment | `keccak256(PCR0)` = `0xc9ff301894c99edbab0f2e673c0e7363c8de67b481f7466fc43a0333207a7331` (`rotateTeeKey` tx `0x915bf9033f65700edd341aeeb86a59bf604be58515e8a8f0502482d955288935`; the contract stores the commitment but still verifies only the EIP-191 signature) |
| Verification | Coston2 production smoke test `12/12` passed; task 22 `Verified` (`requestAnalysis` `0x09fa733083965ef579aea8e0a9b9da08e0b570c49ebe690662e670bf2a54d13a`, `ResultSubmitted` `0xf25433a4e57611271379c429c63455fcbe319f0df08ccd3b8e42a950a70d8ba8`); evidence in `deliverables/aws-nitro-attestation-22.json` + independent NSM verification |

> **Build provenance (2026-10-03):** this non-debug EIF was rebuilt from the
> 2.7.0 source, which adds PCR-conditioned KMS key release on top of the 2.6.0
> hardening (on-chain `inputDataHash` binding, dedicated gas-only relayer,
> serialized nonce allocation, batched FTSO reads, bounded LLM budget, API
> security headers). The parent receives only KMS ciphertext and
> `kms:Decrypt` is allowed only when the enclave attestation matches
> PCR0/PCR1/PCR2 below, so the TEE key and the dedicated relayer key are
> released inside the enclave. The on-chain `teeAddress` and
> `activeTeePublicKey` are unchanged across the rebuild. Any future source
> change requires a new EIF, a new PCR0, and a fresh verification before this
> table is updated.

The temporary IAM access key used for this deployment was revoked on
2026-10-03 and the local copy deleted. The dedicated IAM user and
least-privilege policy remain for future rotations. The deploy identity
intentionally cannot list or delete its own access keys, so temporary keys are
created and revoked with the admin profile (or the IAM console). The previous
2.6.0 instance `i-0ded6c8853f4cb1ae` was stopped on the same day; its EBS volume
is retained and it can be terminated once no longer needed for rollback.

## Why not use the root user

Do not create an access key for the AWS root user. Create a dedicated
deployment identity with MFA and programmatic credentials:

- Preferred: AWS IAM Identity Center user/profile.
- Alternative: a dedicated IAM user with MFA and
  `deploy/aws/deployment-policy.json` attached.

Follow [`iam-setup.md`](iam-setup.md), then configure the profile locally:

```powershell
aws configure sso
# or:
aws configure --profile securesignal-deploy
$env:AWS_PROFILE = "securesignal-deploy"
aws sts get-caller-identity
```

The sign-in identity must not be an `arn:aws:iam::...:root` principal.

## Deploy

Prerequisites:

- `tee-service/.env` containing `TEE_PRIVATE_KEY`, a dedicated `PRIVATE_KEY`
  relayer account (gas only — **never the contract owner key**), and optionally
  `LLM_API_KEY`.
- An IAM profile with the policy in this directory.

```powershell
.\deploy\aws\deploy-nitro-enclaves.ps1 `
  -Region us-east-1 `
  -ApiCidr "YOUR_PUBLIC_IP/32"
```

The script:

1. Packages `tee-service` and uploads it to a private staging S3 bucket.
2. Creates a least-privilege EC2 role, security group, and AL2023 parent
   instance.
3. On first boot the parent builds and pushes the `linux/amd64` image, runs
   `nitro-cli build-enclave`, records PCR0/PCR1/PCR2 in S3, and waits for the
   sealed runtime bundle.
4. The local script creates or reuses the KMS key, writes a policy that only
   allows `kms:Decrypt` when the enclave attestation matches those PCRs,
   encrypts the local keys with `kms:Encrypt`, and uploads the sealed bundle.
5. The parent serves the sealed bundle over vsock port 8001; the enclave
   performs the KMS key-release handshake and then starts without
   `--debug-mode`. TCP port 8000 is proxied to vsock port 8000.
6. `-DisableKmsKeyRelease` keeps the old plaintext-bundle path for rollback or
   debugging only.

If the local deploy script is interrupted after the PCRs have been recorded,
`.\deploy\aws\provision-kms.ps1 -Pcr0 <pcr0> -Pcr1 <pcr1> -Pcr2 <pcr2>` can
re-provision the KMS key policy and the sealed bundle for the already-running
instance (this is how the 2.7.0 deployment was completed after an IAM
propagation error).

See [`docs/kms-key-release.md`](../../docs/kms-key-release.md) for the threat
model and protocol details.

## Publish the enclave over HTTPS

The enclave service itself only serves HTTP; CloudFront can provide an HTTPS
endpoint without a custom domain. After the instance is healthy:

```powershell
.\deploy\aws\expose-https-cloudfront.ps1 `
  -InstanceId i-0ac0b18a850a8e334
```

The script:

1. Creates or reuses a CloudFront distribution with HTTPS viewer access.
2. Uses the EC2 public DNS name as the HTTP origin.
3. Adds the CloudFront origin-facing managed prefix list to the security group.
4. Prints the `https://<distribution>.cloudfront.net` endpoint.

> **Status (2026-10-02):** live. Distribution `E3I9PI0XZFXX88` serves
> `https://d1tubqcwiwwev5.cloudfront.net` with `redirect-to-https` and a
> 60-second origin read timeout. The origin security group accepts port 8000
> only from the CloudFront origin-facing managed prefix list (`pl-3b927c52`)
> plus the operator IP.

`GET /health` must report `attestation_mode="aws-nitro-enclaves"`. Each
`POST /analyze` response then contains:

- `nsm_document`: base64 COSE/CBOR attestation document
- `nsm_document_sha256`
- `nsm_nonce`, `nsm_user_data`
- `pcr0`, `pcr1`, `pcr2`

Verify an NSM document with:

```powershell
python tee-service\tools\verify_aws_nitro_attestation.py `
  --nonce-hex <nsm_nonce> `
  --user-data-hex <nsm_user_data> `
  --public-key-hex <TEE public key> `
  --pcr0 <pcr0> `
  <nsm_document>
```

## Trust boundary

The parent instance relays the encrypted HTTP body and the runtime secret
bundle over vsock and can still deny service. The running 2.7.0 deployment
uses KMS key release: the bundle contains only KMS ciphertexts and the
parent's `kms:Decrypt` permission is conditioned on the enclave attestation
PCR0/PCR1/PCR2, so the parent cannot read the TEE key or the dedicated relayer
key.

## Availability

The parent instance is still a single point of failure, but the runtime
recovers without operator action:

- `securesignal-enclave-watchdog.timer` runs every 30 seconds and restarts the
  enclave when `nitro-cli describe-enclaves` does not report `RUNNING`,
  reconnecting the vsock HTTP proxy with the same enclave CID. The enclave is
  started in the background by `nitro-cli`, not as a systemd process, so this
  watchdog is what brings it back after an enclave crash or a parent reboot.
- CloudWatch alarm `securesignal-instance-recover` (`StatusCheckFailed_System`)
  triggers the built-in EC2 recover action; `securesignal-instance-reboot`
  (`StatusCheckFailed_Instance`) reboots the instance, after which the watchdog
  restarts the enclave.
- The CloudFront origin uses the instance's public DNS name, so an
  operator-initiated stop/start can change the origin address. Re-run
  `expose-https-cloudfront.ps1` after a manual stop/start, or attach a stable
  address before stopping the instance.

## Cost and teardown

The ongoing cost is dominated by the always-on `m5.xlarge` parent instance.
CloudFront adds a low, traffic-based charge; the S3 staging bucket, ECR image,
Secrets Manager secret, and IAM resources cost little or nothing at rest.

Stopping the instance takes the enclave API offline and stops compute billing
(the EBS volume and other resources remain):

```powershell
aws ec2 stop-instances --region us-east-1 --instance-ids i-0ac0b18a850a8e334
```

Terminating it is permanent; the EIF, PCR measurements, and runtime secrets
would need to be rebuilt from this repository:

```powershell
aws ec2 terminate-instances --region us-east-1 --instance-ids i-0ac0b18a850a8e334
```

To remove the CloudFront distribution, disable it first, wait for the change to
deploy, then delete it (the AWS Console performs the same sequence):

```powershell
aws cloudfront get-distribution-config --id <DISTRIBUTION_ID>
# set Enabled=false in the returned DistributionConfig and keep the ETag
aws cloudfront update-distribution --id <DISTRIBUTION_ID> --if-match <ETAG> `
  --distribution-config file://disabled-config.json
aws cloudfront wait distribution-deployed --id <DISTRIBUTION_ID>
aws cloudfront delete-distribution --id <DISTRIBUTION_ID> --if-match <NEW_ETAG>
```
