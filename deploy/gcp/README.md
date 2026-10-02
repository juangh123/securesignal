# GCP Confidential Space deployment

This directory contains the production path for running `tee-service` in a real
GCP Confidential Space VM with a Google-signed attestation token.

## What the deployment does

1. Enables Artifact Registry, Cloud Build, Compute Engine, Confidential
   Computing, and Secret Manager APIs.
2. Builds `tee-service` as a `linux/amd64` image in Cloud Build and records the
   resulting image digest.
3. Stores `TEE_PRIVATE_KEY`, the Coston2 relayer key, and optional
   `LLM_API_KEY` in Secret Manager. Secret values are never passed through VM
   metadata.
4. Starts the workload with non-secret secret *names*. At startup,
   `gcp_secrets_bootstrap.py` resolves the values through the VM service account
   and then starts uvicorn.
5. Boots a Confidential Space image with
   `tee-image-reference=<image>@sha256:<digest>` and `ENV=prod`.

In production mode the service requests an OIDC attestation JWT over
`/run/container_launcher/teeserver.sock`, binds each result to a nonce, and
returns the signed JWT in the attestation JSON. It refuses to start or return a
`gcp-confidential-space` token when the launcher socket is unavailable.

## Prerequisites

- Google Cloud SDK with an authenticated account:

  ```powershell
  gcloud auth login
  gcloud config set project PROJECT_ID
  ```

- A project with billing enabled.
- `tee-service/.env` containing `TEE_PRIVATE_KEY`, `PRIVATE_KEY`, and
  optionally `LLM_API_KEY`. The file is gitignored.

## Deploy

From the repository root:

```powershell
.\deploy\gcp\deploy-confidential-space.ps1 -ProjectId PROJECT_ID
```

Useful optional parameters:

```powershell
.\deploy\gcp\deploy-confidential-space.ps1 `
  -ProjectId PROJECT_ID `
  -Region us-central1 `
  -Zone us-central1-a `
  -FrontendOrigins "https://securesignal.vercel.app,http://localhost:3000"
```

The script is idempotent: an existing VM is stopped, its image metadata is
updated to the new digest, and it is started again.

## Verify the real attestation

1. Wait for the service:

   ```powershell
   Invoke-RestMethod http://PUBLIC_IP:8000/health
   ```

   `attestation_mode` must be `gcp-confidential-space`.

2. Run a real Coston2 task and inspect the `attestation` field. In production it
   contains a Google-signed `jwt`, `attestation_audience`,
   `attestation_nonce`, and the measured `sha256:` image digest.

3. Verify the JWT with Google's OIDC discovery endpoint:

   `https://confidentialcomputing.googleapis.com/.well-known/openid-configuration`

   Check `iss`, `aud`, `exp`, `swname == "CONFIDENTIAL_SPACE"`, `eat_nonce`,
   `dbgstat`, and `submods.container.image_digest`.

4. Convert the `sha256:<hex>` image digest to `bytes32` and register it with
   `contracts/scripts/setup-tee.ts`. The script accepts either
   `TEE_IMAGE_DIGEST=sha256:<hex>` or `TEE_IMAGE_DIGEST=0x<64 hex>`.

## Security notes

- The private keys are still injected by Secret Manager and exist in process
  memory; this deployment does not use Confidential Space key release or KMS
  sealing. Treat the service account and project as part of the trust boundary.
- Port 8000 is exposed directly for the first deployment iteration. Put the VM
  behind an HTTPS load balancer or authenticated proxy before using it from a
  production frontend.
- GCP's attestation JWT exposes the workload's `container.env` claims. Do not
  add secrets with `tee-env-*`; add secret names only.
