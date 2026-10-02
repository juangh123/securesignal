"""
Request-boundary tests for the FastAPI surface of the TEE service.

Offline by design: no RPC calls, no on-chain writes. Covers the caps that
protect /analyze from oversized or malformed requests, and the on-chain task
gate that stops the public endpoint (which may call a paid LLM) from being
driven by arbitrary task ids.
"""

import os
import time
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from eth_utils import keccak

import main


@pytest.fixture(autouse=True)
def _offline_registry(monkeypatch):
    """
    main.py calls load_dotenv(), so a developer's tee-service/.env can make the
    relayer look configured and send these tests to a real RPC. Force the gate
    off by default; the gate tests below opt back in explicitly.
    """
    monkeypatch.setattr(main.relayer, "is_configured", lambda: False)
    monkeypatch.delenv("ANALYZE_REQUIRE_ONCHAIN_TASK", raising=False)
    monkeypatch.delenv("ANALYZE_TASK_MAX_AGE_SECONDS", raising=False)


def _post(payload: dict):
    with TestClient(main.app) as c:
        return c.post("/analyze", json=payload)


def test_rejects_negative_task_id():
    r = _post({"task_id": -1, "encrypted_data": "AAAA"})
    assert r.status_code == 400
    assert "task_id" in r.json()["detail"]


def test_rejects_oversized_encrypted_data():
    r = _post({"task_id": 0, "encrypted_data": "A" * (main.MAX_ENCRYPTED_DATA_CHARS + 1)})
    assert r.status_code == 413
    assert "limit" in r.json()["detail"]


def test_rejects_invalid_base64():
    r = _post({"task_id": 0, "encrypted_data": "not base64!!"})
    assert r.status_code == 400
    assert "base64" in r.json()["detail"]


# ---------------------------------------------------------------------------
# On-chain task gate
# ---------------------------------------------------------------------------

INVALID_B64 = "not base64!!"
INVALID_B64_HASH = "0x" + keccak(text=INVALID_B64).hex()
NOW = int(time.time())


def _with_gate(state, *, configured=True, input_hash=INVALID_B64_HASH):
    """Patch the gate with a (status, requestedAt, inputDataHash) state."""
    if state is not None and len(state) == 2:
        state = (state[0], state[1], input_hash)
    return (
        mock.patch.object(main.relayer, "is_configured", return_value=configured),
        mock.patch.object(
            main.relayer, "task_state_with_input", return_value=state
        ),
    )


def test_rejects_task_that_does_not_exist_onchain():
    is_conf, task_state = _with_gate((0, 0))
    with is_conf, task_state:
        r = _post({"task_id": 1001, "encrypted_data": INVALID_B64})
    assert r.status_code == 409
    assert "not pending on-chain" in r.json()["detail"]
    assert "None" in r.json()["detail"]


def test_rejects_already_verified_task():
    is_conf, task_state = _with_gate((3, NOW))
    with is_conf, task_state:
        r = _post({"task_id": 5, "encrypted_data": INVALID_B64})
    assert r.status_code == 409
    assert "Verified" in r.json()["detail"]


def test_fresh_pending_task_passes_the_gate():
    """status=1 (Requested) must reach decryption, i.e. fail later on base64."""
    is_conf, task_state = _with_gate((1, NOW))
    with is_conf, task_state:
        r = _post({"task_id": 1, "encrypted_data": INVALID_B64})
    assert r.status_code == 400
    assert "base64" in r.json()["detail"]


def test_rejects_stale_requested_task():
    """A task left Requested for hours must not be a free LLM trigger."""
    stale = NOW - main.DEFAULT_TASK_MAX_AGE_SECONDS - 60
    is_conf, task_state = _with_gate((1, stale))
    with is_conf, task_state:
        r = _post({"task_id": 0, "encrypted_data": INVALID_B64})
    assert r.status_code == 409
    assert "analysis window" in r.json()["detail"]


def test_age_window_can_be_disabled():
    stale = NOW - main.DEFAULT_TASK_MAX_AGE_SECONDS - 60
    is_conf, task_state = _with_gate((1, stale))
    with mock.patch.dict(os.environ, {"ANALYZE_TASK_MAX_AGE_SECONDS": "0"}), is_conf, task_state:
        r = _post({"task_id": 0, "encrypted_data": INVALID_B64})
    assert r.status_code == 400
    assert "base64" in r.json()["detail"]


def test_registry_read_failure_fails_closed():
    """Without the on-chain input hash we cannot prove the payload binding."""
    is_conf, task_state = _with_gate(None)
    with is_conf, task_state:
        r = _post({"task_id": 1, "encrypted_data": INVALID_B64})
    assert r.status_code == 503
    assert "binding" in r.json()["detail"]


def test_rejects_payload_that_does_not_match_onchain_input_hash():
    """A substituted ciphertext must not be analysed against someone else's task."""
    is_conf, task_state = _with_gate((1, NOW), input_hash="0x" + "11" * 32)
    with is_conf, task_state:
        r = _post({"task_id": 7, "encrypted_data": INVALID_B64})
    assert r.status_code == 409
    assert "inputDataHash" in r.json()["detail"]


def test_gate_can_be_disabled_by_env():
    is_conf, task_state = _with_gate((0, 0))
    with mock.patch.dict(os.environ, {"ANALYZE_REQUIRE_ONCHAIN_TASK": "0"}), is_conf, task_state as ts:
        r = _post({"task_id": 1001, "encrypted_data": INVALID_B64})
    assert r.status_code == 400
    ts.assert_not_called()


def test_gate_is_inert_without_a_configured_registry():
    is_conf, task_state = _with_gate((0, 0), configured=False)
    with is_conf, task_state as ts:
        r = _post({"task_id": 1001, "encrypted_data": INVALID_B64})
    assert r.status_code == 400
    ts.assert_not_called()
