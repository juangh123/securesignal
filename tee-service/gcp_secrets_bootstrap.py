"""Load application secrets from Secret Manager before starting uvicorn.

Confidential Space includes values passed with ``tee-env-*`` in the signed
attestation JWT. Application private keys therefore must not be passed that way.
Instead, the VM receives only non-secret Secret Manager resource names and this
bootstrap resolves their values with the workload service account before
starting the service.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

METADATA_BASE = "http://metadata.google.internal/computeMetadata/v1"
METADATA_HEADERS = {"Metadata-Flavor": "Google"}

SECRET_ENV_NAMES = {
    "TEE_PRIVATE_KEY": "GCP_SECRET_TEE_PRIVATE_KEY",
    "PRIVATE_KEY": "GCP_SECRET_PRIVATE_KEY",
    "LLM_API_KEY": "GCP_SECRET_LLM_API_KEY",
}


def _metadata_text(path: str, timeout: float = 5.0) -> str:
    request = urllib.request.Request(
        f"{METADATA_BASE}/{path.lstrip('/')}",
        headers=METADATA_HEADERS,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8").strip()


def _metadata_token() -> str:
    payload = json.loads(
        _metadata_text("instance/service-accounts/default/token")
    )
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("metadata server returned no access token")
    return token


def _project_id() -> str:
    configured = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    return configured or _metadata_text("project/project-id")


def _secret_value(project_id: str, secret_name: str, access_token: str) -> str:
    encoded_name = urllib.parse.quote(secret_name, safe="")
    url = (
        "https://secretmanager.googleapis.com/v1/projects/"
        f"{urllib.parse.quote(project_id, safe='')}/secrets/{encoded_name}"
        "/versions/latest:access"
    )
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(request, timeout=10.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    encoded = payload.get("payload", {}).get("data")
    if not encoded:
        raise RuntimeError(f"Secret Manager returned no payload for {secret_name}")
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"secret {secret_name} is not valid UTF-8") from exc


def load_secrets() -> list[str]:
    mapping = {
        target: os.environ.get(source, "").strip()
        for target, source in SECRET_ENV_NAMES.items()
    }
    requested = {target: name for target, name in mapping.items() if name}
    enabled = os.environ.get("GCP_SECRETS_ENABLED", "").strip() == "1"
    if not requested:
        if enabled:
            raise RuntimeError(
                "GCP_SECRETS_ENABLED=1 but no GCP_SECRET_* mappings are set"
            )
        return []

    project_id = _project_id()
    access_token = _metadata_token()
    loaded: list[str] = []
    for target, secret_name in requested.items():
        os.environ[target] = _secret_value(project_id, secret_name, access_token)
        loaded.append(target)
    return loaded


def main() -> None:
    try:
        loaded = load_secrets()
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
        print(f"[gcp-secrets] fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if loaded:
        print(f"[gcp-secrets] loaded: {', '.join(loaded)}")

    port = os.environ.get("PORT", "8000")
    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
        ],
    )


if __name__ == "__main__":
    main()
