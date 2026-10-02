"""Unit tests for flare/contracts.py nonce allocation and task parsing."""

import pytest

from flare import contracts


@pytest.fixture(autouse=True)
def _clear_nonce_cache():
    with contracts._nonce_lock:
        contracts._next_nonce.clear()
    yield
    with contracts._nonce_lock:
        contracts._next_nonce.clear()


class _FakeEth:
    def __init__(self, nonce):
        self._nonce = nonce

    def get_transaction_count(self, address, block=None):
        return self._nonce


class _FakeW3:
    def __init__(self, nonce):
        self.eth = _FakeEth(nonce)


def test_allocate_nonce_uses_pending_and_local_cache():
    w3 = _FakeW3(5)
    assert contracts._allocate_nonce(w3, "0xAbC") == 5
    assert contracts._allocate_nonce(w3, "0xabc") == 6
    assert contracts._allocate_nonce(w3, "0xABC") == 7


def test_release_nonce_reuses_a_failed_nonce():
    w3 = _FakeW3(5)
    assert contracts._allocate_nonce(w3, "0xabc") == 5
    contracts._release_nonce("0xabc", 5)
    assert contracts._allocate_nonce(w3, "0xabc") == 5


def test_allocate_nonce_falls_back_to_latest():
    class _LatestOnlyEth:
        def get_transaction_count(self, address, block=None):
            if block == "pending":
                raise ValueError("pending is not supported")
            return 9

    class _LatestOnlyW3:
        eth = _LatestOnlyEth()

    assert contracts._allocate_nonce(_LatestOnlyW3(), "0xabc") == 9
    assert contracts._allocate_nonce(_LatestOnlyW3(), "0xabc") == 10


def test_parse_task_result_returns_status_time_and_input_hash():
    task = [
        "0xuser",
        bytes.fromhex("22" * 32),
        b"\x00" * 32,
        123456,
        0,
        1,
    ]
    assert contracts._parse_task_result(task) == (
        1,
        123456,
        "0x" + "22" * 32,
    )
