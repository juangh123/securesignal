"""AWS Nitro Enclaves NSM attestation support."""

import base64
import hashlib
import os
import subprocess

import cbor2

DEFAULT_HELPER_PATH = "/usr/local/bin/nsm-attest"
NSM_DEVICE_PATH = "/dev/nsm"
# COSE_Sign1 tag used by every NSM attestation document.
COSE_SIGN1_TAG = 18


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
    pcrs = _document_pcrs(
        document, nonce=nonce, user_data=user_data, public_key=public_key
    )
    fields = {
        "nsm_document": base64.b64encode(document).decode("ascii"),
        "nsm_document_sha256": hashlib.sha256(document).hexdigest(),
        "nsm_nonce": nonce.hex(),
        "nsm_user_data": user_data.hex(),
    }
    # PCR values come from the signed document itself, not from the parent's
    # environment: the env copy is only used as an expected-value cross-check.
    for index in sorted(pcrs):
        fields[f"pcr{index}"] = pcrs[index]
    for index in range(0, 9):
        expected = os.environ.get(f"AWS_NITRO_PCR{index}", "").strip().lower()
        if expected and pcrs.get(index) != expected:
            raise NsmAttestationError(
                f"AWS_NITRO_PCR{index} does not match the signed NSM document"
            )
    return fields


def _document_pcrs(
    document: bytes,
    *,
    nonce: bytes,
    user_data: bytes,
    public_key: bytes | None,
) -> dict[int, str]:
    """Extract the PCRs (and verify the request bindings) from an NSM document.

    The document is a COSE_Sign1 structure whose payload carries the PCR map
    together with the nonce/user_data/public_key the caller asked for. The
    full signature-chain check stays in tools/verify_aws_nitro_attestation.py;
    here we only reject documents that obviously do not belong to this request.
    """
    try:
        decoded = cbor2.loads(document)
    except Exception as exc:  # noqa: BLE001 - cbor2 raises several types
        raise NsmAttestationError(f"NSM document is not valid CBOR: {exc}") from exc
    if getattr(decoded, "tag", None) != COSE_SIGN1_TAG:
        raise NsmAttestationError("NSM document is not a COSE_Sign1 structure")
    try:
        body = getattr(decoded, "value", decoded)
        _protected, _unprotected, payload_bytes, _signature = body
        payload = cbor2.loads(payload_bytes)
        pcrs = {
            int(index): bytes(value).hex() for index, value in payload["pcrs"].items()
        }
    except Exception as exc:  # noqa: BLE001 - malformed CBOR shapes vary
        raise NsmAttestationError(f"NSM document payload is malformed: {exc}") from exc
    if not pcrs:
        raise NsmAttestationError("NSM document contains no PCR measurements")
    if payload.get("nonce") != nonce:
        raise NsmAttestationError("NSM document nonce does not match the request")
    if payload.get("user_data") != user_data:
        raise NsmAttestationError("NSM document user_data does not match the request")
    if public_key is not None and payload.get("public_key") != public_key:
        raise NsmAttestationError(
            "NSM document public_key does not match the request"
        )
    return pcrs
