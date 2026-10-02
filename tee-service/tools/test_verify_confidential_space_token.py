import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from attestation import vtpm
from tools import verify_confidential_space_token as verifier


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _segment(value: dict) -> str:
    return _b64url(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _signed_jwt(private_key, claims: dict) -> str:
    header = {"alg": "RS256", "kid": "test-key", "typ": "JWT"}
    signing_input = f"{_segment(header)}.{_segment(claims)}".encode("ascii")
    signature = private_key.sign(
        signing_input,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{signing_input.decode('ascii')}.{_b64url(signature)}"


def _claims(nonce: str, audience: str) -> dict:
    now = int(time.time())
    return {
        "iss": vtpm.CONFIDENTIAL_SPACE_ISSUER,
        "aud": audience,
        "sub": "projects/test/zones/us-central1-a/instances/tee",
        "eat_nonce": [nonce],
        "exp": now + 300,
        "nbf": now - 5,
        "swname": "CONFIDENTIAL_SPACE",
        "swversion": ["250900"],
        "dbgstat": "disabled-since-boot",
        "submods": {
            "container": {
                "image_digest": "sha256:" + "ab" * 32,
                "image_reference": "example/tee@sha256:" + "ab" * 32,
            },
            "gce": {"project_id": "test", "zone": "us-central1-a"},
        },
    }


def test_verifies_google_oidc_style_signature(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "n": _b64url(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
    }
    monkeypatch.setattr(
        verifier,
        "_fetch_json",
        lambda url: (
            {"jwks_uri": "https://example.test/jwks"}
            if url == verifier.OIDC_CONFIG_URL
            else {"keys": [jwk]}
        ),
    )
    token = _signed_jwt(
        private_key,
        _claims("nonce-1234567890", "https://securesignal.test"),
    )

    result = verifier.verify_token(
        token,
        audience="https://securesignal.test",
        nonce="nonce-1234567890",
        image_digest="sha256:" + "ab" * 32,
    )

    assert result["swname"] == "CONFIDENTIAL_SPACE"
    assert result["image_digest"] == "sha256:" + "ab" * 32


def test_rejects_modified_payload(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "n": _b64url(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
    }
    monkeypatch.setattr(
        verifier,
        "_fetch_json",
        lambda _url: {"jwks_uri": "https://example.test/jwks"}
        if _url == verifier.OIDC_CONFIG_URL
        else {"keys": [jwk]},
    )
    token = _signed_jwt(
        private_key,
        _claims("nonce-1234567890", "https://securesignal.test"),
    )
    header, _payload, signature = token.split(".")
    changed = _segment(_claims("nonce-changed", "https://securesignal.test"))

    with pytest.raises(vtpm.AttestationError, match="signature verification failed"):
        verifier.verify_token(
            f"{header}.{changed}.{signature}",
            audience="https://securesignal.test",
            nonce="nonce-changed",
        )
