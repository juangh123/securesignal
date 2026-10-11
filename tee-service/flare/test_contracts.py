"""Unit tests for flare/contracts.py nonce allocation and task parsing."""

import pytest
from eth_account import Account
from web3 import Web3 as RealWeb3
from web3.exceptions import ContractLogicError

from flare import contracts

DEV_PRIVATE_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)


@pytest.fixture(autouse=True)
def _clear_nonce_cache():
    with contracts._nonce_lock:
        contracts._next_nonce.clear()
        contracts._failed_nonces.clear()
    yield
    with contracts._nonce_lock:
        contracts._next_nonce.clear()
        contracts._failed_nonces.clear()


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


def test_failed_nonce_fills_the_gap_before_new_nonces():
    """A failed broadcast must not leave a hole the chain would stall on."""
    w3 = _FakeW3(5)
    assert contracts._allocate_nonce(w3, "0xabc") == 5
    assert contracts._allocate_nonce(w3, "0xabc") == 6

    contracts._release_nonce("0xabc", 5)

    # The failed nonce is handed out first even though 6 is already in flight.
    assert contracts._allocate_nonce(w3, "0xabc") == 5
    assert contracts._allocate_nonce(w3, "0xabc") == 7


def test_stale_failed_nonce_is_discarded():
    """A queued nonce the chain already moved past must never be reused."""
    contracts._release_nonce("0xabc", 5)
    contracts._next_nonce["0xabc"] = 6

    # The node reports 9: slot 5 was consumed by a later transaction.
    assert contracts._allocate_nonce(_FakeW3(9), "0xabc") == 9
    assert contracts._failed_nonces.get("0xabc") is None


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


def test_parse_task_result_prefers_named_fields():
    class _NamedTask:
        user = "0xuser"
        inputDataHash = "0x" + "33" * 32
        resultHash = "0x" + "00" * 32
        requestedAt = 999
        completedAt = 0
        status = 2

    assert contracts._parse_task_result(_NamedTask()) == (
        2,
        999,
        "0x" + "33" * 32,
    )


class _FakeTime:
    def __init__(self, values):
        self._values = iter(values)
        self.sleeps = 0

    def monotonic(self):
        return next(self._values, 1000.0)

    def sleep(self, _seconds):
        self.sleeps += 1


class _FakeReceiptEth:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)

    def get_transaction_receipt(self, _tx_hash):
        outcome = self._outcomes.pop(0) if self._outcomes else ValueError("pending")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _install_receipt_web3(monkeypatch, outcomes):
    fake_eth = _FakeReceiptEth(outcomes)

    class _FakeW3:
        def __init__(self, _provider):
            self.eth = fake_eth

    monkeypatch.setattr(contracts, "Web3", _FakeW3)
    monkeypatch.setattr(contracts, "make_provider", lambda url, timeout: object())


def test_wait_for_receipt_reports_success(monkeypatch):
    _install_receipt_web3(monkeypatch, [ValueError("pending"), {"status": 1}])
    monkeypatch.setattr(contracts, "time", _FakeTime([0.0, 0.1, 0.2]))

    assert contracts.wait_for_receipt("0xdead", timeout=5.0) is True


def test_wait_for_receipt_reports_revert(monkeypatch):
    _install_receipt_web3(monkeypatch, [{"status": 0}])
    monkeypatch.setattr(contracts, "time", _FakeTime([0.0, 0.1]))

    assert contracts.wait_for_receipt("0xdead", timeout=5.0) is False


def test_wait_for_receipt_times_out_unknown(monkeypatch):
    _install_receipt_web3(monkeypatch, [])
    monkeypatch.setattr(contracts, "time", _FakeTime([0.0, 1.0, 2.0]))

    assert contracts.wait_for_receipt("0xdead", timeout=1.5) is None


def test_wait_for_receipt_skips_ipc_and_zero_timeout(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("provider must not be built")

    monkeypatch.setattr(contracts, "make_provider", _boom)

    assert contracts.wait_for_receipt("0xdead", timeout=5.0, rpc_url="ipc:///tmp/x") is None
    assert contracts.wait_for_receipt("0xdead", timeout=0.0) is None


def test_task_state_failure_is_logged_once_per_window(monkeypatch, capsys):
    """Silent fail-closed paths must still leave an operational trace."""
    monkeypatch.setattr(contracts, "_last_warning_at", 0.0)
    monkeypatch.setattr(contracts, "_load_registry_address", lambda: "0x" + "11" * 20)

    def boom(*_args, **_kwargs):
        raise ConnectionError("rpc down")

    monkeypatch.setattr(contracts, "Web3", boom)

    assert contracts.task_state_with_input(1) is None
    out = capsys.readouterr().out
    assert "[relayer]" in out
    assert "rpc down" in out

    # A second failure inside the throttle window stays quiet.
    assert contracts.task_state_with_input(2) is None
    assert capsys.readouterr().out == ""


def test_safe_rpc_label_strips_path_and_query():
    from flare.rpc import safe_rpc_label

    assert (
        safe_rpc_label("https://host.example:8545/ext/C/rpc?apikey=SECRET")
        == "https://host.example:8545"
    )
    assert safe_rpc_label("ipc:///tmp/coston2-rpc.sock") == (
        "the configured RPC endpoint"
    )
    assert safe_rpc_label("not a url") == "the configured RPC endpoint"


def test_redact_rpc_url_removes_path_and_query_fragments():
    from flare.rpc import redact_rpc_url

    url = "https://host.example:8545/ext/C/rpc?apikey=SECRET"
    text = (
        "HTTPConnectionPool(host='host.example', port=8545): "
        f"Max retries exceeded with url: /ext/C/rpc?apikey=SECRET"
    )

    cleaned = redact_rpc_url(text, url)

    assert "SECRET" not in cleaned
    assert "/ext/C/rpc" not in cleaned


def _install_scheme_probe_web3(monkeypatch, call_impl):
    monkeypatch.setattr(contracts, "_load_registry_address", lambda: "0x" + "11" * 20)
    monkeypatch.setattr(contracts, "make_provider", lambda url, timeout: object())

    class _FakeContract:
        def __init__(self, *_args, **_kwargs):
            class _Functions:
                def ATTESTATION_SCHEME(self):
                    class _Call:
                        def call(self):
                            return call_impl()

                    return _Call()

            self.functions = _Functions()

    class _FakeW3:
        to_checksum_address = staticmethod(RealWeb3.to_checksum_address)

        def __init__(self, _provider):
            class _Eth:
                def contract(self, *_args, **_kwargs):
                    return _FakeContract()

            self.eth = _Eth()

    monkeypatch.setattr(contracts, "Web3", _FakeW3)


def test_attestation_scheme_probe_treats_revert_as_legacy(monkeypatch):
    """A v1 registry has no marker: the revert is a definitive legacy answer."""

    def revert():
        raise ContractLogicError("execution reverted")

    _install_scheme_probe_web3(monkeypatch, revert)

    assert (
        contracts.registry_attestation_scheme()
        == contracts.SCHEME_EIP191_LEGACY
    )


def test_attestation_scheme_probe_transport_error_stays_inconclusive(monkeypatch):
    """A transport failure is NOT a legacy answer; the signing path must not guess."""

    def transport_failure():
        raise ConnectionError("rpc down")

    _install_scheme_probe_web3(monkeypatch, transport_failure)

    assert contracts.registry_attestation_scheme() is None


class _FakeBuilder:
    def __init__(self, captured, args):
        self._captured = captured
        self._args = args

    def build_transaction(self, overrides):
        self._captured["args"] = self._args
        self._captured["overrides"] = dict(overrides)
        return {
            "from": overrides["from"],
            "to": "0x" + "11" * 20,
            "nonce": overrides["nonce"],
            "gas": overrides["gas"],
            "gasPrice": overrides["gasPrice"],
            "chainId": overrides["chainId"],
            "value": 0,
            "data": "0x1234",
        }


class _FakeFunctions:
    def __init__(self, captured):
        self._captured = captured

    def submitResult(self, task_id, result_hash, attestation):
        return _FakeBuilder(self._captured, (task_id, result_hash, attestation))


class _FakeContract:
    def __init__(self, captured):
        self.functions = _FakeFunctions(captured)


class _FakeSubmitEth:
    def __init__(self, captured, *, fail_broadcast=False, nonce=5):
        self.account = Account
        self.chain_id = 114
        self.gas_price = 10**9
        self._captured = captured
        self._nonce = nonce
        self._fail_broadcast = fail_broadcast

    def get_transaction_count(self, _address, block=None):
        return self._nonce

    def contract(self, address=None, abi=None):
        return _FakeContract(self._captured)

    def send_raw_transaction(self, raw):
        if self._fail_broadcast:
            raise ConnectionError("broadcast rejected")
        self._captured["raw"] = bytes(raw)

        class _TxHash:
            def hex(self):
                return "0x" + "ab" * 32

        return _TxHash()


def _install_submit_web3(monkeypatch, captured, *, fail_broadcast=False):
    eth = _FakeSubmitEth(captured, fail_broadcast=fail_broadcast)

    class _FakeW3:
        to_checksum_address = staticmethod(RealWeb3.to_checksum_address)

        def __init__(self, _provider):
            self.eth = eth

        def is_connected(self):
            return True

    monkeypatch.setattr(contracts, "Web3", _FakeW3)
    monkeypatch.setattr(contracts, "make_provider", lambda url, timeout: object())
    return eth, _FakeW3


def _prepare_submit(monkeypatch, captured, *, fail_broadcast=False):
    monkeypatch.setenv("PRIVATE_KEY", DEV_PRIVATE_KEY)
    monkeypatch.setattr(
        contracts, "_load_registry_address", lambda: "0x" + "11" * 20
    )
    monkeypatch.setattr(contracts, "_load_abi", lambda: [])
    return _install_submit_web3(monkeypatch, captured, fail_broadcast=fail_broadcast)


def test_submit_result_builds_signs_and_broadcasts(monkeypatch):
    captured: dict = {}
    _prepare_submit(monkeypatch, captured)

    tx_hash = contracts.submit_result(
        7,
        "0x" + "22" * 32,
        "0x" + "33" * 65,
        rpc_url="http://127.0.0.1:8545",
    )

    assert tx_hash == "0x" + "ab" * 32
    assert captured["overrides"]["nonce"] == 5
    assert captured["overrides"]["gas"] == 300_000
    assert captured["overrides"]["chainId"] == 114
    assert captured["args"][0] == 7
    assert captured["args"][1] == bytes.fromhex("22" * 32)
    assert len(captured["args"][2]) == 65
    assert captured["raw"]  # signed payload actually reached the wire


def test_submit_result_requeues_nonce_on_broadcast_failure(monkeypatch):
    captured: dict = {}
    _eth, fake_w3 = _prepare_submit(monkeypatch, captured, fail_broadcast=True)
    account_address = Account.from_key(DEV_PRIVATE_KEY).address

    with pytest.raises(ConnectionError):
        contracts.submit_result(
            1,
            "0x" + "22" * 32,
            "0x" + "33" * 65,
            rpc_url="http://127.0.0.1:8545",
        )

    # The failed nonce is queued, then handed out again before any fresh one.
    assert contracts._failed_nonces[account_address.lower()] == {5}
    assert contracts._allocate_nonce(fake_w3(object()), account_address) == 5
    assert contracts._failed_nonces.get(account_address.lower()) is None
