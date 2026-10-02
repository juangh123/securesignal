"""AWS Nitro Enclaves NSM attestation support."""

import base64
import hashlib
import os
import subprocess

DEFAULT_HELPER_PATH = "/usr/local/bin/nsm-attest"
NSM_DEVICE_PATH = "/dev/nsm"


class NsmAttestationError(RuntimeError):
    pass


def helper_path() -> str:
    return os.environ.get("AWS_NSM_HELPER", DEFAULT_HELPER_PATH)


def ensure_nitro_runtime() -> None:
    if not os.path.exists(NSM_DEVICE_PATH):
        raise NsmAttestationError(
            f"missing {NSM_DEVICE_PATH}; the service is not running inside "
            "an AWS Nitro Enclave"
        )
    if not os.path.exists(helper_path()):
        raise NsmAttestationError(f"missing NSM helper: {helper_path()}")


def fetch_attestation_document(
    *,
    nonce: bytes,
    user_data: bytes,
    public_key: bytes | None = None,
    timeout: float = 15.0,
) -> bytes:
    ensure_nitro_runtime()
    env = os.environ.copy()
    env["NSM_NONCE_HEX"] = nonce.hex()
    env["NSM_USER_DATA_HEX"] = user_data.hex()
    if public_key is not None:
        env["NSM_PUBLIC_KEY_HEX"] = public_key.hex()

    try:
        completed = subprocess.run(
            [helper_path()],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NsmAttestationError(f"failed to run NSM helper: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise NsmAttestationError(
            f"NSM helper exited {completed.returncode}: {detail[:500]}"
        )
    if not completed.stdout:
        raise NsmAttestationError("NSM helper returned an empty document")
    return completed.stdout


def attestation_fields(
    *,
    nonce: bytes,
    user_data: bytes,
    public_key: bytes | None = None,
) -> dict:
    document = fetch_attestation_document(
        nonce=nonce,
        user_data=user_data,
        public_key=public_key,
    )
    fields = {
        "nsm_document": base64.b64encode(document).decode("ascii"),
        "nsm_document_sha256": hashlib.sha256(document).hexdigest(),
        "nsm_nonce": nonce.hex(),
        "nsm_user_data": user_data.hex(),
    }
    for pcr in range(0, 9):
        value = os.environ.get(f"AWS_NITRO_PCR{pcr}", "").strip()
        if value:
            fields[f"pcr{pcr}"] = value.lower()
    return fields
