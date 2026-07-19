"""
Unit tests for analysis/price_provider.py.

Run from tee-service/:
    python -m unittest analysis.test_price_provider -v
or directly:
    python analysis/test_price_provider.py

Live online test against the real Coston2 RPC (skipped by default):
    ANALYSIS_LIVE_TEST=1 python -m unittest analysis.test_price_provider -v
"""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

from web3 import Web3 as RealWeb3

try:
    from analysis import price_provider
except ImportError:  # running as `python analysis/test_price_provider.py`
    import price_provider


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

def _clear_caches() -> None:
    with price_provider._lock:
        price_provider._price_cache.clear()
        price_provider._address_cache = None
        price_provider._detected_chain_name = None


class _EnvTestCase(unittest.TestCase):
    """Save/restore env and module caches around every test."""

    def setUp(self) -> None:
        self._saved_env = {
            k: os.environ.get(k) for k in ("ANALYSIS_OFFLINE", "RPC_URL")
        }
        _clear_caches()

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _clear_caches()

    def _set_offline(self, offline: bool) -> None:
        if offline:
            os.environ["ANALYSIS_OFFLINE"] = "1"
        else:
            os.environ.pop("ANALYSIS_OFFLINE", None)


# ---------------------------------------------------------------------------
# Fake web3 stack for online-logic tests (no network)
# ---------------------------------------------------------------------------

FTSO_ADDR = "0x1000000000000000000000000000000000000001"


class _FakeHTTPProvider:
    def __init__(self, endpoint_uri=None, request_kwargs=None):
        self.endpoint_uri = endpoint_uri
        self.request_kwargs = request_kwargs or {}


class _FakeCall:
    def __init__(self, fn):
        self._fn = fn

    def call(self, *args, **kwargs):
        return self._fn()


class _FakeFunctionsNS:
    """contract.functions.<name>(*args) -> object with .call()"""

    def __init__(self, handlers):
        self._handlers = handlers

    def __getattr__(self, name):
        handler = self._handlers[name]

        def build(*args):
            return _FakeCall(lambda: handler(*args))

        return build


class _FakeContract:
    def __init__(self, handlers):
        self.functions = _FakeFunctionsNS(handlers)


class _FakeEth:
    def __init__(self, w3):
        self._w3 = w3

    @property
    def chain_id(self):
        return type(self._w3).chain_id_value

    def contract(self, address=None, abi=None):
        w3cls = type(self._w3)
        if address == RealWeb3.to_checksum_address(
            price_provider.FLARE_CONTRACT_REGISTRY_ADDRESS
        ):
            def registry_handler(name):
                w3cls.recorder["registry_names"].append(name)
                return w3cls.registry_result

            return _FakeContract({"getContractAddressByName": registry_handler})

        if address == RealWeb3.to_checksum_address(w3cls.registry_result):
            def feed_handler(feed_id):
                w3cls.recorder["feed_ids"].append(feed_id)
                return w3cls.feed_handler(feed_id)

            return _FakeContract({"getFeedById": feed_handler})

        raise AssertionError(f"unexpected contract address {address}")


class _FakeWeb3:
    """Drop-in for the module-level Web3 symbol; class attrs carry the setup."""

    HTTPProvider = _FakeHTTPProvider
    to_checksum_address = staticmethod(RealWeb3.to_checksum_address)
    to_bytes = staticmethod(RealWeb3.to_bytes)

    # per-test configuration (class-level; tests run sequentially)
    connected = True
    chain_id_value = 114  # Coston2
    registry_result = FTSO_ADDR
    feed_handler = staticmethod(lambda feed_id: (12345678, 4, 1_700_000_000))
    recorder = None

    def __init__(self, provider=None):
        self.provider = provider
        self.eth = _FakeEth(self)

    def is_connected(self):
        return type(self).connected


def _reset_fake_web3() -> None:
    _FakeWeb3.connected = True
    _FakeWeb3.chain_id_value = 114
    _FakeWeb3.registry_result = FTSO_ADDR
    _FakeWeb3.feed_handler = staticmethod(
        lambda feed_id: (12345678, 4, 1_700_000_000)
    )
    _FakeWeb3.recorder = {"registry_names": [], "feed_ids": [], "providers": []}


# ---------------------------------------------------------------------------
# A) Offline fixture mode
# ---------------------------------------------------------------------------

class OfflineFixtureTests(_EnvTestCase):
    def setUp(self):
        super().setUp()
        self._set_offline(True)

    def test_fixture_prices_returned(self):
        prices = price_provider.get_prices(["BTC", "ETH", "FLR"])
        self.assertEqual(
            prices, {"BTC": 65000.0, "ETH": 3500.0, "FLR": 0.02}
        )

    def test_fixture_source_label(self):
        self.assertEqual(price_provider.get_price_source(), "offline-fixture")

    def test_fixture_subset_and_case_normalization(self):
        self.assertEqual(price_provider.get_prices(["btc"]), {"BTC": 65000.0})
        self.assertEqual(
            price_provider.get_prices(["eth", "FLR"]),
            {"ETH": 3500.0, "FLR": 0.02},
        )

    def test_fixture_mode_never_touches_network(self):
        # Even with an unreachable RPC_URL, offline mode must not do I/O.
        os.environ["RPC_URL"] = "http://127.0.0.1:1/unreachable"
        self.assertEqual(price_provider.get_prices(["BTC"]), {"BTC": 65000.0})

    def test_unknown_symbol_raises_offline(self):
        with self.assertRaises(ValueError):
            price_provider.get_prices(["DOGE"])
        with self.assertRaises(ValueError):
            price_provider.get_prices(["BTC", "DOGE"])
        with self.assertRaises(ValueError):
            price_provider.get_prices([123])


# ---------------------------------------------------------------------------
# B) Online mode with mocked web3 — logic, decoding, caching, failures
# ---------------------------------------------------------------------------

class OnlineMockedTests(_EnvTestCase):
    def setUp(self):
        super().setUp()
        self._set_offline(False)
        _reset_fake_web3()
        self._web3_patcher = mock.patch.object(price_provider, "Web3", _FakeWeb3)
        self._web3_patcher.start()
        self.addCleanup(self._web3_patcher.stop)

    # -- happy path / decoding --------------------------------------------

    def test_decode_scaling_and_call_flow(self):
        # value=12345678, decimals=4  ->  1234.5678 USD
        prices = price_provider.get_prices(["BTC", "ETH"])
        self.assertAlmostEqual(prices["BTC"], 1234.5678)
        self.assertAlmostEqual(prices["ETH"], 1234.5678)

        rec = _FakeWeb3.recorder
        # Registry was asked exactly once for the canonical name.
        self.assertEqual(rec["registry_names"], ["FtsoV2"])
        # Each symbol was read with its correct 21-byte feed ID.
        self.assertEqual(len(rec["feed_ids"]), 2)
        for feed_id, sym in zip(rec["feed_ids"], ["BTC", "ETH"]):
            self.assertIsInstance(feed_id, bytes)
            self.assertEqual(len(feed_id), 21)
            self.assertEqual(
                feed_id, RealWeb3.to_bytes(hexstr=price_provider.FEED_IDS[sym])
            )

    def test_rpc_timeout_is_10_seconds(self):
        captured = {}

        class RecordingProvider(_FakeHTTPProvider):
            def __init__(self, endpoint_uri=None, request_kwargs=None):
                super().__init__(endpoint_uri, request_kwargs)
                captured.update(self.request_kwargs)

        with mock.patch.object(_FakeWeb3, "HTTPProvider", RecordingProvider):
            price_provider.get_prices(["BTC"])
        self.assertEqual(captured.get("timeout"), 10)

    def test_negative_decimals_scaling(self):
        _FakeWeb3.feed_handler = staticmethod(lambda fid: (5, -1, 1_700_000_000))
        self.assertAlmostEqual(price_provider.get_prices(["BTC"])["BTC"], 50.0)

    def test_source_label_online(self):
        # Before any read: URL heuristic (default RPC -> coston2).
        self.assertEqual(price_provider.get_price_source(), "coston2-ftso")
        price_provider.get_prices(["BTC"])
        # After a successful read: chainId 114 detected -> still coston2.
        self.assertEqual(price_provider.get_price_source(), "coston2-ftso")

    def test_source_label_follows_detected_chain(self):
        _FakeWeb3.chain_id_value = 14  # Flare mainnet
        price_provider.get_prices(["BTC"])
        self.assertEqual(price_provider.get_price_source(), "flare-ftso")

    # -- caching -----------------------------------------------------------

    def test_cache_hit_avoids_second_rpc(self):
        price_provider.get_prices(["BTC"])
        price_provider.get_prices(["BTC"])
        self.assertEqual(len(_FakeWeb3.recorder["feed_ids"]), 1)

    def test_registry_address_cached(self):
        price_provider.get_prices(["BTC", "ETH", "FLR"])
        self.assertEqual(_FakeWeb3.recorder["registry_names"], ["FtsoV2"])

    def test_cache_expiry_refetches(self):
        price_provider.get_prices(["BTC"])
        with price_provider._lock:
            price, ts, _exp = price_provider._price_cache["BTC"]
            price_provider._price_cache["BTC"] = (price, ts, time.monotonic() - 1)
        price_provider.get_prices(["BTC"])
        self.assertEqual(len(_FakeWeb3.recorder["feed_ids"]), 2)

    # -- failure policy: raise, never fake ---------------------------------

    def test_disconnected_rpc_raises_no_fallback(self):
        _FakeWeb3.connected = False
        with self.assertRaises(price_provider.PriceProviderError):
            price_provider.get_prices(["BTC"])

    def test_zero_address_registry_raises(self):
        _FakeWeb3.registry_result = price_provider.ZERO_ADDRESS
        with self.assertRaises(price_provider.PriceProviderError):
            price_provider.get_prices(["BTC"])

    def test_nonpositive_value_raises(self):
        _FakeWeb3.feed_handler = staticmethod(lambda fid: (0, 8, 1_700_000_000))
        with self.assertRaises(price_provider.PriceProviderError):
            price_provider.get_prices(["BTC"])

    def test_invalid_timestamp_raises(self):
        _FakeWeb3.feed_handler = staticmethod(lambda fid: (100, 8, 0))
        with self.assertRaises(price_provider.PriceProviderError):
            price_provider.get_prices(["BTC"])

    def test_contract_revert_raises_no_fallback(self):
        def revert(fid):
            raise RuntimeError("execution reverted")

        _FakeWeb3.feed_handler = staticmethod(revert)
        with self.assertRaises(price_provider.PriceProviderError):
            price_provider.get_prices(["BTC"])

    def test_unknown_symbol_raises_before_any_network(self):
        with self.assertRaises(ValueError):
            price_provider.get_prices(["SHIB"])
        # No RPC was constructed / no contract call happened.
        self.assertEqual(_FakeWeb3.recorder["registry_names"], [])
        self.assertEqual(_FakeWeb3.recorder["feed_ids"], [])


# ---------------------------------------------------------------------------
# C) Live online test against real Coston2 RPC (opt-in via env var)
# ---------------------------------------------------------------------------

@unittest.skipUnless(
    os.environ.get("ANALYSIS_LIVE_TEST") == "1",
    "set ANALYSIS_LIVE_TEST=1 to run the live Coston2 RPC test",
)
class LiveCoston2Tests(_EnvTestCase):
    def setUp(self):
        super().setUp()
        self._set_offline(False)

    def test_live_prices_positive_and_fresh(self):
        now = time.time()
        details = {}
        for sym in ["BTC", "ETH", "FLR"]:
            price, feed_ts = price_provider._read_online(sym)
            details[sym] = (price, feed_ts)
            self.assertGreater(price, 0, f"{sym} price must be > 0")
            age = abs(now - feed_ts)
            self.assertLess(
                age, 24 * 3600, f"{sym} feed timestamp older than 24h"
            )

        # get_prices (its own fresh reads, feeds update ~every 90 s) must
        # broadly agree with the direct reads above.
        prices = price_provider.get_prices(["BTC", "ETH", "FLR"])
        for sym, (price, _ts) in details.items():
            self.assertAlmostEqual(
                prices[sym], price, delta=max(price * 0.05, 1e-8)
            )

        self.assertEqual(price_provider.get_price_source(), "coston2-ftso")

        print("\n--- LIVE Coston2 FtsoV2 reads ---")
        for sym, (price, feed_ts) in sorted(details.items()):
            utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(feed_ts))
            print(
                f"  {sym}/USD: ${price:,.6f}  "
                f"(feed ts {feed_ts} = {utc} UTC, age {int(now - feed_ts)}s)"
            )
        print(f"  price_source: {price_provider.get_price_source()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
