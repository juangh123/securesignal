import base64
import hashlib
import json
import time

import pytest

from attestation import vtpm


def _jwt_segment(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unsigned_jwt(claims: dict) -> str:
    return ".".join(
        [
            _jwt_segment({"alg": "RS256", "kid": "test-key"}),
            _jwt_segment(claims),
            "signature",
        ]
    )


def _claims(*, nonce: str, audience: str, image_digest: str) -> dict:
    now = int(time.time())
    return {
        "iss": vtpm.CONFIDENTIAL_SPACE_ISSUER,
        "aud": audience,
        "eat_nonce": [nonce],
        "exp": now + 300,
        "nbf": now - 5,
        "swname": "CONFIDENTIAL_SPACE",
        "dbgstat": "disabled-since-boot",
        "submods": {"container": {"image_digest": image_digest}},
    }


def test_dev_token_is_explicitly_simulated(monkeypatch):
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("TEE_IMAGE_DIGEST", "dev")
    monkeypatch.setattr(vtpm, "sign_result", lambda _task, _hash: "0x" + "11" * 65)

    token = json.loads(
        vtpm.generate_attestation_token(
            7,
            "0x" + "22" * 32,
            tee_address="0x0000000000000000000000000000000000000001",
        )
    )

    assert token["mode"] == "dev-simulated"
    assert token["image_digest"] == "dev"
    assert "jwt" not in token


def test_production_token_uses_confidential_space_measurement(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("GCP_ATTESTATION_AUDIENCE", "https://securesignal.test")
    monkeypatch.setenv("TEE_IMAGE_DIGEST", "sha256:" + "ab" * 32)
    monkeypatch.setattr(vtpm, "sign_result", lambda _task, _hash: "0x" + "11" * 65)

    task_id = 7
    result_hash = "0x" + "22" * 32
    nonce = hashlib.sha256(f"{task_id}:{result_hash}".encode("utf-8")).hexdigest()
    jwt = _unsigned_jwt(
        _claims(
            nonce=nonce,
            audience="https://securesignal.test",
            image_digest="sha256:" + "ab" * 32,
        )
    )
    monkeypatch.setattr(vtpm, "fetch_attestation_jwt", lambda _aud, _nonce: jwt)

    token = json.loads(
        vtpm.generate_attestation_token(
            task_id,
            result_hash,
            tee_address="0x0000000000000000000000000000000000000001",
        )
    )

    assert token["mode"] == "gcp-confidential-space"
    assert token["jwt"] == jwt
    assert token["attestation_nonce"] == nonce
    assert token["image_digest"] == "sha256:" + "ab" * 32


def test_production_token_rejects_measurement_mismatch(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("GCP_ATTESTATION_AUDIENCE", "https://securesignal.test")
    monkeypatch.setenv("TEE_IMAGE_DIGEST", "sha256:" + "ff" * 32)
    monkeypatch.setattr(vtpm, "sign_result", lambda _task, _hash: "0x" + "11" * 65)

    task_id = 7
    result_hash = "0x" + "22" * 32
    nonce = hashlib.sha256(f"{task_id}:{result_hash}".encode("utf-8")).hexdigest()
    jwt = _unsigned_jwt(
        _claims(
            nonce=nonce,
            audience="https://securesignal.test",
            image_digest="sha256:" + "ab" * 32,
        )
    )
    monkeypatch.setattr(vtpm, "fetch_attestation_jwt", lambda _aud, _nonce: jwt)

    with pytest.raises(vtpm.AttestationError, match="does not match"):
        vtpm.generate_attestation_token(
            task_id,
            result_hash,
            tee_address="0x0000000000000000000000000000000000000001",
        )


def test_aws_nitro_token_contains_nsm_document(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ATTESTATION_PROVIDER", "aws-nitro-enclaves")
    monkeypatch.setenv("AWS_NITRO_PCR0", "ab" * 48)
    monkeypatch.setattr(vtpm, "sign_result", lambda _task, _hash: "0x" + "11" * 65)
    monkeypatch.setattr(
        vtpm,
        "get_public_key_hex",
        lambda: "04" + "22" * 64,
    )
    monkeypatch.setattr(
        vtpm.aws_nsm,
        "attestation_fields",
        lambda **_kwargs: {
            "nsm_document": "base64-document",
            "nsm_document_sha256": "cd" * 32,
            "nsm_nonce": "ee" * 32,
            "nsm_user_data": "ff" * 64,
            "pcr0": "ab" * 48,
        },
    )

    token = json.loads(
        vtpm.generate_attestation_token(
            9,
            "0x" + "33" * 32,
            tee_address="0x0000000000000000000000000000000000000001",
        )
    )

    assert token["mode"] == "aws-nitro-enclaves"
    assert token["nsm_document"] == "base64-document"
    assert token["pcr0"] == "ab" * 48
    assert token["image_digest"] == "ab" * 48


def test_aws_runtime_fails_closed_without_nsm_device(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ATTESTATION_PROVIDER", "aws-nitro-enclaves")
    monkeypatch.setattr(
        vtpm.aws_nsm,
        "ensure_nitro_runtime",
        lambda: (_ for _ in ()).throw(
            vtpm.aws_nsm.NsmAttestationError("missing /dev/nsm")
        ),
    )

    with pytest.raises(RuntimeError, match="/dev/nsm"):
        vtpm.ensure_attestation_runtime()


def test_fetch_attestation_jwt_posts_official_request(monkeypatch):
    seen = {}

    class FakeResponse:
        status = 200

        def read(self):
            return b"header.payload.signature"

    class FakeConnection:
        def __init__(self, path, timeout):
            seen["path"] = path
            seen["timeout"] = timeout

        def request(self, method, request_path, body, headers):
            seen["method"] = method
            seen["request_path"] = request_path
            seen["body"] = json.loads(body)
            seen["headers"] = headers

        def getresponse(self):
            return FakeResponse()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(vtpm, "_UnixHTTPConnection", FakeConnection)
    token = vtpm.fetch_attestation_jwt(
        "https://securesignal.test",
        "nonce-1234567890",
        socket_path="/tmp/teeserver.sock",
        timeout=3,
    )

    assert token == "header.payload.signature"
    assert seen == {
        "path": "/tmp/teeserver.sock",
        "timeout": 3,
        "method": "POST",
        "request_path": "/v1/token",
        "body": {
            "audience": "https://securesignal.test",
            "token_type": "OIDC",
            "nonces": ["nonce-1234567890"],
        },
        "headers": {"Content-Type": "application/json"},
        "closed": True,
    }
