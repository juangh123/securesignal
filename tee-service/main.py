"""
SecureSignal TEE service (FastAPI).

Implements the unified encryption protocol from plan.md:
  1. GET  /public-key -> TEE secp256k1 public key hex (65B uncompressed,
     "04" prefix, no "0x")
  2. POST /analyze { task_id, encrypted_data(base64 ECIES) }
       -> base64 decode -> eciespy decrypt -> parse JSON
          { client_pubkey, holdings, risk_profile? }
       -> engine analysis -> result_json
       -> result_hash = keccak256(result_json)
       -> attestation = structured token + TEE signature over
          (task_id, result_hash)
       -> ECIES-encrypt result_json to client_pubkey -> base64
       -> if PRIVATE_KEY + registry address configured: submit
          submitResult(task_id, result_hash, signature) on-chain as relayer
          (failure never blocks the response; see onchain_submitted flag)
       -> response { task_id, encrypted_result, attestation, result_hash,
                     onchain_submitted }
"""

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from eth_utils import keccak
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import Response

from analysis import llm, price_provider
from analysis.engine import analyze_portfolio
from attestation.vtpm import (
    AttestationError,
    attestation_mode,
    attestation_scheme,
    ensure_attestation_runtime,
    generate_attestation_token,
)
from crypto import keys as tee_keys
from flare import contracts as relayer


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tee_keys.init_keys()
    ensure_attestation_runtime()
    print(f"[main] TEE public key: {tee_keys.get_public_key_hex()}")
    print(f"[main] TEE address:    {tee_keys.get_tee_address()}")
    print(f"[main] Attestation mode: {attestation_mode()}")
    # Discovered from the registry (or ATTESTATION_SCHEME): eip712-v2 for
    # registries deployed from the v2 revision, eip191-legacy before that.
    print(f"[main] On-chain attestation scheme: {attestation_scheme()}")
    if relayer.is_configured():
        print("[main] Relayer configured: results will be submitted on-chain")
    else:
        print(
            "[main] Relayer NOT configured (PRIVATE_KEY and/or registry "
            "address missing): onchain_submitted will be false"
        )
    if _gate_mode() == "unavailable":
        if _gate_fail_closed():
            print(
                "[main] CRITICAL: ANALYZE_REQUIRE_ONCHAIN_TASK is on but the "
                "relayer/registry is not configured; /analyze will fail "
                "closed (503). Set ANALYZE_REQUIRE_ONCHAIN_TASK=0 to disable "
                "the on-chain gate explicitly."
            )
        else:
            print(
                "[main] WARNING: on-chain task gate requested but the relayer "
                "is not configured; only dev/offline use is safe here "
                "(no LLM key configured)."
            )
    yield


# 2.9.0: harden the public /analyze surface — body-size limit before parsing,
# fail-closed on-chain gate when the relayer is missing, single-flight +
# result cache for duplicate task payloads, bounded LLM/analysis concurrency.
SERVICE_VERSION = "2.9.0"

# Raw request-body cap, enforced before JSON parsing (see middleware below).
# The encrypted payload cap is 128K characters, so 256 KiB leaves headroom for
# JSON framing while still bounding what one request can materialise.
DEFAULT_MAX_REQUEST_BODY_BYTES = 256 * 1024


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read a non-negative int env var, falling back to ``default``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _body_size_limit_bytes() -> int:
    """Upper bound for request bodies, enforced before JSON parsing."""
    return _env_int("MAX_REQUEST_BODY_BYTES", DEFAULT_MAX_REQUEST_BODY_BYTES)


def _cors_headers_for_origin(origin: str | None) -> list[tuple[bytes, bytes]]:
    """CORS headers for an allowed origin (mirrors the CORSMiddleware config).

    The body-size middleware sits outside CORSMiddleware, so it has to attach
    these itself; otherwise a browser only sees an opaque CORS failure instead
    of the 413 detail.
    """
    if not origin:
        return []
    allowed = origin in _allowed_origins
    if not allowed and _dev_origin_regex and re.fullmatch(_dev_origin_regex, origin):
        allowed = True
    if not allowed:
        return []
    return [
        (b"access-control-allow-origin", origin.encode("latin-1")),
        (b"access-control-allow-credentials", b"true"),
        (b"vary", b"Origin"),
    ]


async def _reject_oversized_body(
    send, max_bytes: int, origin: str | None = None
) -> None:
    body = json.dumps(
        {"detail": f"request body exceeds the {max_bytes}-byte limit"}
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ]
            + _cors_headers_for_origin(origin),
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before FastAPI buffers them.

    ``MAX_ENCRYPTED_DATA_CHARS`` is only checked after the JSON body has been
    parsed, so a client could still make the process materialise an arbitrary
    amount of data. This middleware enforces the cap on the raw body instead.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        origin = None
        content_length = None
        for name, value in scope.get("headers") or []:
            if name == b"origin" and origin is None:
                origin = value.decode("latin-1")
            elif name == b"content-length" and content_length is None:
                content_length = value
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                await _reject_oversized_body(send, self.max_bytes, origin)
                return
            if declared > self.max_bytes:
                await _reject_oversized_body(send, self.max_bytes, origin)
                return

        # Read the body up to the cap, then replay it downstream. Buffering is
        # bounded by max_bytes, and rejecting here (instead of raising from
        # ``receive``) keeps a clean 413: FastAPI would otherwise report a
        # body-parsing 400 before the exception reaches this middleware.
        messages: list[dict] = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") == "http.disconnect":
                break
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    await _reject_oversized_body(send, self.max_bytes, origin)
                    return
                if not message.get("more_body", False):
                    break

        index = 0

        async def replay():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, replay, send)


app = FastAPI(title="SecureSignal TEE Service", version=SERVICE_VERSION, lifespan=lifespan)

# CORS: production sets ALLOWED_ORIGINS to the frontend origin(s), e.g.
#   ALLOWED_ORIGINS=https://securesignal.vercel.app,https://www.securesignal.io
# Known frontend origins are always allowed so a stale/missing dashboard env var
# never breaks the live app; ALLOWED_ORIGINS may add extra origins on top.
# Note: allow_credentials=True is incompatible with "*" in browsers anyway.
# Outside production any loopback origin is allowed, because `next dev` moves to
# the next free port (3001, 3100, ...) when the default one is taken.
_base_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://securesignal.vercel.app",
]
_env_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
_allowed_origins = []
for origin in _base_origins + _env_origins:
    if origin not in _allowed_origins:
        _allowed_origins.append(origin)

_dev_origin_regex = (
    None
    if os.environ.get("ENV", "").strip().lower() == "prod"
    else r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=_dev_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registered here so it runs outside the route layer: an oversized body is
# rejected before CORS, route code, or the JSON parser touch it.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=_body_size_limit_bytes())


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    """Apply conservative API response headers on every route.

    HSTS is only added when the request arrived over HTTPS (directly or via a
    TLS-terminating proxy such as CloudFront), so plain-HTTP local dev is not
    affected.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cache-Control", "no-store")
    forwarded_proto = (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    )
    if request.url.scheme == "https" or forwarded_proto == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000"
        )
    return response


class AnalysisRequest(BaseModel):
    task_id: int
    encrypted_data: str  # base64-encoded ECIES ciphertext for the TEE pubkey


# A real portfolio payload encrypts to a few hundred bytes; this cap only
# exists to stop an oversized body from being materialized in memory.
MAX_ENCRYPTED_DATA_CHARS = 128 * 1024

# /analyze is public and unauthenticated, and an enabled LLM makes every call
# cost money. By default we therefore only analyse task ids that really exist
# on-chain in Requested state, so spamming the endpoint requires paying C2FLR
# gas to register a task first. Set ANALYZE_REQUIRE_ONCHAIN_TASK=0 to disable
# (purely offline/dev setups without a configured relayer are exempt anyway).
ONCHAIN_TASK_STATUS_NAMES = {0: "None", 1: "Requested", 2: "Completed", 3: "Verified"}

# A task that stayed Requested forever (a client that never came back) would
# otherwise be a free, repeatable trigger for paid LLM calls. Only tasks
# requested within this window are analysed; 0 disables the age check.
DEFAULT_TASK_MAX_AGE_SECONDS = 900

# One /analyze call can trigger a paid LLM request, and the same
# (task_id, ciphertext) pair is idempotent, so duplicates are coalesced
# (single-flight) and the first successful response is cached briefly.
DEFAULT_ANALYZE_RESULT_CACHE_SECONDS = 300
DEFAULT_ANALYZE_CACHE_MAX_ENTRIES = 128
DEFAULT_ANALYZE_MAX_CONCURRENCY = 2
# How long to wait for the relayer transaction receipt before reporting the
# result as "broadcast but unconfirmed". 0 disables the wait entirely.
DEFAULT_ONCHAIN_RECEIPT_TIMEOUT = 15

# Coarse wall-clock budget for one /analyze call. The individual steps already
# have their own timeouts (LLM budget, FTSO 10s, RPC 10s, NSM 15s); this caps
# the optional tail (waiting for the relayer receipt) so a slow chain cannot
# push the response past the CloudFront origin timeout. 0 disables the cap.
DEFAULT_ANALYZE_TOTAL_BUDGET_SECONDS = 50

# Application-level rate limiting for the public /analyze endpoint. This is
# defense in depth on top of CloudFront/WAF: it keeps one client (or a global
# burst) from driving unbounded analysis work. 0 disables either limiter.
DEFAULT_ANALYZE_RATE_LIMIT_PER_MINUTE = 30
DEFAULT_ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE = 120
ANALYZE_RATE_LIMIT_WINDOW_SECONDS = 60.0
# Bound the per-client table so a spoofed-IP flood cannot grow memory forever.
ANALYZE_RATE_LIMIT_MAX_CLIENTS = 4096


def _require_onchain_task() -> bool:
    return os.getenv("ANALYZE_REQUIRE_ONCHAIN_TASK", "1").strip() != "0"


def _task_max_age_seconds() -> int:
    raw = os.getenv("ANALYZE_TASK_MAX_AGE_SECONDS", "").strip()
    if not raw:
        return DEFAULT_TASK_MAX_AGE_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_TASK_MAX_AGE_SECONDS


def _gate_mode() -> str:
    """How the on-chain task gate is behaving right now.

    ``disabled``    — the operator explicitly opted out (env var set to 0).
    ``enforced``    — every /analyze call is checked against the registry.
    ``unavailable`` — the gate was requested but the relayer/registry is not
                      configured, so the payload binding cannot be verified.
    """
    if not _require_onchain_task():
        return "disabled"
    if relayer.is_configured():
        return "enforced"
    return "unavailable"


def _gate_enabled() -> bool:
    """True when the gate is actually enforced (used by /health)."""
    return _gate_mode() == "enforced"


def _gate_fail_closed() -> bool:
    """Refuse to analyse when the required gate cannot run.

    The gate is what stops the public endpoint from being an open, paid LLM
    trigger. Losing the relayer config must not silently turn that protection
    off: a production deployment, or any deployment with an LLM key, fails
    closed with 503. Explicitly setting ANALYZE_REQUIRE_ONCHAIN_TASK=0 remains
    the documented way to disable the gate (offline/dev setups).
    """
    if _gate_mode() != "unavailable":
        return False
    production = os.environ.get("ENV", "").strip().lower() == "prod"
    return production or llm.is_configured()


class AnalysisResponse(BaseModel):
    task_id: int
    encrypted_result: str  # base64-encoded ECIES ciphertext for client_pubkey
    attestation: str       # structured attestation token (JSON string)
    result_hash: str       # 0x-prefixed keccak256 of result_json
    onchain_submitted: bool = False
    # True = receipt confirms success, False = mined/rejected failure,
    # None = broadcast but outcome unconfirmed (or no submission attempted).
    onchain_confirmed: bool | None = None


@app.get("/public-key")
async def public_key():
    """TEE ECIES public key: 65B uncompressed hex, 04 prefix, no 0x.
    Also returns the derived Ethereum address for on-chain registration."""
    return {
        "public_key": tee_keys.get_public_key_hex(),
        "address": tee_keys.get_tee_address(),
    }


def _attestation_measurement(mode: str) -> str:
    """Measured workload identity for the selected attestation provider."""
    if mode == "aws-nitro-enclaves":
        return os.getenv("AWS_NITRO_PCR0", "")
    return os.getenv("TEE_IMAGE_DIGEST", "dev")


def _attestation_measurement_type(mode: str) -> str:
    if mode == "aws-nitro-enclaves":
        return "pcr0"
    if mode == "gcp-confidential-space":
        return "image_digest"
    return "dev"


@app.get("/health")
async def health():
    """Non-secret operational status for monitoring and ops tooling.

    Exposes only booleans, derived addresses, and mode labels — never key
    material, the RPC URL, or the LLM API key.
    """
    mode = attestation_mode()
    return {
        "status": "ok",
        "version": app.version,
        "tee_address": tee_keys.get_tee_address(),
        "registry_address": relayer.registry_address(),
        "relayer_configured": relayer.is_configured(),
        "price_mode": price_provider.get_price_source(),
        "llm_configured": llm.is_configured(),
        # Model name only (never the key or base URL). Lets the UI and ops
        # tooling show which engine is actually answering.
        "llm_model": llm.configured_model() if llm.is_configured() else None,
        # Request budget visible to ops tooling: the whole LLM call must stay
        # comfortably below the CloudFront origin read timeout (60 s max).
        "llm_timeout_seconds": (
            llm.configured_timeout_seconds() if llm.is_configured() else None
        ),
        "llm_total_budget_seconds": (
            llm.configured_total_budget_seconds() if llm.is_configured() else None
        ),
        "attestation_mode": mode,
        # Signature convention used for submitResult attestations. Clients and
        # ops tooling use it to pick the right verification path instead of
        # assuming EIP-191 (the pre-v2 behaviour).
        "attestation_scheme": attestation_scheme(),
        # `image_digest` is kept for older clients. On AWS Nitro the value is
        # actually PCR0, so new callers should use attestation_measurement and
        # attestation_measurement_type instead.
        "image_digest": _attestation_measurement(mode),
        "attestation_measurement": _attestation_measurement(mode),
        "attestation_measurement_type": _attestation_measurement_type(mode),
        # Effective enforcement (false when no registry/relayer is configured,
        # since the task state cannot be read in that case).
        "analyze_requires_onchain_task": _gate_enabled(),
        # "disabled" | "enforced" | "unavailable"; combined with
        # analyze_gate_fail_closed this tells ops whether /analyze is
        # protected, open on purpose, or refusing requests.
        "analyze_gate_mode": _gate_mode(),
        "analyze_gate_fail_closed": _gate_fail_closed(),
        # Application-level throttling (0 = disabled); CloudFront/WAF should
        # still front the service in production.
        "analyze_rate_limit_per_minute": _env_int(
            "ANALYZE_RATE_LIMIT_PER_MINUTE",
            DEFAULT_ANALYZE_RATE_LIMIT_PER_MINUTE,
        ),
        "analyze_rate_limit_global_per_minute": _env_int(
            "ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE",
            DEFAULT_ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE,
        ),
        # Wall-clock budget for one analysis (0 = disabled). Only the optional
        # tail (receipt wait) is trimmed; required steps keep their timeouts.
        "analyze_total_budget_seconds": _env_int(
            "ANALYZE_TOTAL_BUDGET_SECONDS", DEFAULT_ANALYZE_TOTAL_BUDGET_SECONDS
        ),
    }


@app.get("/assets")
async def assets():
    """Assets the price provider can actually price.

    Deliberately separate from /health: this is a capability contract the
    frontend uses to reject unsupported symbols *before* the user pays gas for
    a requestAnalysis transaction (the engine would otherwise reject them only
    after the on-chain round trip).
    """
    return {
        "symbols": list(price_provider.SUPPORTED_SYMBOLS),
        "count": len(price_provider.SUPPORTED_SYMBOLS),
        "price_source": price_provider.get_price_source(),
    }


_analyze_cache: dict[str, tuple[float, AnalysisResponse]] = {}
_analyze_inflight: dict[tuple[int, str], asyncio.Future] = {}
_analyze_semaphores: dict[int, asyncio.Semaphore] = {}
_analyze_rate_lock = threading.Lock()
# client key -> request timestamps (monotonic seconds) inside the window
_analyze_rate_hits: dict[str, list[float]] = {}
_analyze_rate_global_hits: list[float] = []


def _prune_hits(hits: list[float], window_start: float) -> None:
    while hits and hits[0] <= window_start:
        hits.pop(0)


def _check_rate_limit(client_key: str) -> float | None:
    """Return seconds to wait when the caller must be throttled, else None.

    Two sliding windows guard the public endpoint: a per-client limit (the
    first hop we can see) and a global one. Both default to generous values so
    a normal user never notices, and either can be disabled with 0.
    """
    per_minute = _env_int(
        "ANALYZE_RATE_LIMIT_PER_MINUTE", DEFAULT_ANALYZE_RATE_LIMIT_PER_MINUTE
    )
    global_per_minute = _env_int(
        "ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE",
        DEFAULT_ANALYZE_RATE_LIMIT_GLOBAL_PER_MINUTE,
    )
    if per_minute <= 0 and global_per_minute <= 0:
        return None

    now = time.monotonic()
    window_start = now - ANALYZE_RATE_LIMIT_WINDOW_SECONDS
    with _analyze_rate_lock:
        _prune_hits(_analyze_rate_global_hits, window_start)
        if (
            global_per_minute > 0
            and len(_analyze_rate_global_hits) >= global_per_minute
        ):
            return max(
                1.0,
                _analyze_rate_global_hits[0]
                + ANALYZE_RATE_LIMIT_WINDOW_SECONDS
                - now,
            )

        hits: list[float] | None = None
        if per_minute > 0:
            hits = _analyze_rate_hits.get(client_key)
            if hits is None:
                if len(_analyze_rate_hits) >= ANALYZE_RATE_LIMIT_MAX_CLIENTS:
                    _analyze_rate_hits.pop(next(iter(_analyze_rate_hits)), None)
                hits = []
                _analyze_rate_hits[client_key] = hits
            else:
                # Refresh insertion order so eviction drops idle clients first.
                _analyze_rate_hits[client_key] = _analyze_rate_hits.pop(client_key)
            _prune_hits(hits, window_start)
            if len(hits) >= per_minute:
                return max(
                    1.0,
                    hits[0] + ANALYZE_RATE_LIMIT_WINDOW_SECONDS - now,
                )
            hits.append(now)

        if global_per_minute > 0:
            _analyze_rate_global_hits.append(now)
    return None


def _client_key(http_request: Request) -> str:
    """Best-effort client identity for rate limiting.

    Behind CloudFront/Render the real client address arrives in a proxy header.
    ``X-Forwarded-For`` is read from the right: the rightmost entry is the one
    the proxy closest to this service appended, so a client-supplied prefix
    cannot dodge the per-client limit. ``CF-Connecting-IP`` wins when present.
    Set ANALYZE_TRUST_PROXY_HEADERS=0 when the service is exposed directly so a
    spoofed header cannot dodge the per-client limit.
    """
    trust_proxy = os.environ.get("ANALYZE_TRUST_PROXY_HEADERS", "1").strip() != "0"
    if trust_proxy:
        cloudfront = http_request.headers.get("cf-connecting-ip", "").strip()
        if cloudfront:
            return cloudfront.split(",")[0].strip()
        forwarded = [
            part.strip()
            for part in http_request.headers.get("x-forwarded-for", "").split(",")
            if part.strip()
        ]
        if forwarded:
            return forwarded[-1]
        real_ip = http_request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
    return http_request.client.host if http_request.client else "unknown"


def _reset_analyze_caches() -> None:
    """Drop cached/single-flight state (tests and ops tooling)."""
    _analyze_cache.clear()
    _analyze_inflight.clear()
    _analyze_semaphores.clear()
    with _analyze_rate_lock:
        _analyze_rate_hits.clear()
        _analyze_rate_global_hits.clear()


def _analyze_cache_key(request: AnalysisRequest) -> str:
    """One cache key per (task, ciphertext) pair — same input, same result."""
    digest = hashlib.sha256(request.encrypted_data.encode("utf-8")).hexdigest()
    return f"{request.task_id}:{digest}"


def _cache_ttl_seconds() -> int:
    return _env_int(
        "ANALYZE_RESULT_CACHE_SECONDS",
        DEFAULT_ANALYZE_RESULT_CACHE_SECONDS,
    )


def _cache_get(key: str) -> AnalysisResponse | None:
    ttl = _cache_ttl_seconds()
    if ttl <= 0:
        return None
    entry = _analyze_cache.get(key)
    if entry is None:
        return None
    stored_at, response = entry
    if time.monotonic() - stored_at > ttl:
        _analyze_cache.pop(key, None)
        return None
    return response


def _cache_put(key: str, response: AnalysisResponse) -> None:
    ttl = _cache_ttl_seconds()
    if ttl <= 0:
        return
    max_entries = _env_int(
        "ANALYZE_CACHE_MAX_ENTRIES", DEFAULT_ANALYZE_CACHE_MAX_ENTRIES, minimum=1
    )
    _analyze_cache[key] = (time.monotonic(), response)
    while len(_analyze_cache) > max_entries:
        _analyze_cache.pop(next(iter(_analyze_cache)), None)


def _analyze_semaphore() -> asyncio.Semaphore:
    """Per-event-loop concurrency limit for the expensive analysis path."""
    loop_id = id(asyncio.get_running_loop())
    semaphore = _analyze_semaphores.get(loop_id)
    if semaphore is None:
        semaphore = asyncio.Semaphore(
            _env_int(
                "ANALYZE_MAX_CONCURRENCY",
                DEFAULT_ANALYZE_MAX_CONCURRENCY,
                minimum=1,
            )
        )
        if len(_analyze_semaphores) >= 8:  # drop the oldest loop binding
            _analyze_semaphores.pop(next(iter(_analyze_semaphores)), None)
        _analyze_semaphores[loop_id] = semaphore
    return semaphore


async def _single_flight(key: str, factory):
    """Coalesce concurrent identical work into one execution per loop."""
    loop_key = (id(asyncio.get_running_loop()), key)
    existing = _analyze_inflight.get(loop_key)
    if existing is not None:
        return await existing

    future: asyncio.Future = asyncio.get_running_loop().create_future()
    # Retrieve the exception even when no duplicate request joins the flight,
    # so asyncio never logs "exception was never retrieved".
    future.add_done_callback(lambda f: f.cancelled() or f.exception())
    _analyze_inflight[loop_key] = future
    try:
        response = await factory()
    except BaseException as exc:
        if not future.done():
            future.set_exception(exc)
        raise
    else:
        if not future.done():
            future.set_result(response)
        return response
    finally:
        _analyze_inflight.pop(loop_key, None)


def _analyze_deadline(now: float | None = None) -> float | None:
    """Wall-clock deadline for one analysis, or None when disabled."""
    budget = _env_int(
        "ANALYZE_TOTAL_BUDGET_SECONDS", DEFAULT_ANALYZE_TOTAL_BUDGET_SECONDS
    )
    if budget <= 0:
        return None
    return (time.monotonic() if now is None else now) + budget


def _remaining_budget(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return deadline - time.monotonic()


async def _analyze_uncached(
    request: AnalysisRequest, deadline: float | None = None
) -> tuple[AnalysisResponse, bool]:
    """Run the full gate → decrypt → analyse → attest → submit pipeline.

    Returns ``(response, cacheable)``; only successful analysis results are
    cached, so a transient price/RPC failure is retried on the next request.
    """
    gate_mode = _gate_mode()
    if gate_mode == "unavailable" and _gate_fail_closed():
        raise HTTPException(
            status_code=503,
            detail=(
                "on-chain task verification is required but the relayer/registry "
                "is not configured; refusing to analyse. Set "
                "ANALYZE_REQUIRE_ONCHAIN_TASK=0 to disable the gate explicitly."
            ),
        )

    # Reject unknown / already-finalized tasks before doing any crypto or
    # model work, and bind the submitted ciphertext to the inputDataHash the
    # client registered on-chain. A failed registry read is fail-closed here:
    # without the hash we cannot prove that this payload belongs to this task.
    if gate_mode == "enforced":
        state = await asyncio.to_thread(
            relayer.task_state_with_input, request.task_id
        )
        if state is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "cannot verify the on-chain task state/input binding right "
                    "now; retry shortly"
                ),
            )
        status, requested_at, onchain_input_hash = state
        if status != 1:
            name = ONCHAIN_TASK_STATUS_NAMES.get(status, str(status))
            raise HTTPException(
                status_code=409,
                detail=(
                    f"task {request.task_id} is not pending on-chain (status={name}); "
                    "call requestAnalysis first and retry while the task is Requested"
                ),
            )
        max_age = _task_max_age_seconds()
        if max_age > 0:
            if requested_at <= 0:
                # A pending task always carries requestedAt on-chain; a zero
                # value means the read/ABI is off, so the window cannot be
                # verified. Fail closed instead of analysing an unbounded task.
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"task {request.task_id} has no on-chain requestedAt "
                        "timestamp; refusing to analyse without a verifiable "
                        "analysis window"
                    ),
                )
            age = int(time.time()) - requested_at
            if age > max_age:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"task {request.task_id} was requested {age}s ago, which is older "
                        f"than the {max_age}s analysis window; call requestAnalysis again"
                    ),
                )
        expected_input_hash = "0x" + keccak(text=request.encrypted_data).hex()
        if onchain_input_hash.lower() != expected_input_hash.lower():
            raise HTTPException(
                status_code=409,
                detail=(
                    "encrypted_data does not match the inputDataHash registered "
                    "on-chain for this task; refusing to analyse a substituted payload"
                ),
            )

    # 1. base64 decode + ECIES decrypt
    try:
        ciphertext = base64.b64decode(request.encrypted_data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="encrypted_data is not valid base64")
    try:
        plaintext = tee_keys.decrypt(ciphertext)
    except Exception as e:
        # Log details server-side only; never echo internals to the caller.
        print(f"[analyze] ECIES decryption failed: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(
            status_code=400,
            detail="encrypted_data could not be decrypted with the TEE key",
        )

    # 2. Parse JSON payload
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"[analyze] payload parse failed: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(
            status_code=400, detail="decrypted payload is not valid JSON"
        )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="decrypted payload must be a JSON object")

    client_pubkey = payload.get("client_pubkey", "")
    if not isinstance(client_pubkey, str) or not re.fullmatch(
        r"04[0-9a-fA-F]{128}", client_pubkey
    ):
        raise HTTPException(
            status_code=400,
            detail="payload.client_pubkey must be 65B uncompressed secp256k1 "
                   "hex (04 prefix, no 0x)",
        )

    # 3. Run analysis
    try:
        result_dict = await asyncio.to_thread(analyze_portfolio, payload)
    except Exception as e:
        print(f"[analyze] analysis failed: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(
            status_code=500, detail="analysis failed; see the service logs"
        )
    result_json = json.dumps(result_dict, separators=(",", ":"), sort_keys=True)

    # 4. result_hash = keccak256(result_json)
    result_hash = "0x" + keccak(text=result_json).hex()

    # 5. Structured attestation (includes TEE signature over (task_id, result_hash))
    try:
        # Runs off the event loop: the NSM helper subprocess (15s), the GCP
        # unix-socket JWT fetch (5s) and the cold-start scheme/domain probes
        # are all blocking calls.
        attestation = await asyncio.to_thread(
            generate_attestation_token, request.task_id, result_hash
        )
    except AttestationError as exc:
        # e.g. the registry signing scheme could not be determined: fail closed
        # instead of returning a signature the target registry would reject.
        print(f"[analyze] attestation failed: {exc}", flush=True)
        raise HTTPException(
            status_code=503,
            detail="attestation is unavailable right now; retry shortly",
        )
    attestation_sig = json.loads(attestation)["signature"]

    # 6. Encrypt result back to the client's session public key
    try:
        encrypted_result = base64.b64encode(
            tee_keys.encrypt(client_pubkey, result_json.encode("utf-8"))
        ).decode("ascii")
    except Exception as e:
        print(f"[analyze] result encryption failed: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(
            status_code=500, detail="result encryption failed; see the service logs"
        )

    # 7. Optional on-chain submission as relayer. Failure must NOT block
    #    the response — only reflected in the onchain_submitted flag.
    onchain_submitted = False
    onchain_confirmed: bool | None = None
    if relayer.is_configured():
        # Only submit for a task that is still pending on-chain. Without this
        # pre-check an unknown or already-finalized task id makes the relayer
        # broadcast a transaction that is guaranteed to revert (wasting gas).
        # status None means the read failed, so we fall back to attempting.
        status = await asyncio.to_thread(relayer.task_status, request.task_id)
        if status is not None and status != 1:  # 1 = Status.Requested
            print(
                f"[main] relayer skipped: task {request.task_id} on-chain "
                f"status={status} (not Requested)"
            )
        else:
            try:
                tx_hash = await asyncio.to_thread(
                    relayer.submit_result,
                    request.task_id,
                    result_hash,
                    attestation_sig,
                )
                onchain_submitted = True
                print(f"[main] submitResult on-chain tx: {tx_hash}")
                receipt_timeout = _env_int(
                    "ONCHAIN_RECEIPT_TIMEOUT", DEFAULT_ONCHAIN_RECEIPT_TIMEOUT
                )
                remaining = _remaining_budget(deadline)
                if remaining is not None:
                    capped = min(receipt_timeout, max(0, int(remaining)))
                    if capped != receipt_timeout:
                        print(
                            f"[main] analysis budget: receipt wait capped to "
                            f"{capped}s (remaining {remaining:.1f}s)",
                            flush=True,
                        )
                    receipt_timeout = capped
                onchain_confirmed = await asyncio.to_thread(
                    relayer.wait_for_receipt, tx_hash, float(receipt_timeout)
                )
                if onchain_confirmed is False:
                    print(
                        f"[main] WARNING: submitResult tx {tx_hash} reverted "
                        "on-chain"
                    )
                elif onchain_confirmed is None:
                    print(
                        f"[main] submitResult tx {tx_hash} is unconfirmed "
                        f"(no receipt within {receipt_timeout}s)"
                    )
            except Exception as e:
                onchain_confirmed = False
                print(f"[main] WARNING: on-chain submitResult failed: {e}")

    response = AnalysisResponse(
        task_id=request.task_id,
        encrypted_result=encrypted_result,
        attestation=attestation,
        result_hash=result_hash,
        onchain_submitted=onchain_submitted,
        onchain_confirmed=onchain_confirmed,
    )
    return response, result_dict.get("status") == "success"


@app.post("/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest, http_request: Request):
    if request.task_id < 0:
        raise HTTPException(status_code=400, detail="task_id must be a non-negative integer")
    retry_after = _check_rate_limit(_client_key(http_request))
    if retry_after is not None:
        wait_seconds = int(retry_after) + 1
        raise HTTPException(
            status_code=429,
            detail=(
                "too many analysis requests from this client; "
                f"retry in {wait_seconds}s"
            ),
            headers={"Retry-After": str(wait_seconds)},
        )
    if len(request.encrypted_data) > MAX_ENCRYPTED_DATA_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"encrypted_data exceeds the {MAX_ENCRYPTED_DATA_CHARS}-character limit",
        )

    key = _analyze_cache_key(request)
    cached = _cache_get(key)
    if cached is not None:
        print(f"[analyze] cache hit for task {request.task_id}", flush=True)
        return cached

    # The budget starts when the request arrives: time spent waiting for the
    # concurrency semaphore counts against it too.
    deadline = _analyze_deadline()

    async def run() -> tuple[AnalysisResponse, bool]:
        async with _analyze_semaphore():
            return await _analyze_uncached(request, deadline=deadline)

    response, cacheable = await _single_flight(key, run)
    if cacheable:
        _cache_put(key, response)
    else:
        print(
            f"[analyze] task {request.task_id} produced an error result; not cached",
            flush=True,
        )
    return response
