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
   zero bytes to 21 bytes. The three IDs below are copied verbatim from
   analysis/engine.py and cross-checked against
   contracts/scripts/test-ftso.ts (same registry flow in TypeScript).

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
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from web3 import Web3

__all__ = [
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

# bytes21 feed IDs: category 0x01 (crypto) + "<SYM>/USD" ASCII, right-padded
# with zero bytes to 21 bytes. Copied verbatim from analysis/engine.py.
FEED_IDS: dict[str, str] = {
    "BTC": "0x014254432f55534400000000000000000000000000",  # "BTC/USD"
    "ETH": "0x014554482f55534400000000000000000000000000",  # "ETH/USD"
    "FLR": "0x01464c522f55534400000000000000000000000000",  # "FLR/USD"
}

# Dev fixtures — used ONLY when ANALYSIS_OFFLINE=1. NOT real market data.
FIXTURE_PRICES: dict[str, float] = {"BTC": 65000.0, "ETH": 3500.0, "FLR": 0.02}

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


def _read_online(symbol: str) -> tuple[float, int]:
    """
    Read one feed on-chain. Returns (price_usd, feed_timestamp_unix).

    Raises PriceProviderError on any network / contract / timeout / data
    failure. Never returns fake data.
    """
    global _detected_chain_name
    rpc_url = _rpc_url()
    try:
        w3 = Web3(
            Web3.HTTPProvider(
                rpc_url, request_kwargs={"timeout": RPC_TIMEOUT_SECONDS}
            )
        )
        if not w3.is_connected():
            raise PriceProviderError(f"cannot connect to RPC {rpc_url}")

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
        feed_id_bytes = Web3.to_bytes(hexstr=FEED_IDS[symbol])
        value, decimals, timestamp = ftsov2.functions.getFeedById(
            feed_id_bytes
        ).call()

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

        price_usd = value / (10 ** decimals)
        return price_usd, timestamp
    except PriceProviderError:
        raise
    except Exception as e:  # network, timeout, ABI decode, ...
        raise PriceProviderError(
            f"FTSO price read failed for {symbol} via {rpc_url}: {e}"
        ) from e


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

    for sym in to_fetch:
        price_usd, feed_ts = _read_online(sym)
        with _lock:
            _price_cache[sym] = (
                price_usd,
                feed_ts,
                time.monotonic() + CACHE_TTL_SECONDS,
            )
        result[sym] = price_usd

    return result
