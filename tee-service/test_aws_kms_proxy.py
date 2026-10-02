"""Unit tests for the parent-side AWS KMS proxy."""

import base64
import json

import aws_kms_proxy


class _FakeKms:
    def __init__(self, response=None, error=None):
        self.response = response or {"Plaintext": b"released-secret"}
        self.error = error
        self.calls = []

    def decrypt(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _request() -> dict:
    return {
        "key_id": "arn:aws:kms:us-east-1:123456789012:key/test",
        "ciphertext_blob": base64.b64encode(b"sealed").decode(),
        "attestation_document": base64.b64encode(b"document").decode(),
        "encryption_algorithm": "RSAES_OAEP_SHA_256",
        "encryption_context": {"app": "securesignal"},
    }


def test_decrypt_forwards_the_attestation_and_returns_base64(monkeypatch):
    fake = _FakeKms()
    monkeypatch.setattr(aws_kms_proxy, "_kms_client", lambda: fake)

    result = aws_kms_proxy._decrypt(_request())

    assert base64.b64decode(result) == b"released-secret"
    call = fake.calls[0]
    assert call["KeyId"].endswith("/test")
    assert call["CiphertextBlob"] == b"sealed"
    assert call["EncryptionContext"] == {"app": "securesignal"}
    assert call["Recipient"]["KeyEncryptionAlgorithm"] == "RSAES_OAEP_SHA_256"
    assert call["Recipient"]["AttestationDocument"] == b"document"


def test_handle_line_reports_kms_errors(monkeypatch):
    fake = _FakeKms(error=RuntimeError("access denied"))
    monkeypatch.setattr(aws_kms_proxy, "_kms_client", lambda: fake)

    response = json.loads(aws_kms_proxy.handle_line(json.dumps(_request()).encode()))

    assert "error" in response
    assert "access denied" in response["error"]


def test_handle_line_rejects_non_object_json():
    response = json.loads(aws_kms_proxy.handle_line(b"[1, 2, 3]"))
    assert "error" in response
