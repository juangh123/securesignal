"""Unit tests for the enclave-side AWS KMS key release client."""

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import aws_kms_release
from attestation import aws_nsm


def _oaep() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def test_decrypt_response_round_trip():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encrypted = private_key.public_key().encrypt(b"released-key", _oaep())
    assert aws_kms_release._decrypt_response(private_key, encrypted) == b"released-key"


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
        encrypted = public_key.encrypt(plaintext, _oaep())
        return (
            json.dumps(
                {"plaintext": base64.b64encode(encrypted).decode("ascii")}
            ).encode("utf-8")
            + b"\n"
        )

    sockets = []

    def socket_factory(*args, **kwargs):
        sock = _FakeSocket(response_factory)
        sockets.append(sock)
        return sock

    monkeypatch.setattr(aws_nsm, "fetch_attestation_document", fake_attestation)
    monkeypatch.setattr(
        aws_kms_release.socket,
        "socket",
        socket_factory,
    )

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

    def fake_release_key(*, key_id, ciphertext_blob_b64, encryption_context=None, port=8600):
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
