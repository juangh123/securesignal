"""Load runtime configuration from the parent over vsock, then start uvicorn."""

import json
import os
import socket
import sys
import time

from aws_kms_release import KmsReleaseError, release_bundle

PARENT_CID = 3
SECRET_PORT = int(os.environ.get("AWS_NITRO_SECRET_PORT", "8001"))
MAX_BUNDLE_BYTES = 256 * 1024
KMS_BOOTSTRAP_TIMEOUT_SECONDS = int(
    os.environ.get("KMS_BOOTSTRAP_TIMEOUT_SECONDS", "1200")
)

# Environment keys the parent may inject into the enclave process. Anything
# else is dropped with a warning, and interpreter/runtime hijacking keys are
# rejected outright, so a compromised or buggy parent cannot rewrite how the
# service starts.
ALLOWED_ENV_KEYS = frozenset(
    {
        "ENV",
        "PORT",
        "RPC_URL",
        "ALLOWED_ORIGINS",
        "ANALYSIS_OFFLINE",
        "ANALYZE_REQUIRE_ONCHAIN_TASK",
        "ANALYZE_TASK_MAX_AGE_SECONDS",
        "ANALYZE_RESULT_CACHE_SECONDS",
        "ANALYZE_MAX_CONCURRENCY",
        "MAX_REQUEST_BODY_BYTES",
        "ONCHAIN_RECEIPT_TIMEOUT",
        "FTSO_MAX_FEED_AGE_SECONDS",
        "TEE_PRIVATE_KEY",
        "TEE_PRIVATE_KEY_CIPHERTEXT",
        "PRIVATE_KEY",
        "PRIVATE_KEY_CIPHERTEXT",
        "LLM_API_KEY",
        "LLM_API_KEY_CIPHERTEXT",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TIMEOUT",
        "LLM_TOTAL_BUDGET",
        "TEE_IMAGE_DIGEST",
        "ATTESTATION_PROVIDER",
        "ATTESTATION_SCHEME",
        "KMS_KEY_RELEASE",
        "KMS_KEY_ID",
        "KMS_REGION",
        "KMS_VSOCK_PORT",
        "KMS_BOOTSTRAP_PENDING",
        "KMS_BOOTSTRAP_TIMEOUT_SECONDS",
        "AWS_NITRO_ENCLAVES",
        "AWS_NSM_HELPER",
        "AWS_NITRO_SECRET_PORT",
        "AWS_NITRO_PARENT_CID",
        "RPC_VSOCK_PORT",
        "ENCLAVE_PORT",
        "APP_DIR",
        "APP_SOCKET",
        "GCP_SECRETS_ENABLED",
        "GOOGLE_CLOUD_PROJECT",
    }
)
ALLOWED_ENV_PREFIXES = ("AWS_NITRO_", "GCP_SECRET_", "GCP_ATTESTATION_")
DENIED_ENV_KEYS = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "NODE_OPTIONS",
        "BASH_ENV",
        "SHELLOPTS",
        "IFS",
    }
)


def apply_bundle(bundle: dict) -> list[str]:
    """Copy an allowlisted runtime bundle into ``os.environ``.

    Returns the skipped (unknown) keys so the caller can warn about them.
    Raises ValueError for non-string entries or reserved interpreter keys.
    """
    skipped: list[str] = []
    for key, value in bundle.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("bundle keys and values must be strings")
        if key in DENIED_ENV_KEYS:
            raise ValueError(f"refusing to inject reserved environment key {key}")
        if key in ALLOWED_ENV_KEYS or key.startswith(ALLOWED_ENV_PREFIXES):
            os.environ[key] = value
        else:
            skipped.append(key)
    return skipped


def read_bundle() -> dict:
    last_error: Exception | None = None
    for _ in range(30):
        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        try:
            sock.settimeout(3.0)
            sock.connect((PARENT_CID, SECRET_PORT))
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BUNDLE_BYTES:
                    raise RuntimeError("secret bundle is too large")
                chunks.append(chunk)
            if not chunks:
                raise RuntimeError("parent returned an empty secret bundle")
            payload = json.loads(b"".join(chunks).decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("secret bundle must be a JSON object")
            return payload
        except (OSError, ValueError, RuntimeError) as exc:
            last_error = exc
            time.sleep(1.0)
        finally:
            sock.close()
    raise RuntimeError(f"failed to read configuration from the parent: {last_error}")


def wait_for_kms_bundle(timeout_seconds: int = KMS_BOOTSTRAP_TIMEOUT_SECONDS) -> dict:
    """Wait until the deploy script replaces the pending placeholder bundle."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        bundle = read_bundle()
        if str(bundle.get("KMS_BOOTSTRAP_PENDING", "")).strip() != "1":
            return bundle
        time.sleep(5.0)
    raise RuntimeError("timed out waiting for the KMS key-release bundle")


def main() -> None:
    try:
        bundle = read_bundle()
        if str(bundle.get("KMS_BOOTSTRAP_PENDING", "")).strip() == "1":
            bundle = wait_for_kms_bundle()
        try:
            port = int(bundle.get("KMS_VSOCK_PORT", "8600"))
        except (TypeError, ValueError):
            port = 8600
        bundle = release_bundle(bundle, port=port)
    except KmsReleaseError as exc:
        print(f"[aws-nitro] fatal: KMS key release failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except RuntimeError as exc:
        print(f"[aws-nitro] fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)

    try:
        skipped = apply_bundle(bundle)
    except ValueError as exc:
        print(f"[aws-nitro] fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
    for key in skipped:
        print(f"[aws-nitro] ignoring unknown bundle key: {key}", file=sys.stderr)
    os.environ.setdefault("ATTESTATION_PROVIDER", "aws-nitro-enclaves")
    os.environ.setdefault("ENV", "prod")

    app_dir = os.environ.get("APP_DIR", "/app")
    if os.path.isdir(app_dir):
        os.chdir(app_dir)

    socket_path = os.environ.get("APP_SOCKET", "/tmp/securesignal.sock")
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass
    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--uds",
            socket_path,
        ],
    )


if __name__ == "__main__":
    main()
