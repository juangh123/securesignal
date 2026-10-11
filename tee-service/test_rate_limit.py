"""Unit tests for the /analyze rate limiter."""

import pytest

import main


class _Clock:
    """Stand-in for the ``time`` module so windows can be advanced instantly."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("ANALYZE_RATE_LIMIT_PER_MINUTE", raising=False)
    monkeypatch.delenv("ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE", raising=False)
    main._reset_analyze_caches()
    yield
    main._reset_analyze_caches()


def test_per_client_limit_blocks_only_that_client(monkeypatch):
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE", "0")

    assert main._check_rate_limit("a") is None
    assert main._check_rate_limit("a") is None
    assert main._check_rate_limit("a") is not None
    # A different client still has its own budget.
    assert main._check_rate_limit("b") is None


def test_per_client_window_recovers(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(main, "time", clock)
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE", "0")

    assert main._check_rate_limit("a") is None
    assert main._check_rate_limit("a") is not None

    clock.now += main.ANALYZE_RATE_LIMIT_WINDOW_SECONDS + 1
    assert main._check_rate_limit("a") is None


def test_global_limit_applies_across_clients(monkeypatch):
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE", "2")

    assert main._check_rate_limit("a") is None
    assert main._check_rate_limit("b") is None
    assert main._check_rate_limit("c") is not None


def test_rejected_requests_do_not_consume_global_budget(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(main, "time", clock)
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE", "2")

    assert main._check_rate_limit("a") is None
    assert main._check_rate_limit("a") is not None  # per-client rejection
    # The rejected attempt must not have burned the global slot.
    assert main._check_rate_limit("b") is None
    assert main._check_rate_limit("c") is not None


def test_limits_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE", "0")

    for _ in range(50):
        assert main._check_rate_limit("a") is None


def test_client_key_prefers_proxy_headers(monkeypatch):
    class _Client:
        host = "10.0.0.9"

    class _Request:
        client = _Client()

        def __init__(self, headers):
            self.headers = headers

    monkeypatch.delenv("ANALYZE_TRUST_PROXY_HEADERS", raising=False)
    assert main._client_key(_Request({"cf-connecting-ip": "203.0.113.7"})) == "203.0.113.7"
    # X-Forwarded-For is read from the right so a spoofed prefix is ignored.
    assert (
        main._client_key(_Request({"x-forwarded-for": "1.2.3.4, 203.0.113.8"}))
        == "203.0.113.8"
    )
    assert main._client_key(_Request({"x-real-ip": "203.0.113.9"})) == "203.0.113.9"
    assert main._client_key(_Request({})) == "10.0.0.9"

    monkeypatch.setenv("ANALYZE_TRUST_PROXY_HEADERS", "0")
    assert main._client_key(_Request({"cf-connecting-ip": "203.0.113.7"})) == "10.0.0.9"
