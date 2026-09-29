"""
Request-boundary tests for the FastAPI surface of the TEE service.

Offline by design: no RPC calls, no on-chain writes. These cover the caps that
protect /analyze from oversized or malformed requests before any decryption or
analysis work happens.
"""

from fastapi.testclient import TestClient

import main


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
