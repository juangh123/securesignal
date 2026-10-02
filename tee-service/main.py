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
import json
import os
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
    attestation_mode,
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
    if relayer.is_configured():
        print("[main] Relayer configured: results will be submitted on-chain")
    else:
        print(
            "[main] Relayer NOT configured (PRIVATE_KEY and/or registry "
            "address missing): onchain_submitted will be false"
        )
    yield


SERVICE_VERSION = "2.7.0"

app = FastAPI(title="SecureSignal TEE Service", version=SERVICE_VERSION, lifespan=lifespan)

# CORS: production sets ALLOWED_ORIGINS to the frontend origin(s), e.g.
#   ALLOWED_ORIGINS=https://securesignal.vercel.app,https://www.securesignal.io
# Known frontend origins are always allowed so a stale/missing dashboard env var
# never breaks the live app; ALLOWED_ORIGINS may add extra origins on top.
# Note: allow_credentials=True is incompatible with "*" in browsers anyway.
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def _gate_enabled() -> bool:
    """The gate is only meaningful when we can actually read the registry."""
    return _require_onchain_task() and relayer.is_configured()


class AnalysisResponse(BaseModel):
    task_id: int
    encrypted_result: str  # base64-encoded ECIES ciphertext for client_pubkey
    attestation: str       # structured attestation token (JSON string)
    result_hash: str       # 0x-prefixed keccak256 of result_json
    onchain_submitted: bool = False


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
        # `image_digest` is kept for older clients. On AWS Nitro the value is
        # actually PCR0, so new callers should use attestation_measurement and
        # attestation_measurement_type instead.
        "image_digest": _attestation_measurement(mode),
        "attestation_measurement": _attestation_measurement(mode),
        "attestation_measurement_type": _attestation_measurement_type(mode),
        # Effective behaviour (false when no registry/relayer is configured,
        # since the task state cannot be read in that case).
        "analyze_requires_onchain_task": _gate_enabled(),
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


@app.post("/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest):
    if request.task_id < 0:
        raise HTTPException(status_code=400, detail="task_id must be a non-negative integer")
    if len(request.encrypted_data) > MAX_ENCRYPTED_DATA_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"encrypted_data exceeds the {MAX_ENCRYPTED_DATA_CHARS}-character limit",
        )

    # Reject unknown / already-finalized tasks before doing any crypto or
    # model work, and bind the submitted ciphertext to the inputDataHash the
    # client registered on-chain. A failed registry read is fail-closed here:
    # without the hash we cannot prove that this payload belongs to this task.
    if _gate_enabled():
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
        if max_age > 0 and requested_at > 0:
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
        raise HTTPException(status_code=400, detail=f"ECIES decryption failed: {e}")

    # 2. Parse JSON payload
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"decrypted payload is not valid JSON: {e}")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="decrypted payload must be a JSON object")

    client_pubkey = payload.get("client_pubkey", "")
    if (
        not isinstance(client_pubkey, str)
        or len(client_pubkey) != 130
        or not client_pubkey.startswith("04")
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
        raise HTTPException(status_code=500, detail=f"analysis failed: {e}")
    result_json = json.dumps(result_dict, separators=(",", ":"), sort_keys=True)

    # 4. result_hash = keccak256(result_json)
    result_hash = "0x" + keccak(text=result_json).hex()

    # 5. Structured attestation (includes TEE signature over (task_id, result_hash))
    attestation = generate_attestation_token(request.task_id, result_hash)
    attestation_sig = json.loads(attestation)["signature"]

    # 6. Encrypt result back to the client's session public key
    try:
        encrypted_result = base64.b64encode(
            tee_keys.encrypt(client_pubkey, result_json.encode("utf-8"))
        ).decode("ascii")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"result encryption failed: {e}")

    # 7. Optional on-chain submission as relayer. Failure must NOT block
    #    the response — only reflected in the onchain_submitted flag.
    onchain_submitted = False
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
            except Exception as e:
                print(f"[main] WARNING: on-chain submitResult failed: {e}")

    return AnalysisResponse(
        task_id=request.task_id,
        encrypted_result=encrypted_result,
        attestation=attestation,
        result_hash=result_hash,
        onchain_submitted=onchain_submitted,
    )
