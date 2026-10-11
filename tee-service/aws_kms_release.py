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
from cryptography.hazmat.primitives import padding as symmetric_padding
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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
# The AWS deploy path seals these two unconditionally; a bundle that arrives
# without them in KMS mode is either truncated or tampered with.
REQUIRED_RELEASED_SECRETS = frozenset({"TEE_PRIVATE_KEY", "PRIVATE_KEY"})


class KmsReleaseError(RuntimeError):
    pass


def _rsa_oaep() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def _read_tlv(data: bytes, offset: int, end: int) -> tuple[int, bytes, int]:
    """Read one DER TLV (definite or indefinite length)."""
    if offset + 2 > end:
        raise KmsReleaseError("truncated KMS CMS structure")
    tag = data[offset]
    offset += 1
    length_byte = data[offset]
    offset += 1
    if length_byte == 0x80:
        start = offset
        while offset < end:
            if data[offset : offset + 2] == b"\x00\x00":
                return tag, data[start:offset], offset + 2
            _, _, offset = _read_tlv(data, offset, end)
        raise KmsReleaseError("unterminated KMS CMS value")
    if length_byte & 0x80:
        count = length_byte & 0x7F
        if count == 0 or offset + count > end:
            raise KmsReleaseError("invalid KMS CMS length")
        length = int.from_bytes(data[offset : offset + count], "big")
        offset += count
    else:
        length = length_byte
    if offset + length > end:
        raise KmsReleaseError("truncated KMS CMS value")
    return tag, data[offset : offset + length], offset + length


# OID contents (without the DER tag/length) used to navigate the CMS blob:
# rsaEncryption-OAEP for the recipient, AES-CBC variants for the content key.
OID_CONTENT_RSA_OAEP = bytes.fromhex("2a864886f70d010107")
OID_CONTENTS_AES_CBC = frozenset(
    bytes.fromhex(value)
    for value in ("608648016503040102", "608648016503040122", "60864801650304012a")
)


class _DerNode:
    __slots__ = ("tag", "value", "children")

    def __init__(self, tag: int, value: bytes, children: list["_DerNode"]):
        self.tag = tag
        self.value = value
        self.children = children


def _decode_der(data: bytes) -> list[_DerNode]:
    """Decode a DER/CER structure into nodes; constructed nodes get children."""
    nodes: list[_DerNode] = []
    offset = 0
    end = len(data)
    while offset < end:
        tag, value, offset = _read_tlv(data, offset, end)
        children = _decode_der(value) if tag & 0x20 else []
        nodes.append(_DerNode(tag, value, children))
    return nodes


def _iter_der(nodes: list[_DerNode], ancestors=()):
    """Yield ``(path, node)`` with the full ancestor path for each node."""
    for index, node in enumerate(nodes):
        path = ancestors + ((nodes, index, node),)
        yield path, node
        if node.children:
            yield from _iter_der(node.children, path)


def _find_algorithm(nodes: list[_DerNode], oid_contents):
    """Locate the AlgorithmIdentifier node carrying one of ``oid_contents``.

    Returns ``(parent_list, index, node)`` where ``node`` is the enclosing
    SEQUENCE when the OID is its first child (the CMS AlgorithmIdentifier
    shape), otherwise the OID node itself (the flattened test fixture shape).
    """
    for path, node in _iter_der(nodes):
        if node.tag != 0x06 or node.value not in oid_contents:
            continue
        if len(path) >= 2:
            parent_list, parent_index, parent_node = path[-2]
            if (
                parent_node.tag == 0x30
                and parent_node.children
                and parent_node.children[0] is node
                # A CMS AlgorithmIdentifier holds only the OID (+params); the
                # check keeps a flattened fixture's outer SEQUENCE from being
                # mistaken for one.
                and len(parent_node.children) <= 2
            ):
                return parent_list, parent_index, parent_node
        parent_list, parent_index, _ = path[-1]
        return parent_list, parent_index, node
    return None


def _first_octet_string(nodes: list[_DerNode], start: int) -> bytes | None:
    for node in nodes[start:]:
        if node.tag == 0x04:
            return node.value
    return None


def _context_octets(node: _DerNode) -> bytes | None:
    """Return the bytes of a ``[0]`` encryptedContent node, if it is one."""
    if node.tag & 0xC0 != 0x80:
        return None
    if node.tag & 0x20:  # explicit wrapper: [0] { OCTET STRING ... }
        parts = [child.value for child in node.children if child.tag == 0x04]
        return b"".join(parts) if parts else None
    return node.value  # implicit primitive: [0] IMPLICIT OCTET STRING


def _first_context_octets(nodes: list[_DerNode], start: int) -> bytes | None:
    for node in nodes[start:]:
        candidate = _context_octets(node)
        if candidate:
            return candidate
    return None


def _parse_kms_cms(data: bytes) -> tuple[bytes, bytes, bytes]:
    """Extract (encrypted CEK, AES-CBC IV, encrypted content) from a KMS CMS blob.

    The structure is navigated by OID instead of guessing by element size, so a
    response containing another 16/256-byte OCTET STRING cannot be misread.
    Both the full CMS ContentInfo layout KMS returns and the flattened fixture
    used by the unit tests are supported.
    """
    try:
        nodes = _decode_der(data)
    except KmsReleaseError:
        raise
    except Exception as exc:  # noqa: BLE001 - DER decoding can fail in many ways
        raise KmsReleaseError(f"KMS CMS is not valid DER: {exc}") from exc

    recipient = _find_algorithm(nodes, {OID_CONTENT_RSA_OAEP})
    if recipient is None:
        raise KmsReleaseError("KMS CMS has no RSA-OAEP recipient")
    key_parent, key_index, _key_node = recipient
    encrypted_key = _first_octet_string(key_parent, key_index + 1)
    if not encrypted_key:
        raise KmsReleaseError("KMS CMS recipient has no encrypted content key")

    content = _find_algorithm(nodes, OID_CONTENTS_AES_CBC)
    if content is None:
        raise KmsReleaseError("KMS CMS has no AES-CBC content encryption")
    content_parent, content_index, content_node = content
    iv: bytes | None = None
    if content_node.tag == 0x30:
        iv = next(
            (child.value for child in content_node.children if child.tag == 0x04),
            None,
        )
    if iv is None:
        iv = _first_octet_string(content_parent, content_index + 1)
    encrypted_content = _first_context_octets(content_parent, content_index + 1)

    if iv is None or len(iv) != 16:
        raise KmsReleaseError("unexpected KMS CMS IV")
    if not encrypted_content or len(encrypted_content) % 16 != 0:
        raise KmsReleaseError("unexpected KMS CMS encrypted content")
    return encrypted_key, iv, encrypted_content


def _decrypt_response(private_key: rsa.RSAPrivateKey, encrypted: bytes) -> bytes:
    try:
        encrypted_key, iv, encrypted_content = _parse_kms_cms(encrypted)
        content_key = private_key.decrypt(encrypted_key, _rsa_oaep())
        if len(content_key) != 32:
            raise KmsReleaseError(
                f"unexpected KMS content key length {len(content_key)}"
            )
        decryptor = Cipher(
            algorithms.AES(content_key), modes.CBC(iv)
        ).decryptor()
        padded = decryptor.update(encrypted_content) + decryptor.finalize()
        unpadder = symmetric_padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except KmsReleaseError:
        raise
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

    # KMS mode accepts sealed blobs only: a plaintext secret alongside the flag
    # means the parent skipped (or replaced) the PCR-bound release, so refuse to
    # start with keys that did not come out of KMS.
    for env_name, _cipher_field in SECRET_FIELDS:
        if str(bundle.get(env_name, "")).strip():
            raise KmsReleaseError(
                f"refusing to start: {env_name} arrived in plaintext while "
                "KMS_KEY_RELEASE=1 (only the sealed ciphertext is expected)"
            )

    for env_name, cipher_field in SECRET_FIELDS:
        ciphertext = str(bundle.get(cipher_field, "")).strip()
        if not ciphertext:
            if env_name in REQUIRED_RELEASED_SECRETS:
                raise KmsReleaseError(
                    f"{cipher_field} is missing from the runtime bundle while "
                    "KMS_KEY_RELEASE=1"
                )
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
    if not str(bundle.get("PRIVATE_KEY", "")).strip():
        raise KmsReleaseError("KMS release did not produce PRIVATE_KEY")
    return bundle
