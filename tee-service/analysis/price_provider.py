"""
FTSO v2 price provider for the SecureSignal TEE analysis service.

PUBLIC INTERFACE (the analysis/engine.py integration worker codes against
exactly these two functions — do not rename or change their signatures):

    get_prices(symbols: list[str]) -> dict[str, float]
        Return human-readable USD prices (raw feed value scaled by the
        feed's own decimals). Keys are the upper-cased symbols.

    get_price_source() -> str
        "offline-fixture" when ANALYSIS_OFFLINE=1 (dev fixture mode);
        otherwise "<chain>-ftso" resolved dynamically from the connected
        chain — e.g. "coston2-ftso" for the default Coston2 RPC.

MODES
-----
1. Offline dev-fixture mode — env ANALYSIS_OFFLINE=1:
   Returns FIXTURE_PRICES ({BTC: 65000, ETH: 3500, FLR: 0.02}). These are
   development fixtures, NOT real market data. get_price_source() returns
   "offline-fixture" so callers can label their results accordingly.

2. Online mode (default) — real on-chain reads; no contract deployment
   needed because the official FtsoV2 contract already exists on Coston2:

     RPC_URL (env, default https://coston2-api.flare.network/ext/C/rpc)
       -> FlareContractRegistry (0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019)
          .getContractAddressByName("FtsoV2")          (eth_call)
       -> FtsoV2.getFeedById(bytes21 feedId)           (eth_call)
          returns (uint256 value, int8 decimals, uint64 timestamp)
     price_usd = value / 10**decimals

   Feed ID rule (bytes21): 0x01 || ASCII("<SYM>/USD") right-padded with
   zero bytes to 21 bytes. IDs are generated from SUPPORTED_SYMBOLS below so
   they cannot drift; analysis/test_price_provider.py pins the generated BTC /
   ETH / FLR ids to the literal values they replaced, and the BTC id is also
   cross-checked by contracts/scripts/test-ftso.ts.

FAILURE POLICY
--------------
* Any network / RPC / contract / timeout / bad-data failure raises
  PriceProviderError. There is NO silent fallback to fake prices.
* Unknown symbols raise ValueError before any network access.

PERFORMANCE
-----------
* 10 s HTTP timeout on every RPC call (RPC_TIMEOUT_SECONDS).
* 60 s TTL cache (CACHE_TTL_SECONDS) on prices and on the resolved
  FtsoV2 address, to avoid hammering the RPC endpoint.
* A batch of symbols is read over one provider/connection
  (_read_online_many), so a 25-asset portfolio does not pay provider setup
  and chain detection 25 times.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from web3 import Web3

from flare.rpc import make_provider

__all__ = [
    "SUPPORTED_SYMBOLS",
    "FEED_IDS",
    "FIXTURE_PRICES",
    "PriceProviderError",
    "get_prices",
    "get_price_source",
]

DEFAULT_RPC_URL = "https://coston2-api.flare.network/ext/C/rpc"
RPC_TIMEOUT_SECONDS = 10
CACHE_TTL_SECONDS = 60.0

# FlareContractRegistry — same address on all Flare networks (Coston2 / Flare).
# See https://dev.flare.network/network/solidity-reference/
FLARE_CONTRACT_REGISTRY_ADDRESS = "0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

REGISTRY_ABI: list[dict[str, Any]] = [
    {
        "inputs": [{"internalType": "string", "name": "name", "type": "string"}],
        "name": "getContractAddressByName",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

FTSO_V2_ABI: list[dict[str, Any]] = [
    {
        "inputs": [{"internalType": "bytes21", "name": "_feedId", "type": "bytes21"}],
        "name": "getFeedById",
        "outputs": [
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "int8", "name": "decimals", "type": "int8"},
            {"internalType": "uint64", "name": "timestamp", "type": "uint64"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

# Assets we can price. Every entry was verified live against Coston2 FtsoV2
# (getFeedById returns a non-zero value with a fresh timestamp) — see
# tools/ops-status.mjs and analysis/test_price_provider.py.
SUPPORTED_SYMBOLS: tuple[str, ...] = (
    "BTC", "ETH", "XRP", "LTC", "DOGE", "ADA", "SOL", "AVAX", "BNB", "DOT",
    "TRX", "LINK", "USDC", "USDT", "FLR", "MATIC", "POL", "ARB", "OP",
    "ATOM", "FIL", "XLM", "SHIB", "UNI", "AAVE", "BCH", "ETC", "TON",
    "NEAR", "APT", "SUI",
)


def _crypto_feed_id(symbol: str) -> str:
    """bytes21 FTSO v2 feed id: category 0x01 + ASCII "<SYM>/USD", zero-padded."""
    raw = b"\x01" + f"{symbol}/USD".encode("ascii")
    if len(raw) > 21:
        raise ValueError(f"symbol {symbol!r} is too long for an FTSO feed id")
    return "0x" + (raw + b"\x00" * (21 - len(raw))).hex()


FEED_IDS: dict[str, str] = {s: _crypto_feed_id(s) for s in SUPPORTED_SYMBOLS}

# Dev fixtures — used ONLY when ANALYSIS_OFFLINE=1. NOT real market data.
# Every FEED_IDS key must appear here (pinned by a test) so offline mode can
# never raise KeyError for an asset that online mode supports.
FIXTURE_PRICES: dict[str, float] = {
    "BTC": 65000.0, "ETH": 3500.0, "XRP": 2.10, "LTC": 85.0, "DOGE": 0.12,
    "ADA": 0.45, "SOL": 150.0, "AVAX": 25.0, "BNB": 600.0, "DOT": 5.0,
    "TRX": 0.25, "LINK": 18.0, "USDC": 1.0, "USDT": 1.0, "FLR": 0.02,
    "MATIC": 0.55, "POL": 0.55, "ARB": 0.80, "OP": 1.60, "ATOM": 7.0,
    "FIL": 4.50, "XLM": 0.12, "SHIB": 0.00002, "UNI": 9.0, "AAVE": 250.0,
    "BCH": 400.0, "ETC": 22.0, "TON": 3.50, "NEAR": 5.0, "APT": 7.0,
    "SUI": 1.20,
}

# Well-known Flare chain IDs (used only for the human-readable source label).
_CHAIN_NAMES = {14: "flare", 19: "songbird", 16: "coston", 114: "coston2"}


class PriceProviderError(RuntimeError):
    """A real FTSO price read failed. Never silently fall back to fake prices."""


# --- 60 s TTL caches ---------------------------------------------------------
_lock = threading.Lock()
# symbol -> (price_usd, feed_timestamp_unix, expires_at_monotonic)
_price_cache: dict[str, tuple[float, int, float]] = {}
# (ftsov2_address, expires_at_monotonic)
_address_cache: tuple[str, float] | None = None
# chain name detected from eth_chainId during the first successful online read
_detected_chain_name: str | None = None


def _is_offline() -> bool:
    return os.environ.get("ANALYSIS_OFFLINE", "").strip() == "1"


def _rpc_url() -> str:
    return os.environ.get("RPC_URL", "").strip() or DEFAULT_RPC_URL


def _chain_label_from_url(url: str) -> str:
    """Best-effort chain label from the RPC URL, before any chainId lookup."""
    u = url.lower()
    for name in ("coston2", "coston", "songbird", "flare"):
        if name in u:
            return name
    return "unknown-chain"


def get_price_source() -> str:
    """
    Return the provenance label for prices from this provider.

    Offline mode -> "offline-fixture".
    Online mode  -> "<chain>-ftso"; the chain name comes from eth_chainId
    once a successful read has happened, and from an RPC_URL heuristic
    before that (default RPC -> "coston2-ftso"). Never performs network I/O.
    """
    if _is_offline():
        return "offline-fixture"
    with _lock:
        detected = _detected_chain_name
    if detected:
        return f"{detected}-ftso"
    return f"{_chain_label_from_url(_rpc_url())}-ftso"


def _resolve_ftsov2_address(w3: Web3) -> str:
    """Resolve the official FtsoV2 contract via FlareContractRegistry (60 s cache)."""
    global _address_cache
    now = time.monotonic()
    with _lock:
        if _address_cache is not None and _address_cache[1] > now:
            return _address_cache[0]

    registry = w3.eth.contract(
        address=Web3.to_checksum_address(FLARE_CONTRACT_REGISTRY_ADDRESS),
        abi=REGISTRY_ABI,
    )
    address = Web3.to_checksum_address(
        registry.functions.getContractAddressByName("FtsoV2").call()
    )
    if address.lower() == ZERO_ADDRESS.lower():
        raise PriceProviderError(
            "FtsoV2 not found in FlareContractRegistry (returned zero address)"
        )

    with _lock:
        _address_cache = (address, now + CACHE_TTL_SECONDS)
    return address


def _read_online_many(symbols: list[str]) -> dict[str, tuple[float, int]]:
    """
    Read several feeds over one RPC connection.

    Returns {symbol: (price_usd, feed_timestamp_unix)}. Raises
    PriceProviderError on any network / contract / timeout / data failure —
    never returns fake data.
    """
    if not symbols:
        return {}

    global _detected_chain_name
    rpc_url = _rpc_url()
    try:
        w3 = Web3(make_provider(rpc_url, timeout=RPC_TIMEOUT_SECONDS))
        if not w3.is_connected():
            try:
                probe = w3.provider.make_request("web3_clientVersion", [])
            except Exception as exc:
                raise PriceProviderError(
                    f"cannot connect to RPC {rpc_url}: {exc}"
                ) from exc
            raise PriceProviderError(
                f"cannot connect to RPC {rpc_url}: {probe}"
            )

        # Chain detection — only used for the "<chain>-ftso" source label.
        try:
            chain_id = w3.eth.chain_id
        except Exception:
            chain_id = None
        if chain_id is not None:
            with _lock:
                _detected_chain_name = _CHAIN_NAMES.get(
                    int(chain_id), f"chain-{int(chain_id)}"
                )

        ftsov2_address = _resolve_ftsov2_address(w3)
        ftsov2 = w3.eth.contract(
            address=Web3.to_checksum_address(ftsov2_address), abi=FTSO_V2_ABI
        )
    except PriceProviderError:
        raise
    except Exception as e:  # network, timeout, ABI decode, ...
        raise PriceProviderError(
            f"FTSO connection/registry setup failed via {rpc_url}: {e}"
        ) from e

    feed_ids = {symbol: Web3.to_bytes(hexstr=FEED_IDS[symbol]) for symbol in symbols}
    raw_results: list | None = None
    if callable(getattr(w3, "batch_requests", None)):
        try:
            # One JSON-RPC batch for the whole portfolio. The HTTP provider
            # sorts responses by request id, so results stay aligned.
            with w3.batch_requests() as batch:
                for symbol in symbols:
                    batch.add(ftsov2.functions.getFeedById(feed_ids[symbol]))
                raw_results = list(batch.execute())
        except Exception as e:  # noqa: BLE001 - fall back, then report per symbol
            print(
                f"[price_provider] batched FTSO read failed ({e}); "
                "falling back to sequential reads"
            )
            raw_results = None

    if raw_results is None:
        try:
            raw_results = [
                ftsov2.functions.getFeedById(feed_ids[symbol]).call()
                for symbol in symbols
            ]
        except PriceProviderError:
            raise
        except Exception as e:  # network, timeout, ABI decode, ...
            raise PriceProviderError(
                f"FTSO price read failed via {rpc_url}: {e}"
            ) from e

    if len(raw_results) != len(symbols):
        raise PriceProviderError(
            f"FTSO batch returned {len(raw_results)} results for "
            f"{len(symbols)} symbols"
        )

    prices: dict[str, tuple[float, int]] = {}
    for symbol, result in zip(symbols, raw_results):
        try:
            value, decimals, timestamp = result
            value = int(value)
            decimals = int(decimals)  # int8; may legally be negative
            timestamp = int(timestamp)

            if value <= 0:
                raise PriceProviderError(
                    f"feed {symbol} returned non-positive value {value} "
                    f"(feedId {FEED_IDS[symbol]})"
                )
            if timestamp <= 0:
                raise PriceProviderError(
                    f"feed {symbol} returned invalid timestamp {timestamp}"
                )

            prices[symbol] = (value / (10 ** decimals), timestamp)
        except PriceProviderError:
            raise
        except Exception as e:  # ABI decode, malformed result, ...
            raise PriceProviderError(
                f"FTSO price decode failed for {symbol} via {rpc_url}: {e}"
            ) from e

    return prices


def _read_online(symbol: str) -> tuple[float, int]:
    """Read one feed on-chain. Thin wrapper around _read_online_many."""
    return _read_online_many([symbol])[symbol]


def get_prices(symbols: list[str]) -> dict[str, float]:
    """
    Return human-readable USD prices for the given symbols.

    * Keys of the returned dict are the upper-cased symbols.
    * ANALYSIS_OFFLINE=1 -> dev fixture prices (NOT real market data).
    * Otherwise -> real FtsoV2 on-chain reads with a 60 s TTL cache and a
      10 s RPC timeout. Any failure raises PriceProviderError — there is
      NO silent fallback to fake prices.
    * Unknown symbols raise ValueError before any network access.
    """
    if symbols is None:
        raise ValueError("symbols must be a list of asset symbols")

    normalized: list[str] = []
    unknown: list[Any] = []
    for s in symbols:
        if isinstance(s, str) and s.upper() in FEED_IDS:
            normalized.append(s.upper())
        else:
            unknown.append(s)
    if unknown:
        raise ValueError(
            f"unknown symbol(s) {unknown}: supported symbols are "
            f"{sorted(FEED_IDS.keys())}"
        )

    if _is_offline():
        # Dev fixtures — explicitly labeled via get_price_source().
        return {sym: FIXTURE_PRICES[sym] for sym in normalized}

    result: dict[str, float] = {}
    to_fetch: list[str] = []
    now = time.monotonic()
    with _lock:
        for sym in normalized:
            entry = _price_cache.get(sym)
            if entry is not None and entry[2] > now:
                result[sym] = entry[0]
            elif sym not in to_fetch:
                to_fetch.append(sym)

    if to_fetch:
        fetched = _read_online_many(to_fetch)
        with _lock:
            for sym, (price_usd, feed_ts) in fetched.items():
                _price_cache[sym] = (
                    price_usd,
                    feed_ts,
                    time.monotonic() + CACHE_TTL_SECONDS,
                )
                result[sym] = price_usd

    return result
