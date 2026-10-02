"""Parent-side AWS KMS proxy for Nitro Enclave key release.

The enclave sends a KMS ``Decrypt`` request over vsock together with an NSM
attestation document whose ``public_key`` is an ephemeral RSA public key. This
process forwards the request to KMS; the key policy requires the attestation
PCRs, and KMS encrypts the response to the enclave's RSA key, so the parent
never sees the released plaintext.
"""

import base64
import json
import os
import socket
import threading

LISTEN_PORT = int(os.environ.get("KMS_VSOCK_PORT", "8600"))
REGION = os.environ.get(
    "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
)
MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_ALGORITHM = "RSAES_OAEP_SHA_256"
# AF_VSOCK/VMADDR_CID_ANY exist on the Linux parent instance; the fallbacks
# keep this module importable/testable on development hosts.
AF_VSOCK = getattr(socket, "AF_VSOCK", 40)
VMADDR_CID_ANY = getattr(socket, "VMADDR_CID_ANY", 0xFFFFFFFF)

_client = None


def _kms_client():
    global _client
    if _client is None:
        # Imported lazily so unit tests and non-parent environments do not need
        # boto3 installed.
        import boto3  # type: ignore

        _client = boto3.client("kms", region_name=REGION)
    return _client


def _decrypt(request: dict) -> str:
    key_id = str(request["key_id"])
    ciphertext_blob = base64.b64decode(request["ciphertext_blob"], validate=True)
    attestation_document = base64.b64decode(
        request["attestation_document"], validate=True
    )
    algorithm = str(request.get("encryption_algorithm") or DEFAULT_ALGORITHM)
    encryption_context = request.get("encryption_context") or {}
    if not isinstance(encryption_context, dict):
        raise ValueError("encryption_context must be an object")

    response = _kms_client().decrypt(
        KeyId=key_id,
        CiphertextBlob=ciphertext_blob,
        EncryptionContext=encryption_context,
        Recipient={
            "KeyEncryptionAlgorithm": algorithm,
            "AttestationDocument": attestation_document,
        },
    )
    return base64.b64encode(response["Plaintext"]).decode("ascii")


def handle_line(line: bytes) -> bytes:
    """Handle one JSON-line request and return one JSON-line response."""
    try:
        request = json.loads(line.decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        return json.dumps({"plaintext": _decrypt(request)}).encode("utf-8") + b"\n"
    except Exception as exc:  # noqa: BLE001 - report any KMS/parse failure
        return (
            json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode("utf-8")
            + b"\n"
        )


def handle(connection: socket.socket) -> None:
    try:
        reader = connection.makefile("rb")
        while True:
            line = reader.readline(MAX_REQUEST_BYTES + 1)
            if not line:
                return
            if len(line) > MAX_REQUEST_BYTES:
                connection.sendall(
                    json.dumps({"error": "KMS request is too large"}).encode("utf-8")
                    + b"\n"
                )
                return
            connection.sendall(handle_line(line))
    finally:
        connection.close()


def main() -> None:
    listener = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((VMADDR_CID_ANY, LISTEN_PORT))
    listener.listen(16)
    print(
        f"[kms-proxy] listening on vsock:{LISTEN_PORT} (region {REGION})",
        flush=True,
    )
    while True:
        connection, _ = listener.accept()
        threading.Thread(target=handle, args=(connection,), daemon=True).start()


if __name__ == "__main__":
    main()
