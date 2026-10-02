"""Unit tests for the enclave-side AWS KMS key release client."""

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import padding as symmetric_padding
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import aws_kms_release
from attestation import aws_nsm

# Real CiphertextForRecipient captured from KMS during a debug-enclave run.
# It is a CMS/PKCS#7 EnvelopedData structure encrypted to an ephemeral key
# that no longer exists, so the blob itself is not sensitive.
REAL_KMS_CMS_B64 = (
    "MIAGCSqGSIb3DQEHA6CAMIACAQIxggFrMIIBZwIBAoAg8Y+1paJCZZchAHLryUrXceyzaf+GLKfgpV2lYvJdvjIwPAYJKoZIhvcNAQEHMC+gDzANBglghkgBZQMEAgEFAKEcMBoGCSqGSIb3DQEBCDANBglghkgBZQMEAgEFAASCAQBFUG8wNMDW3FYDnQWmnkNRW4tfVkEkGnBjQU0m2a8XGWb1ptecIGKfcYQ+7xnbKW+6VBHM3AWzhF186hCoNJlGRCbCAFR09YPZ1qulc7QSzyl9pL/b3QUXq6XCdDv3iIrAsIijnyc5TxQ1XwUraAZh8QqUwjEs/NshEdQPZJ6/4grzGVLtiQ1lmMxPmWlNsQsOmt+jSseUgf6HDZjErHeSKNpp3w3eVk+ZombGLaEPXMCdtl602e8p3FooG8bwnGvDp4iPcHBYqZrhbGnXlllFP3+i6kL+yzYMlKidX2Bg7goaFtM3hJkKCdI8j23UN66HU38sRe35L3eEmEAPlMG0MIAGCSqGSIb3DQEHATAdBglghkgBZQMEASoEEG1bYQVuaytvKneqMq47j+CggARQzukUJOkG5nJu95F/VvXkPr4Zo7Fs5bbRBRtwySHua37/esq+nvA98sL9KjXfL5iv4lmhuBVzXh4lKY67FZ3iGLIFgOLeB2+1BNL1atCBERoAAAAAAAAAAAAA"
)


def _oaep() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def _der(tag: int, value: bytes) -> bytes:
    if len(value) < 0x80:
        length = bytes([len(value)])
    else:
        raw = len(value).to_bytes((len(value).bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + length + value


def _build_kms_cms(
    public_key,
    plaintext: bytes,
    *,
    content_key: bytes | None = None,
    iv: bytes | None = None,
) -> bytes:
    """Build a minimal CMS EnvelopedData matching KMS's RSA-OAEP/AES-CBC layout."""
    content_key = content_key or os.urandom(32)
    iv = iv or os.urandom(16)
    padder = symmetric_padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(content_key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    encrypted_key = public_key.encrypt(content_key, _oaep())
    oid_rsa_oaep = bytes.fromhex("06092a864886f70d010107")
    oid_aes_cbc = bytes.fromhex("060960864801650304012a")
    inner = (
        oid_rsa_oaep
        + _der(0x30, b"")
        + _der(0x04, encrypted_key)
        + oid_aes_cbc
        + _der(0x04, iv)
        + _der(0xA0, _der(0x04, ciphertext))
    )
    return _der(0x30, inner)


def test_decrypt_response_round_trip():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    plaintext = b"0x" + b"11" * 32
    cms = _build_kms_cms(private_key.public_key(), plaintext)
    assert aws_kms_release._decrypt_response(private_key, cms) == plaintext


def test_parse_real_kms_cms_structure():
    encrypted_key, iv, content = aws_kms_release._parse_kms_cms(
        base64.b64decode(REAL_KMS_CMS_B64)
    )
    assert len(encrypted_key) == 256
    assert len(iv) == 16
    assert len(content) == 80
    assert len(content) % 16 == 0


class _FakeSocket:
    def __init__(self, response_factory):
        self._response_factory = response_factory
        self.sent = b""

    def settimeout(self, _timeout):
        pass

    def connect(self, address):
        self.address = address

    def sendall(self, data):
        self.sent += data

    def makefile(self, _mode):
        response = self._response_factory()

        class _Reader:
            def readline(self, _limit=-1):
                return response

        return _Reader()

    def close(self):
        pass


def test_release_key_round_trip(monkeypatch):
    captured = {}
    plaintext = b"0x" + b"11" * 32

    def fake_attestation(*, nonce, user_data, public_key=None, timeout=15.0):
        captured["nonce"] = nonce
        captured["user_data"] = user_data
        captured["public_key"] = public_key
        return b"attestation-document"

    def response_factory():
        public_key = serialization.load_der_public_key(captured["public_key"])
        cms = _build_kms_cms(public_key, plaintext)
        return (
            json.dumps(
                {"plaintext": base64.b64encode(cms).decode("ascii")}
            ).encode("utf-8")
            + b"\n"
        )

    sockets = []

    def socket_factory(*args, **kwargs):
        sock = _FakeSocket(response_factory)
        sockets.append(sock)
        return sock

    monkeypatch.setattr(aws_nsm, "fetch_attestation_document", fake_attestation)
    monkeypatch.setattr(aws_kms_release.socket, "socket", socket_factory)

    result = aws_kms_release.release_key(
        key_id="arn:aws:kms:us-east-1:123456789012:key/test",
        ciphertext_blob_b64=base64.b64encode(b"sealed").decode("ascii"),
    )

    assert result == plaintext
    assert len(captured["nonce"]) == 32
    request = json.loads(sockets[0].sent.decode("utf-8"))
    assert request["key_id"].endswith("/test")
    assert request["ciphertext_blob"] == base64.b64encode(b"sealed").decode("ascii")


def test_release_bundle_replaces_sealed_fields(monkeypatch):
    released = {
        "tee": b"0x" + b"11" * 32,
        "relayer": b"0x" + b"22" * 32,
        "llm": b"sk-test",
    }

    def fake_release_key(
        *, key_id, ciphertext_blob_b64, encryption_context=None, port=8600
    ):
        return released[ciphertext_blob_b64]

    monkeypatch.setattr(aws_kms_release, "release_key", fake_release_key)
    bundle = {
        "KMS_KEY_RELEASE": "1",
        "KMS_KEY_ID": "arn:aws:kms:us-east-1:123456789012:key/test",
        "TEE_PRIVATE_KEY_CIPHERTEXT": "tee",
        "PRIVATE_KEY_CIPHERTEXT": "relayer",
        "LLM_API_KEY_CIPHERTEXT": "llm",
    }

    result = aws_kms_release.release_bundle(bundle)

    assert result["TEE_PRIVATE_KEY"] == released["tee"].decode()
    assert result["PRIVATE_KEY"] == released["relayer"].decode()
    assert result["LLM_API_KEY"] == "sk-test"
    assert "TEE_PRIVATE_KEY_CIPHERTEXT" not in result
    assert "PRIVATE_KEY_CIPHERTEXT" not in result
    assert "LLM_API_KEY_CIPHERTEXT" not in result


def test_release_bundle_is_a_noop_without_the_flag():
    bundle = {"TEE_PRIVATE_KEY": "plaintext"}
    assert aws_kms_release.release_bundle(bundle) is bundle


def test_release_bundle_requires_the_tee_key(monkeypatch):
    monkeypatch.setattr(
        aws_kms_release,
        "release_key",
        lambda **kwargs: b"0x" + b"33" * 32,
    )
    bundle = {
        "KMS_KEY_RELEASE": "1",
        "KMS_KEY_ID": "arn:aws:kms:us-east-1:123456789012:key/test",
        "TEE_PRIVATE_KEY_CIPHERTEXT": "",
    }
    with pytest.raises(aws_kms_release.KmsReleaseError):
        aws_kms_release.release_bundle(bundle)
