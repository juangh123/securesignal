# AWS Nitro Enclaves deployment

SecureSignal can run its FastAPI analysis engine inside an AWS Nitro Enclave.
The enclave obtains a signed NSM attestation document for each task/result pair;
the existing EIP-191 signature is still used by the Flare registry contract.

## Current verified deployment

| Item | Value |
|---|---|
| Region / instance | `us-east-1` / `i-0ded6c8853f4cb1ae` |
| Enclave state | `RUNNING`, `Flags: NONE` (not debug mode) |
| Enclave ID | `i-0ded6c8853f4cb1ae-enc1a0fcc974aaa8a9` |
| API | `https://d1tubqcwiwwev5.cloudfront.net` (CloudFront HTTPS; origin restricted to CloudFront + operator IP) |
| Attestation mode | `aws-nitro-enclaves` |
| Service version | `2.6.0` (inputDataHash binding, dedicated relayer, serialized nonce, batched FTSO, bounded LLM budget, security headers) |
| Dedicated relayer | `0x0A3452C5B96396F186bD2d7ed793F8701A88fF72` (gas only; not the contract owner) |
| PCR0 | `d114b727e0bd5856b3a9d6c5295a498c786d2309e7dc960d215135d2389063e4ef643b00483de37c74e0f877121d701f` |
| PCR1 | `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493` |
| PCR2 | `c11ae9c267d8614207307f7b4da9371b98b8057c616d2702972809e46817a7ca2b5e0936ee91d49534b9a1a794cb7d07` |
| On-chain commitment | `keccak256(PCR0)` = `0x92ba6b1956182011f2cf46fa032d9c45b64f779e66697775439e21b93614be0f` (`rotateTeeKey` tx `0xe353838836c44053aa3372110b56db307ac48016bbe9637e66f90996c7db4cde`; the contract stores the commitment but still verifies only the EIP-191 signature) |
| Verification | Coston2 production smoke test `12/12` passed; task 21 `Verified`; evidence in `deliverables/aws-nitro-attestation-21.json` + independent NSM verification |

> **Build provenance (2026-10-02):** this non-debug EIF was built from the
> 2.6.0 source (on-chain `inputDataHash` binding, dedicated gas-only relayer,
> serialized nonce allocation, batched FTSO reads, bounded LLM budget, API
> security headers). The TEE key is unchanged, so the on-chain `teeAddress` and
> `activeTeePublicKey` remained valid across the rebuild. Any future source
> change requires a new EIF, a new PCR0, and a fresh verification before this
> table is updated.

The temporary IAM access keys used for deployment were deleted. The dedicated
IAM user and least-privilege policy remain for future rotations.

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
2. Builds and pushes the `linux/amd64` image from the parent instance.
3. Stores runtime environment and secrets in AWS Secrets Manager.
4. Creates a least-privilege EC2 role, security group, and AL2023 parent
   instance.
5. Uses `nitro-cli build-enclave` to create the EIF and records PCR0/PCR1/PCR2.
6. Starts the enclave without `--debug-mode`, then proxies TCP port 8000 to
   vsock port 8000.
7. Serves the runtime secret bundle to the enclave over vsock port 8001.

## Publish the enclave over HTTPS

The enclave service itself only serves HTTP; CloudFront can provide an HTTPS
endpoint without a custom domain. After the instance is healthy:

```powershell
.\deploy\aws\expose-https-cloudfront.ps1 `
  -InstanceId i-0ded6c8853f4cb1ae
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
bundle over vsock. The parent is outside the enclave trust boundary and can
deny service, so this first deployment does not yet use KMS key release. For a
stronger key lifecycle, move `TEE_PRIVATE_KEY` into KMS and gate `Decrypt` on
PCR0/PCR3/PCR8.

## Cost and teardown

The ongoing cost is dominated by the always-on `m5.xlarge` parent instance.
CloudFront adds a low, traffic-based charge; the S3 staging bucket, ECR image,
Secrets Manager secret, and IAM resources cost little or nothing at rest.

Stopping the instance takes the enclave API offline and stops compute billing
(the EBS volume and other resources remain):

```powershell
aws ec2 stop-instances --region us-east-1 --instance-ids i-0ded6c8853f4cb1ae
```

Terminating it is permanent; the EIF, PCR measurements, and runtime secrets
would need to be rebuilt from this repository:

```powershell
aws ec2 terminate-instances --region us-east-1 --instance-ids i-0ded6c8853f4cb1ae
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
