"""Enclave-side AWS KMS key release.

The enclave generates an ephemeral RSA-2048 key, asks NSM for an attestation
document containing that public key, and sends the document plus the sealed
ciphertext to the parent KMS proxy over vsock. KMS validates the document
against the key policy's PCR conditions and encrypts the released plaintext to
the RSA key, so only this enclave can read it.
"""

import base64
import json
import os
import socket

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from attestation import aws_nsm

PARENT_CID = int(os.environ.get("AWS_NITRO_PARENT_CID", "3"))
DEFAULT_KMS_VSOCK_PORT = int(os.environ.get("KMS_VSOCK_PORT", "8600"))
MAX_RESPONSE_BYTES = 1024 * 1024
ALGORITHM = "RSAES_OAEP_SHA_256"
RELEASE_CONTEXT = {"app": "securesignal", "purpose": "tee-key-release"}
# AF_VSOCK exists on the Linux enclave; the fallback keeps this module
# importable/testable on development hosts (not used for real connections).
AF_VSOCK = getattr(socket, "AF_VSOCK", 40)
SECRET_FIELDS = (
    ("TEE_PRIVATE_KEY", "TEE_PRIVATE_KEY_CIPHERTEXT"),
    ("PRIVATE_KEY", "PRIVATE_KEY_CIPHERTEXT"),
    ("LLM_API_KEY", "LLM_API_KEY_CIPHERTEXT"),
)


class KmsReleaseError(RuntimeError):
    pass


def _decrypt_response(private_key: rsa.RSAPrivateKey, encrypted: bytes) -> bytes:
    try:
        return private_key.decrypt(
            encrypted,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - cryptography raises several types
        raise KmsReleaseError(f"failed to decrypt the KMS response: {exc}") from exc


def release_key(
    *,
    key_id: str,
    ciphertext_blob_b64: str,
    encryption_context: dict | None = None,
    port: int = DEFAULT_KMS_VSOCK_PORT,
) -> bytes:
    """Ask the parent to KMS-decrypt one sealed value and decrypt the response."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    try:
        document = aws_nsm.fetch_attestation_document(
            nonce=os.urandom(32),
            user_data=key_id.encode("utf-8")[:512],
            public_key=public_der,
        )
    except aws_nsm.NsmAttestationError as exc:
        raise KmsReleaseError(f"failed to obtain the KMS attestation: {exc}") from exc

    request = {
        "key_id": key_id,
        "ciphertext_blob": ciphertext_blob_b64,
        "attestation_document": base64.b64encode(document).decode("ascii"),
        "encryption_algorithm": ALGORITHM,
        "encryption_context": encryption_context or RELEASE_CONTEXT,
    }
    sock = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    try:
        sock.settimeout(45.0)
        sock.connect((PARENT_CID, port))
        sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
        reader = sock.makefile("rb")
        line = reader.readline(MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise KmsReleaseError(f"KMS proxy connection failed: {exc}") from exc
    finally:
        sock.close()

    if not line:
        raise KmsReleaseError("KMS proxy returned an empty response")
    if len(line) > MAX_RESPONSE_BYTES:
        raise KmsReleaseError("KMS proxy response is too large")
    try:
        response = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KmsReleaseError(f"KMS proxy response is not valid JSON: {exc}") from exc
    if not isinstance(response, dict):
        raise KmsReleaseError("KMS proxy response must be a JSON object")
    if response.get("error"):
        raise KmsReleaseError(f"KMS proxy error: {response['error']}")
    try:
        encrypted = base64.b64decode(response["plaintext"], validate=True)
    except (KeyError, ValueError) as exc:
        raise KmsReleaseError("KMS proxy response has no valid plaintext") from exc
    return _decrypt_response(private_key, encrypted)


def release_bundle(bundle: dict, port: int = DEFAULT_KMS_VSOCK_PORT) -> dict:
    """Replace sealed secret fields in ``bundle`` with their released values."""
    if str(bundle.get("KMS_KEY_RELEASE", "")).strip() != "1":
        return bundle
    key_id = str(bundle.get("KMS_KEY_ID", "")).strip()
    if not key_id:
        raise KmsReleaseError("KMS_KEY_ID is missing from the runtime bundle")

    for env_name, cipher_field in SECRET_FIELDS:
        ciphertext = str(bundle.get(cipher_field, "")).strip()
        if not ciphertext:
            continue
        plaintext = release_key(
            key_id=key_id,
            ciphertext_blob_b64=ciphertext,
            encryption_context=RELEASE_CONTEXT,
            port=port,
        )
        try:
            value = plaintext.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise KmsReleaseError(f"{env_name} plaintext is not UTF-8") from exc
        if not value:
            raise KmsReleaseError(f"{env_name} plaintext is empty")
        bundle[env_name] = value
        bundle.pop(cipher_field, None)

    if not str(bundle.get("TEE_PRIVATE_KEY", "")).strip():
        raise KmsReleaseError("KMS release did not produce TEE_PRIVATE_KEY")
    return bundle
