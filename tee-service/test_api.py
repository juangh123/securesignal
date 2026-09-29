"""FastAPI surface tests for the TEE service.

Offline by design: no RPC calls, no on-chain writes. Covers the public
``/public-key`` and the operational ``/health`` endpoints.
"""
from fastapi.testclient import TestClient

import main


def test_public_key_shape():
    with TestClient(main.app) as c:
        r = c.get("/public-key")
        assert r.status_code == 200
        body = r.json()
        assert body["public_key"].startswith("04")
        assert len(body["public_key"]) == 130
        assert body["address"].startswith("0x")
        assert len(body["address"]) == 42


def test_health_reports_non_secret_status():
    with TestClient(main.app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["version"] == main.SERVICE_VERSION
        for key in (
            "version",
            "tee_address",
            "registry_address",
            "relayer_configured",
            "price_mode",
            "llm_configured",
            "llm_model",
            "attestation_mode",
            "image_digest",
        ):
            assert key in body
        assert isinstance(body["relayer_configured"], bool)
        assert isinstance(body["llm_configured"], bool)
        # The endpoint must never expose key material or raw endpoints.
        flat = str(body)
        assert "PRIVATE_KEY" not in flat
        assert "http://" not in flat and "https://" not in flat


def test_assets_lists_priceable_symbols():
    """The frontend validates holdings against this list before spending gas."""
    with TestClient(main.app) as c:
        r = c.get("/assets")
        assert r.status_code == 200
        body = r.json()
        symbols = body["symbols"]
        assert isinstance(symbols, list) and symbols
        assert body["count"] == len(symbols)
        # the original three must still be there, and entries are unique + upper-case
        assert {"BTC", "ETH", "FLR"} <= set(symbols)
        assert len(set(symbols)) == len(symbols)
        assert all(s == s.upper() for s in symbols)
