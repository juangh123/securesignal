"""Unit tests for NSM document parsing in attestation/aws_nsm.py.

The envelope here is a well-formed COSE_Sign1 CBOR structure with a payload
that mirrors the AWS NSM layout (module_id/digest/timestamp/pcrs/...). The
signature chain is deliberately not verified in this module; these tests pin
the PCR extraction and the request-binding checks.
"""

import cbor2
import pytest

from attestation import aws_nsm

NONCE = b"\x01" * 32
USER_DATA = b"\x02" * 64
PUBLIC_KEY = b"\x04" + b"\x03" * 64
PCRS = {0: b"\xaa" * 48, 1: b"\xbb" * 48, 2: b"\xcc" * 48}


def _document(
    *,
    pcrs: dict | None = None,
    nonce: bytes = NONCE,
    user_data: bytes = USER_DATA,
    public_key: bytes = PUBLIC_KEY,
) -> bytes:
    payload = cbor2.dumps(
        {
            "module_id": "i-test",
            "digest": "SHA384",
            "timestamp": 1,
            "pcrs": PCRS if pcrs is None else pcrs,
            "certificate": b"leaf",
            "cabundle": [b"root"],
            "nonce": nonce,
            "user_data": user_data,
            "public_key": public_key,
        }
    )
    return cbor2.dumps(
        cbor2.CBORTag(18, [b"protected", {}, payload, b"signature"])
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for index in range(0, 9):
        monkeypatch.delenv(f"AWS_NITRO_PCR{index}", raising=False)


def test_pcr_fields_come_from_the_signed_document(monkeypatch):
    document = _document()
    monkeypatch.setattr(
        aws_nsm, "fetch_attestation_document", lambda **_kwargs: document
    )

    fields = aws_nsm.attestation_fields(
        nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
    )

    assert fields["pcr0"] == "aa" * 48
    assert fields["pcr1"] == "bb" * 48
    assert fields["pcr2"] == "cc" * 48
    assert fields["nsm_nonce"] == NONCE.hex()
    assert fields["nsm_user_data"] == USER_DATA.hex()


def test_matching_env_pcr_is_accepted(monkeypatch):
    monkeypatch.setenv("AWS_NITRO_PCR0", "AA" * 48)
    monkeypatch.setattr(
        aws_nsm, "fetch_attestation_document", lambda **_kwargs: _document()
    )

    fields = aws_nsm.attestation_fields(
        nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
    )

    assert fields["pcr0"] == "aa" * 48


def test_env_pcr_mismatch_fails_closed(monkeypatch):
    monkeypatch.setenv("AWS_NITRO_PCR0", "dd" * 48)
    monkeypatch.setattr(
        aws_nsm, "fetch_attestation_document", lambda **_kwargs: _document()
    )

    with pytest.raises(aws_nsm.NsmAttestationError, match="AWS_NITRO_PCR0"):
        aws_nsm.attestation_fields(
            nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
        )


def test_nonce_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(
        aws_nsm,
        "fetch_attestation_document",
        lambda **_kwargs: _document(nonce=b"\x09" * 32),
    )

    with pytest.raises(aws_nsm.NsmAttestationError, match="nonce"):
        aws_nsm.attestation_fields(
            nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
        )


def test_user_data_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(
        aws_nsm,
        "fetch_attestation_document",
        lambda **_kwargs: _document(user_data=b"\x09" * 64),
    )

    with pytest.raises(aws_nsm.NsmAttestationError, match="user_data"):
        aws_nsm.attestation_fields(
            nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
        )


def test_malformed_document_fails_closed(monkeypatch):
    monkeypatch.setattr(
        aws_nsm, "fetch_attestation_document", lambda **_kwargs: b"not-cbor"
    )

    with pytest.raises(aws_nsm.NsmAttestationError):
        aws_nsm.attestation_fields(
            nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
        )


def test_document_without_pcrs_fails_closed(monkeypatch):
    monkeypatch.setattr(
        aws_nsm,
        "fetch_attestation_document",
        lambda **_kwargs: _document(pcrs={}),
    )

    with pytest.raises(aws_nsm.NsmAttestationError, match="no PCR"):
        aws_nsm.attestation_fields(
            nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
        )


def test_ensure_nitro_runtime_requires_the_device(monkeypatch):
    monkeypatch.setattr(aws_nsm.os.path, "exists", lambda _path: False)

    with pytest.raises(aws_nsm.NsmAttestationError, match="dev/nsm"):
        aws_nsm.ensure_nitro_runtime()


def test_ensure_nitro_runtime_requires_the_helper(monkeypatch):
    monkeypatch.setattr(
        aws_nsm.os.path,
        "exists",
        lambda path: path == aws_nsm.NSM_DEVICE_PATH,
    )

    with pytest.raises(aws_nsm.NsmAttestationError, match="NSM helper"):
        aws_nsm.ensure_nitro_runtime()


def test_fetch_attestation_document_runs_the_helper(monkeypatch):
    captured: dict = {}

    class _Completed:
        returncode = 0
        stdout = b"document-bytes"
        stderr = b""

    def fake_run(args, env, stdout, stderr, check, timeout):
        captured.update(args=args, env=env, timeout=timeout, check=check)
        return _Completed()

    monkeypatch.setattr(aws_nsm, "ensure_nitro_runtime", lambda: None)
    monkeypatch.setattr(aws_nsm.subprocess, "run", fake_run)

    document = aws_nsm.fetch_attestation_document(
        nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
    )

    assert document == b"document-bytes"
    assert captured["args"] == [aws_nsm.helper_path()]
    assert captured["check"] is False
    assert captured["timeout"] == 15.0
    assert captured["env"]["NSM_NONCE_HEX"] == NONCE.hex()
    assert captured["env"]["NSM_USER_DATA_HEX"] == USER_DATA.hex()
    assert captured["env"]["NSM_PUBLIC_KEY_HEX"] == PUBLIC_KEY.hex()


def test_fetch_attestation_document_reports_helper_failure(monkeypatch):
    class _Completed:
        returncode = 2
        stdout = b""
        stderr = b"nsm device busy"

    monkeypatch.setattr(aws_nsm, "ensure_nitro_runtime", lambda: None)
    monkeypatch.setattr(
        aws_nsm.subprocess, "run", lambda *_args, **_kwargs: _Completed()
    )

    with pytest.raises(aws_nsm.NsmAttestationError, match="nsm device busy"):
        aws_nsm.fetch_attestation_document(
            nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
        )


def test_fetch_attestation_document_rejects_empty_output(monkeypatch):
    class _Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    monkeypatch.setattr(aws_nsm, "ensure_nitro_runtime", lambda: None)
    monkeypatch.setattr(
        aws_nsm.subprocess, "run", lambda *_args, **_kwargs: _Completed()
    )

    with pytest.raises(aws_nsm.NsmAttestationError, match="empty document"):
        aws_nsm.fetch_attestation_document(
            nonce=NONCE, user_data=USER_DATA, public_key=PUBLIC_KEY
        )
