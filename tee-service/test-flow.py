"""
End-to-end self-test of the TEE service ECIES protocol + unified analysis schema.

Prereq: server running, e.g. (from tee-service/):
    ANALYSIS_OFFLINE=1 uvicorn main:app --port 8000

Verifies:
  [1] GET /public-key returns 130-hex-char (65B) uncompressed key, 04 prefix
  [2] POST /analyze with an ECIES-encrypted payload decrypts server-side
  [3] encrypted_result decrypts client-side (full roundtrip) AND the result
      conforms to the unified analysis schema (all required fields + types);
      analysis_mode matches the env (no LLM_API_KEY -> "rule-fallback")
  [4] result_hash == keccak256(decrypted result_json)
  [5] attestation token is structured JSON with a valid TEE signature that
      ecrecovers to the token's tee_address
  [6] onchain_submitted flag reported
  [7] mock LLM (valid JSON): local HTTP mock + LLM_BASE_URL -> the engine
      really walks the LLM path (analysis_mode == "llm", content asserted)
  [8] mock LLM (malformed JSON): engine retries once (2 HTTP requests seen)
      then falls back to the rule engine (analysis_mode == "rule-fallback")

Checks [7]/[8] run in-process against analysis.engine with ANALYSIS_OFFLINE=1
(fixture prices) and a threaded localhost HTTP mock standing in for the
OpenAI-compatible API.
"""

import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from coincurve import PrivateKey
from ecies import decrypt as ecies_decrypt
from ecies import encrypt as ecies_encrypt
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

REBALANCE_ACTIONS = ("increase", "decrease", "hold")


def assert_analysis_schema(result: dict, context: str) -> None:
    """Assert the unified analysis result schema: all required fields + types."""
    assert isinstance(result, dict), f"{context}: result must be a dict"
    required = (
        "status", "analysis_mode", "price_source", "prices_used",
        "total_value_usd", "holdings", "risk_score", "risk_level",
        "rebalance", "summary",
    )
    for field in required:
        assert field in result, f"{context}: missing required field '{field}'"

    assert result["status"] == "success", f"{context}: status={result.get('status')!r} error={result.get('error')!r}"
    assert result["analysis_mode"] in ("llm", "rule-fallback"), \
        f"{context}: bad analysis_mode {result['analysis_mode']!r}"
    assert isinstance(result["price_source"], str) and result["price_source"], \
        f"{context}: price_source must be a non-empty str"

    prices = result["prices_used"]
    assert isinstance(prices, dict) and prices, f"{context}: prices_used must be a non-empty dict"
    for sym, price in prices.items():
        assert isinstance(sym, str), f"{context}: prices_used key {sym!r} must be str"
        assert isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0, \
            f"{context}: prices_used[{sym!r}] must be a positive number, got {price!r}"

    total = result["total_value_usd"]
    assert isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0, \
        f"{context}: total_value_usd must be a positive number, got {total!r}"

    holdings = result["holdings"]
    assert isinstance(holdings, list) and holdings, f"{context}: holdings must be a non-empty list"
    weight_sum = 0.0
    for i, h in enumerate(holdings):
        assert isinstance(h, dict), f"{context}: holdings[{i}] must be an object"
        for field in ("symbol", "amount", "value_usd", "weight_pct"):
            assert field in h, f"{context}: holdings[{i}] missing field '{field}'"
        assert isinstance(h["symbol"], str) and h["symbol"], \
            f"{context}: holdings[{i}].symbol must be a non-empty str"
        for field in ("amount", "value_usd", "weight_pct"):
            assert isinstance(h[field], (int, float)) and not isinstance(h[field], bool), \
                f"{context}: holdings[{i}].{field} must be a number, got {h[field]!r}"
        assert h["amount"] > 0, f"{context}: holdings[{i}].amount must be > 0"
        assert h["value_usd"] >= 0, f"{context}: holdings[{i}].value_usd must be >= 0"
        assert 0 <= h["weight_pct"] <= 100, \
            f"{context}: holdings[{i}].weight_pct out of range: {h['weight_pct']}"
        weight_sum += h["weight_pct"]
    assert abs(weight_sum - 100.0) < 0.5, f"{context}: weight_pct sum = {weight_sum}, expected ~100"

    score = result["risk_score"]
    assert isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100, \
        f"{context}: risk_score must be int in 0-100, got {score!r}"
    assert result["risk_level"] in ("low", "medium", "high"), \
        f"{context}: bad risk_level {result['risk_level']!r}"

    rebalance = result["rebalance"]
    assert isinstance(rebalance, list) and rebalance, f"{context}: rebalance must be a non-empty list"
    for i, item in enumerate(rebalance):
        assert isinstance(item, dict), f"{context}: rebalance[{i}] must be an object"
        assert item.get("action") in REBALANCE_ACTIONS, \
            f"{context}: rebalance[{i}].action must be one of {REBALANCE_ACTIONS}, got {item.get('action')!r}"
        assert isinstance(item.get("symbol"), str) and item["symbol"], \
            f"{context}: rebalance[{i}].symbol must be a non-empty str"
        assert isinstance(item.get("reason"), str) and item["reason"].strip(), \
            f"{context}: rebalance[{i}].reason must be a non-empty str"

    assert isinstance(result["summary"], str) and result["summary"].strip(), \
        f"{context}: summary must be a non-empty str"


# --- mock OpenAI-compatible LLM server (checks 7/8) ---------------------------

MOCK_LLM_VALID_ANALYSIS = {
    "risk_score": 55,
    "risk_level": "medium",
    "rebalance": [
        {"action": "hold", "symbol": "BTC", "reason": "BTC weight is reasonable — keep it as a core position."},
        {"action": "decrease", "symbol": "ETH", "reason": "Example: ETH is volatile — consider trimming slightly to lock in gains."},
    ],
    "summary": "Portfolio valuation looks reasonable, BTC and ETH weights are balanced, concentration is moderate — keep core positions and watch market volatility.",
}


class _MockLLMHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible /v1/chat/completions mock. Class attrs steer behavior."""

    assistant_content: str = "{}"  # raw `content` string returned to the client
    requests_seen: int = 0

    def do_POST(self):  # noqa: N802 (http.server API)
        type(self).requests_seen += 1
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # drain request body
        envelope = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": "mock-llm",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": type(self).assistant_content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence stderr logging
        pass


def run_mock_llm_checks() -> None:
    from analysis import engine as analysis_engine

    # Isolate env: offline fixture prices + LLM pointed at the local mock.
    saved_env = {k: os.environ.get(k) for k in (
        "ANALYSIS_OFFLINE", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT")}
    server = None
    try:
        os.environ["ANALYSIS_OFFLINE"] = "1"
        os.environ["LLM_API_KEY"] = "mock-key-for-local-test"
        os.environ["LLM_MODEL"] = "mock-llm"
        os.environ["LLM_TIMEOUT"] = "10"

        server = ThreadingHTTPServer(("127.0.0.1", 0), _MockLLMHandler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{port}/v1"

        payload = {"holdings": {"BTC": 2.0, "ETH": 10.0}, "risk_profile": "moderate"}
        # fixture math: 2*65000 + 10*3500 = 165000
        expected_total = 2.0 * 65000.0 + 10.0 * 3500.0

        # [7] valid mock JSON -> analysis_mode == "llm", content asserted
        _MockLLMHandler.assistant_content = json.dumps(MOCK_LLM_VALID_ANALYSIS, ensure_ascii=False)
        _MockLLMHandler.requests_seen = 0
        result = analysis_engine.analyze_portfolio(payload)
        assert_analysis_schema(result, "mock-llm-valid")
        assert result["analysis_mode"] == "llm", \
            f"mock-llm-valid: expected analysis_mode='llm', got {result['analysis_mode']!r}"
        assert result["risk_score"] == 55, f"mock-llm-valid: risk_score={result['risk_score']}"
        assert result["risk_level"] == "medium"
        assert result["summary"] == MOCK_LLM_VALID_ANALYSIS["summary"], \
            "mock-llm-valid: summary must come from the LLM, not the rule engine"
        assert result["rebalance"] == MOCK_LLM_VALID_ANALYSIS["rebalance"]
        assert result["price_source"] == "offline-fixture"
        assert result["prices_used"] == {"BTC": 65000.0, "ETH": 3500.0}
        assert abs(result["total_value_usd"] - expected_total) < 0.01
        assert _MockLLMHandler.requests_seen == 1, \
            f"mock-llm-valid: expected exactly 1 LLM HTTP request, saw {_MockLLMHandler.requests_seen}"
        print(f"[7] mock LLM (valid JSON) OK: analysis_mode=llm, risk_score={result['risk_score']}, "
              f"risk_level={result['risk_level']}, summary from mock verified, "
              f"total_value_usd={result['total_value_usd']}, LLM HTTP requests=1")

        # [8] malformed mock JSON -> one retry (2 requests), then rule fallback
        _MockLLMHandler.assistant_content = "抱歉，我无法处理这个请求。[[not-json{{"
        _MockLLMHandler.requests_seen = 0
        result = analysis_engine.analyze_portfolio(payload)
        assert_analysis_schema(result, "mock-llm-malformed")
        assert result["analysis_mode"] == "rule-fallback", \
            f"mock-llm-malformed: expected analysis_mode='rule-fallback', got {result['analysis_mode']!r}"
        assert _MockLLMHandler.requests_seen == 2, \
            f"mock-llm-malformed: expected 2 LLM HTTP requests (1 retry), saw {_MockLLMHandler.requests_seen}"
        assert "rule engine" in result["summary"], \
            "mock-llm-malformed: summary must disclose the rule-engine fallback"
        assert result["prices_used"] == {"BTC": 65000.0, "ETH": 3500.0}, \
            "mock-llm-malformed: fallback must still use REAL provider prices"
        print(f"[8] mock LLM (malformed JSON) OK: retried once (HTTP requests=2), "
              f"fell back to rule-fallback, risk_score={result['risk_score']}, "
              f"risk_level={result['risk_level']}")
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    # 1. /public-key format check
    r = requests.get(f"{BASE_URL}/public-key", timeout=10)
    r.raise_for_status()
    tee_pub = r.json()["public_key"]
    assert isinstance(tee_pub, str), "public_key must be str"
    assert len(tee_pub) == 130, f"public_key must be 130 hex chars, got {len(tee_pub)}"
    assert tee_pub.startswith("04"), "public_key must start with 04 (uncompressed)"
    int(tee_pub, 16)  # valid hex
    print(f"[1] /public-key OK: {tee_pub[:20]}... ({len(tee_pub)} hex chars)")

    # 2. Simulate client: fresh session keypair, encrypt payload to TEE key
    client_key = PrivateKey()
    client_priv_hex = client_key.secret.hex()
    client_pub_hex = client_key.public_key.format(compressed=False).hex()

    payload = {
        "client_pubkey": client_pub_hex,
        "holdings": {"BTC": 2.0, "ETH": 10.0},
        "risk_profile": "moderate",
    }
    payload_json = json.dumps(payload)
    encrypted_data = base64.b64encode(
        ecies_encrypt(tee_pub, payload_json.encode("utf-8"))
    ).decode("ascii")

    # 3. POST /analyze
    r = requests.post(
        f"{BASE_URL}/analyze",
        json={"task_id": 1, "encrypted_data": encrypted_data},
        timeout=30,
    )
    assert r.status_code == 200, f"/analyze -> {r.status_code}: {r.text}"
    resp = r.json()
    print(f"[2] /analyze OK: keys = {sorted(resp.keys())}")

    # 4. Decrypt encrypted_result with the client session private key,
    #    then assert the unified analysis schema.
    result_json = ecies_decrypt(
        client_priv_hex, base64.b64decode(resp["encrypted_result"])
    ).decode("utf-8")
    result = json.loads(result_json)
    assert_analysis_schema(result, "e2e")
    # Env-consistent expectations: this test is prescribed to run against a
    # server started with ANALYSIS_OFFLINE=1 and WITHOUT LLM_API_KEY, i.e.
    # offline fixtures + rule-fallback.
    expected_mode = "llm" if os.environ.get("LLM_API_KEY", "").strip() else "rule-fallback"
    assert result["analysis_mode"] == expected_mode, (
        f"e2e: analysis_mode={result['analysis_mode']!r}, expected {expected_mode!r} "
        f"(server env must mirror this test's LLM_API_KEY presence)"
    )
    if os.environ.get("ANALYSIS_OFFLINE", "").strip() == "1":
        assert result["price_source"] == "offline-fixture", \
            f"e2e: expected offline-fixture prices, got {result['price_source']!r}"
        assert result["prices_used"] == {"BTC": 65000.0, "ETH": 3500.0}
    print(f"[3] roundtrip decrypt OK: schema verified (10 required fields), "
          f"analysis_mode={result['analysis_mode']}, price_source={result['price_source']}, "
          f"total_value_usd={result['total_value_usd']}, risk_score={result['risk_score']}, "
          f"risk_level={result['risk_level']}, rebalance={len(result['rebalance'])} items")

    # 5. Verify result_hash == keccak256(result_json)
    expected_hash = "0x" + keccak(text=result_json).hex()
    assert resp["result_hash"] == expected_hash, (
        f"result_hash mismatch: {resp['result_hash']} != {expected_hash}"
    )
    print(f"[4] result_hash OK: {resp['result_hash'][:18]}... == keccak256(result_json)")

    # 6. Verify attestation structure + signature
    token = json.loads(resp["attestation"])
    for field in ("task_id", "result_hash", "image_digest", "tee_address",
                  "timestamp", "mode", "signature"):
        assert field in token, f"attestation missing field: {field}"
    assert token["task_id"] == 1
    assert token["result_hash"] == resp["result_hash"]
    assert token["mode"] == "dev-simulated"

    # EIP-191 personal_sign over the RAW 64-byte packed message
    # abi.encodePacked(uint256 taskId, bytes32 resultHash) — prefix "\n64",
    # matching AnalysisRegistry._verifyAttestation exactly.
    packed64 = (1).to_bytes(32, "big") + bytes.fromhex(resp["result_hash"][2:])
    assert len(packed64) == 64
    recovered = Account.recover_message(
        encode_defunct(primitive=packed64), signature=token["signature"]
    )
    assert recovered.lower() == token["tee_address"].lower(), (
        f"attestation signature recovers to {recovered}, "
        f"expected {token['tee_address']}"
    )
    print(f"[5] attestation OK: mode={token['mode']}, "
          f"tee_address={token['tee_address']}, signature verified via ecrecover")

    print(f"[6] onchain_submitted = {resp.get('onchain_submitted')} "
          f"(relayer {'configured' if resp.get('onchain_submitted') else 'not configured — expected locally'})")

    # 7/8. In-process mock LLM checks (engine -> real HTTP to local mock).
    run_mock_llm_checks()

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
