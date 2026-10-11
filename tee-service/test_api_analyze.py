"""Behaviour tests for the hardened /analyze endpoint.

Offline by design: the relayer, LLM, ECIES and engine are all stubbed, so the
tests pin the abuse-protection semantics (fail-closed gate, single-flight,
result cache, body-size limit) without any RPC or on-chain traffic.
"""

import asyncio
import base64
import json
import time

import pytest
from eth_utils import keccak
from fastapi.testclient import TestClient

import main

CLIENT_PUBKEY = "04" + "ab" * 64


def _plaintext(client_pubkey: str = CLIENT_PUBKEY) -> bytes:
    return json.dumps(
        {
            "client_pubkey": client_pubkey,
            "holdings": {"BTC": 1.0},
            "risk_profile": "moderate",
        }
    ).encode("utf-8")


def _success_result() -> dict:
    return {
        "status": "success",
        "analysis_mode": "rule-fallback",
        "price_source": "offline-fixture",
        "prices_used": {"BTC": 65000.0},
        "total_value_usd": 65000.0,
        "holdings": [
            {
                "symbol": "BTC",
                "amount": 1.0,
                "value_usd": 65000.0,
                "weight_pct": 100.0,
            }
        ],
        "risk_score": 75,
        "risk_level": "high",
        "rebalance": [
            {"action": "decrease", "symbol": "BTC", "reason": "concentrated"}
        ],
        "summary": "test summary",
    }


@pytest.fixture()
def analyze_env(monkeypatch):
    """Offline, relayer-less, LLM-less environment with clean caches."""
    monkeypatch.setattr(main.relayer, "is_configured", lambda: False)
    monkeypatch.setattr(main.relayer, "registry_address", lambda: "")
    monkeypatch.setattr(main.llm, "is_configured", lambda: False)
    monkeypatch.delenv("ANALYZE_REQUIRE_ONCHAIN_TASK", raising=False)
    monkeypatch.delenv("ANALYZE_RESULT_CACHE_SECONDS", raising=False)
    monkeypatch.delenv("ANALYZE_MAX_CONCURRENCY", raising=False)
    # Rate limiting is disabled by default here so the functional tests stay
    # hermetic; the dedicated test below opts back in explicitly.
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE", "0")
    monkeypatch.delenv("ANALYZE_TOTAL_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("ONCHAIN_RECEIPT_TIMEOUT", raising=False)
    main._reset_analyze_caches()
    yield
    main._reset_analyze_caches()


def _patch_pipeline(monkeypatch, calls: list, result: dict | None = None) -> None:
    payload_result = _success_result() if result is None else result

    def fake_decrypt(ciphertext):  # noqa: ARG001 - stub
        calls.append("decrypt")
        return _plaintext()

    def fake_analyze(payload):  # noqa: ARG001 - stub
        calls.append("analyze")
        return payload_result

    def fake_attestation(task_id, result_hash):
        return json.dumps(
            {
                "task_id": task_id,
                "result_hash": result_hash,
                "signature": "0x" + "11" * 65,
            }
        )

    monkeypatch.setattr(main.tee_keys, "decrypt", fake_decrypt)
    monkeypatch.setattr(main, "analyze_portfolio", fake_analyze)
    monkeypatch.setattr(main, "generate_attestation_token", fake_attestation)
    monkeypatch.setattr(
        main.tee_keys, "encrypt", lambda pubkey, data: b"encrypted-result"
    )


def _post(body: dict):
    with TestClient(main.app) as client:
        return client.post("/analyze", json=body)


def _request_body(task_id: int = 0) -> dict:
    return {
        "task_id": task_id,
        "encrypted_data": base64.b64encode(b"ciphertext").decode("ascii"),
    }


def test_success_response_shape_and_cache_hit(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)

    first = _post(_request_body())
    assert first.status_code == 200
    body = first.json()
    assert body["task_id"] == 0
    assert body["result_hash"].startswith("0x")
    assert body["onchain_submitted"] is False
    assert body["encrypted_result"] == base64.b64encode(
        b"encrypted-result"
    ).decode("ascii")

    second = _post(_request_body())
    assert second.status_code == 200
    assert second.json()["result_hash"] == body["result_hash"]
    # The duplicate must not pay for a second analysis.
    assert calls.count("analyze") == 1


def test_error_result_is_not_cached(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls, {"status": "error", "error": "boom"})

    assert _post(_request_body()).status_code == 200
    assert _post(_request_body()).status_code == 200
    # Transient failures must be retried instead of being pinned by the cache.
    assert calls.count("analyze") == 2


def test_single_flight_coalesces_concurrent_work(analyze_env):
    async def scenario():
        calls: list = []

        async def work():
            calls.append("run")
            await asyncio.sleep(0.05)
            return "result"

        results = await asyncio.gather(
            main._single_flight("same-key", work),
            main._single_flight("same-key", work),
        )
        return calls, results

    calls, results = asyncio.run(scenario())
    assert calls == ["run"]
    assert results == ["result", "result"]


def test_gate_fails_closed_without_relayer_when_llm_configured(
    analyze_env, monkeypatch
):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(main.llm, "is_configured", lambda: True)

    response = _post(_request_body())

    assert response.status_code == 503
    assert "ANALYZE_REQUIRE_ONCHAIN_TASK=0" in response.json()["detail"]
    assert calls == []


def test_gate_can_be_disabled_explicitly(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(main.llm, "is_configured", lambda: True)
    monkeypatch.setenv("ANALYZE_REQUIRE_ONCHAIN_TASK", "0")

    assert _post(_request_body()).status_code == 200
    assert calls.count("analyze") == 1


def _enable_relayer(monkeypatch, encrypted_data: str, *, receipt, captured=None):
    input_hash = "0x" + keccak(text=encrypted_data).hex()
    monkeypatch.setattr(main.relayer, "is_configured", lambda: True)
    monkeypatch.setattr(
        main.relayer,
        "task_state_with_input",
        lambda task_id: (1, int(time.time()), input_hash),
    )
    monkeypatch.setattr(main.relayer, "task_status", lambda task_id: 1)
    submitted: dict = {}

    def fake_submit(task_id, result_hash, attestation_sig):
        submitted.update(
            task_id=task_id,
            result_hash=result_hash,
            attestation_sig=attestation_sig,
        )
        return "0x" + "ab" * 32

    monkeypatch.setattr(main.relayer, "submit_result", fake_submit)
    def fake_wait(tx_hash, timeout):
        if captured is not None:
            captured["timeout"] = timeout
        return receipt

    monkeypatch.setattr(main.relayer, "wait_for_receipt", fake_wait)
    return submitted


def test_relayer_submission_reports_confirmed_receipt(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    body = _request_body(task_id=7)
    submitted = _enable_relayer(
        monkeypatch, body["encrypted_data"], receipt=True
    )

    response = _post(body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["onchain_submitted"] is True
    assert payload["onchain_confirmed"] is True
    assert submitted["task_id"] == 7
    assert submitted["attestation_sig"] == "0x" + "11" * 65


def test_relayer_revert_is_reported(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    body = _request_body(task_id=8)
    _enable_relayer(monkeypatch, body["encrypted_data"], receipt=False)

    payload = _post(body).json()

    assert payload["onchain_submitted"] is True
    assert payload["onchain_confirmed"] is False


def test_relayer_unconfirmed_receipt_is_reported(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    body = _request_body(task_id=9)
    _enable_relayer(monkeypatch, body["encrypted_data"], receipt=None)

    payload = _post(body).json()

    assert payload["onchain_submitted"] is True
    assert payload["onchain_confirmed"] is None


def test_total_budget_caps_the_receipt_wait(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    body = _request_body(task_id=10)
    captured: dict = {}
    _enable_relayer(
        monkeypatch, body["encrypted_data"], receipt=True, captured=captured
    )
    monkeypatch.setenv("ONCHAIN_RECEIPT_TIMEOUT", "15")
    # Simulate an exhausted budget deterministically (wall-clock timing is too
    # coarse on Windows to rely on a tiny budget here).
    monkeypatch.setattr(
        main, "_analyze_deadline", lambda: main.time.monotonic() - 10.0
    )

    payload = _post(body).json()

    # The budget is spent by the time the tx is broadcast, so the optional
    # receipt wait is skipped instead of blowing the 60s origin limit.
    assert payload["onchain_submitted"] is True
    assert captured["timeout"] == 0.0


def test_receipt_wait_uses_the_configured_timeout_within_budget(
    analyze_env, monkeypatch
):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    body = _request_body(task_id=11)
    captured: dict = {}
    _enable_relayer(
        monkeypatch, body["encrypted_data"], receipt=True, captured=captured
    )
    monkeypatch.setenv("ONCHAIN_RECEIPT_TIMEOUT", "15")
    monkeypatch.setattr(
        main, "_analyze_deadline", lambda: main.time.monotonic() + 600.0
    )

    _post(body)

    assert captured["timeout"] == 15.0


def test_oversized_body_is_rejected_before_parsing(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    body = _request_body()
    body["padding"] = "x" * (main.DEFAULT_MAX_REQUEST_BODY_BYTES + 4096)

    response = _post(body)

    assert response.status_code == 413
    assert "request body" in response.json()["detail"]
    # The middleware must reject before decryption/analysis work starts.
    assert calls == []


def test_rate_limit_returns_429_with_retry_after(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE", "0")

    first = _post(_request_body())
    second = _post(_request_body())

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers.get("retry-after")
    assert "too many analysis requests" in second.json()["detail"]
    # The throttled request must not start another analysis.
    assert calls.count("analyze") == 1


def test_chunked_body_is_capped_without_content_length(
    analyze_env, monkeypatch
):
    """A chunked upload has no Content-Length, so the stream cap must kick in."""
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    chunk = b"x" * 65536
    chunks = [chunk] * (
        main.DEFAULT_MAX_REQUEST_BODY_BYTES // len(chunk) + 2
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/analyze", content=iter(chunks), headers={"content-type": "application/json"}
        )

    assert response.status_code == 413
    assert calls == []


def test_oversized_body_keeps_cors_headers_for_allowed_origin(
    analyze_env, monkeypatch
):
    """The early 413 must not look like a CORS failure to the browser."""
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    body = _request_body()
    body["padding"] = "x" * (main.DEFAULT_MAX_REQUEST_BODY_BYTES + 4096)

    with TestClient(main.app) as client:
        allowed = client.post(
            "/analyze", json=body, headers={"Origin": "http://localhost:3100"}
        )
        denied = client.post(
            "/analyze", json=body, headers={"Origin": "https://evil.example"}
        )

    assert allowed.status_code == 413
    assert (
        allowed.headers.get("access-control-allow-origin")
        == "http://localhost:3100"
    )
    assert denied.status_code == 413
    assert "access-control-allow-origin" not in denied.headers
    assert calls == []


def test_decryption_errors_do_not_leak_internals(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    marker = "internal-detail-marker"

    def exploding_decrypt(ciphertext):  # noqa: ARG001 - stub
        raise RuntimeError(marker)

    monkeypatch.setattr(main.tee_keys, "decrypt", exploding_decrypt)

    response = _post(_request_body())

    assert response.status_code == 400
    assert marker not in response.json()["detail"]


def test_client_pubkey_must_be_valid_hex(analyze_env, monkeypatch):
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(
        main.tee_keys, "decrypt", lambda ciphertext: _plaintext("04" + "zz" * 64)
    )

    response = _post(_request_body())

    assert response.status_code == 400
    assert "client_pubkey" in response.json()["detail"]
    assert "analyze" not in calls
