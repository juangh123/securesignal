import base64
import hashlib
import json
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_utils import keccak

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


# ---------------------------------------------------------------------------
# On-chain attestation scheme: eip191-legacy vs eip712-v2
# ---------------------------------------------------------------------------

# Fixed key (hardhat account #1) so these tests do not depend on whether
# crypto.keys generated an ephemeral dev key or loaded TEE_PRIVATE_KEY.
TEE_TEST_PRIVATE_KEY = (
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
)
TEE_TEST_ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

# Fixed domain so the digest can be pinned to a vector. The digest below was
# cross-checked against ethers v6 `TypedDataEncoder.hash` and is what
# OpenZeppelin's `_hashTypedDataV4` computes for the same struct: if either
# implementation drifts, both this test and the contract test fail.
FIXED_DOMAIN = {
    "name": "SecureSignal",
    "version": "2",
    "chainId": 114,
    "verifyingContract": "0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482",
}
TASK_ID = 7
RESULT_HASH = "0x" + "11" * 32
EIP712_DIGEST_VECTOR = (
    "0cd747119c239d9025d8c04aa7a085e7caf7dbe271e184b9a974c62ac5da393f"
)


@pytest.fixture(autouse=True)
def _reset_scheme_state(monkeypatch):
    """Keep the process-wide scheme cache and key material out of the tests."""
    monkeypatch.setattr(vtpm, "_scheme_cache", None)
    monkeypatch.setattr(vtpm, "_scheme_cache_expires_at", 0.0)
    monkeypatch.setattr(vtpm, "_scheme_cache_definitive", False)
    monkeypatch.setattr(vtpm, "_scheme_last_definitive", None)
    monkeypatch.setattr(vtpm, "_eip712_domain_cache", None)
    monkeypatch.delenv("ATTESTATION_SCHEME", raising=False)
    monkeypatch.setattr(vtpm, "get_private_key_hex", lambda: TEE_TEST_PRIVATE_KEY)
    monkeypatch.setattr(vtpm, "get_tee_address", lambda: TEE_TEST_ADDRESS)


def test_attestation_scheme_env_override(monkeypatch):
    monkeypatch.setenv("ATTESTATION_SCHEME", "eip712")
    assert vtpm.attestation_scheme(refresh=True) == vtpm.SCHEME_EIP712_V2

    monkeypatch.setenv("ATTESTATION_SCHEME", "eip191")
    assert vtpm.attestation_scheme(refresh=True) == vtpm.SCHEME_EIP191_LEGACY


def test_attestation_scheme_skips_probe_without_relayer(monkeypatch):
    """Offline/dev/test runs must not pay an RPC round trip per attestation."""
    from flare import contracts as flare_contracts

    monkeypatch.setattr(flare_contracts, "is_configured", lambda: False)
    probes: list[int] = []
    monkeypatch.setattr(
        flare_contracts,
        "registry_attestation_scheme",
        lambda *_args, **_kwargs: probes.append(1),
    )

    assert vtpm.attestation_scheme(refresh=True) == vtpm.SCHEME_EIP191_LEGACY
    assert probes == []


def test_attestation_scheme_reads_and_caches_registry_marker(monkeypatch):
    from flare import contracts as flare_contracts

    monkeypatch.setattr(flare_contracts, "is_configured", lambda: True)
    probes: list[int] = []

    def fake_probe(*_args, **_kwargs):
        probes.append(1)
        return vtpm.SCHEME_EIP712_V2

    monkeypatch.setattr(flare_contracts, "registry_attestation_scheme", fake_probe)

    assert vtpm.attestation_scheme(refresh=True) == vtpm.SCHEME_EIP712_V2
    # A definitive answer is cached for the process lifetime.
    assert vtpm.attestation_scheme() == vtpm.SCHEME_EIP712_V2
    assert len(probes) == 1


def test_attestation_scheme_retries_after_inconclusive_probe(monkeypatch):
    from flare import contracts as flare_contracts

    monkeypatch.setattr(flare_contracts, "is_configured", lambda: True)
    answers: list[str | None] = [None, vtpm.SCHEME_EIP712_V2]
    monkeypatch.setattr(
        flare_contracts,
        "registry_attestation_scheme",
        lambda *_args, **_kwargs: answers.pop(0),
    )

    assert vtpm.attestation_scheme(refresh=True) == vtpm.SCHEME_EIP191_LEGACY
    # The fallback is not pinned forever: the 300 s deadline lets a recovered
    # RPC upgrade the service to the v2 convention.
    assert vtpm._scheme_cache_expires_at < float("inf")

    monkeypatch.setattr(vtpm, "_scheme_cache_expires_at", 0.0)
    assert vtpm.attestation_scheme() == vtpm.SCHEME_EIP712_V2


def _force_unknown_scheme(monkeypatch):
    """Explicitly pin "no answer known" for the strict-mode tests."""
    monkeypatch.setattr(vtpm, "_scheme_cache", None)
    monkeypatch.setattr(vtpm, "_scheme_cache_expires_at", 0.0)
    monkeypatch.setattr(vtpm, "_scheme_cache_definitive", False)
    monkeypatch.setattr(vtpm, "_scheme_last_definitive", None)


def test_attestation_scheme_strict_refuses_to_guess_on_cold_start(monkeypatch):
    from flare import contracts as flare_contracts

    _force_unknown_scheme(monkeypatch)
    monkeypatch.setattr(flare_contracts, "is_configured", lambda: True)
    monkeypatch.setattr(
        flare_contracts, "registry_attestation_scheme", lambda *_a, **_k: None
    )

    # Non-strict callers (/health) still report the legacy default...
    assert vtpm.attestation_scheme(refresh=True) == vtpm.SCHEME_EIP191_LEGACY
    # ...but the signing path must not sign with a guessed convention.
    monkeypatch.setattr(vtpm, "_scheme_cache_expires_at", 0.0)
    with pytest.raises(vtpm.AttestationError, match="refusing to sign"):
        vtpm.attestation_scheme(refresh=True, strict=True)


def test_attestation_scheme_reuses_last_definitive_answer(monkeypatch):
    from flare import contracts as flare_contracts

    _force_unknown_scheme(monkeypatch)
    monkeypatch.setattr(flare_contracts, "is_configured", lambda: True)
    answers: list[str | None] = [vtpm.SCHEME_EIP712_V2, None]

    def fake_probe(*_args, **_kwargs):
        return answers.pop(0) if answers else None

    monkeypatch.setattr(
        flare_contracts,
        "registry_attestation_scheme",
        fake_probe,
    )

    assert vtpm.attestation_scheme(refresh=True) == vtpm.SCHEME_EIP712_V2

    # The probe now fails, but the process already knows the convention: both
    # /health and the signing path keep using the last definitive answer.
    monkeypatch.setattr(vtpm, "_scheme_cache_expires_at", 0.0)
    assert vtpm.attestation_scheme(strict=True) == vtpm.SCHEME_EIP712_V2
    monkeypatch.setattr(vtpm, "_scheme_cache_expires_at", 0.0)
    assert vtpm.attestation_scheme() == vtpm.SCHEME_EIP712_V2


def test_attestation_scheme_strict_accepts_legacy_registry(monkeypatch):
    """Regression: the live Coston2 registry is v1, so a reverting marker probe
    must resolve to a definitive legacy answer and keep the signing path alive."""
    from flare import contracts as flare_contracts

    _force_unknown_scheme(monkeypatch)
    monkeypatch.setattr(flare_contracts, "is_configured", lambda: True)
    monkeypatch.setattr(
        flare_contracts,
        "registry_attestation_scheme",
        lambda *_args, **_kwargs: flare_contracts.SCHEME_EIP191_LEGACY,
    )

    assert (
        vtpm.attestation_scheme(refresh=True, strict=True)
        == vtpm.SCHEME_EIP191_LEGACY
    )


def test_eip712_digest_matches_cross_checked_vector(monkeypatch):
    monkeypatch.setattr(vtpm, "_eip712_domain", lambda: dict(FIXED_DOMAIN))
    digest = vtpm.eip712_attestation_digest(TASK_ID, RESULT_HASH)
    assert digest.hex() == EIP712_DIGEST_VECTOR


def test_sign_result_eip712_recovers_to_tee_address(monkeypatch):
    monkeypatch.setattr(vtpm, "_eip712_domain", lambda: dict(FIXED_DOMAIN))
    monkeypatch.setattr(
        vtpm,
        "attestation_scheme",
        lambda refresh=False, strict=False: vtpm.SCHEME_EIP712_V2,
    )

    signature = vtpm.sign_result(TASK_ID, RESULT_HASH)

    typed = encode_typed_data(
        domain_data=dict(FIXED_DOMAIN),
        message_types=vtpm.EIP712_ATTESTATION_TYPES,
        message_data={
            "taskId": TASK_ID,
            "resultHash": bytes.fromhex(RESULT_HASH[2:]),
        },
    )
    assert Account.recover_message(typed, signature=signature) == TEE_TEST_ADDRESS

    # ...and it must NOT verify as the legacy packed message.
    packed = TASK_ID.to_bytes(32, "big") + bytes.fromhex(RESULT_HASH[2:])
    assert (
        Account.recover_message(encode_defunct(primitive=packed), signature=signature)
        != TEE_TEST_ADDRESS
    )


def test_sign_result_legacy_matches_contract_packing(monkeypatch):
    monkeypatch.setattr(
        vtpm,
        "attestation_scheme",
        lambda refresh=False, strict=False: vtpm.SCHEME_EIP191_LEGACY,
    )

    signature = vtpm.sign_result(TASK_ID, RESULT_HASH)

    # abi.encodePacked(uint256 taskId, bytes32 resultHash) -> EIP-191 envelope.
    packed = TASK_ID.to_bytes(32, "big") + bytes.fromhex(RESULT_HASH[2:])
    legacy_digest = keccak(b"\x19Ethereum Signed Message:\n64" + packed)
    assert legacy_digest.hex() != EIP712_DIGEST_VECTOR
    assert (
        Account.recover_message(encode_defunct(primitive=packed), signature=signature)
        == TEE_TEST_ADDRESS
    )


def test_generate_attestation_token_reports_scheme(monkeypatch):
    monkeypatch.setattr(vtpm, "sign_result", lambda *_args, **_kwargs: "0x" + "11" * 65)
    monkeypatch.setattr(
        vtpm, "attestation_scheme", lambda refresh=False: vtpm.SCHEME_EIP712_V2
    )

    token = json.loads(vtpm.generate_attestation_token(1, RESULT_HASH))

    assert token["attestation_scheme"] == vtpm.SCHEME_EIP712_V2
    assert token["signature"] == "0x" + "11" * 65


def test_eip712_domain_requires_registry_address(monkeypatch):
    from flare import contracts as flare_contracts

    monkeypatch.setattr(flare_contracts, "registry_address", lambda: "")
    monkeypatch.setattr(flare_contracts, "registry_chain_id", lambda *a, **k: 114)

    with pytest.raises(vtpm.AttestationError, match="requires the registry address"):
        vtpm._eip712_domain()


def test_eip712_domain_is_cached_after_first_resolution(monkeypatch):
    """Signing must not need an RPC round trip per attestation."""
    from flare import contracts as flare_contracts

    lookups: list[int] = []

    def fake_address():
        lookups.append(1)
        return "0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482"

    monkeypatch.setattr(flare_contracts, "registry_address", fake_address)
    monkeypatch.setattr(flare_contracts, "registry_chain_id", lambda *a, **k: 114)

    first = vtpm._eip712_domain()
    assert first == FIXED_DOMAIN
    assert vtpm._eip712_domain() == first
    assert len(lookups) == 1
