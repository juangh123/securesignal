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
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from eth_utils import keccak
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from analysis import llm, price_provider
from analysis.engine import analyze_portfolio
from attestation.vtpm import generate_attestation_token
from crypto import keys as tee_keys
from flare import contracts as relayer


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tee_keys.init_keys()
    print(f"[main] TEE public key: {tee_keys.get_public_key_hex()}")
    print(f"[main] TEE address:    {tee_keys.get_tee_address()}")
    if relayer.is_configured():
        print("[main] Relayer configured: results will be submitted on-chain")
    else:
        print(
            "[main] Relayer NOT configured (PRIVATE_KEY and/or registry "
            "address missing): onchain_submitted will be false"
        )
    yield


app = FastAPI(title="SecureSignal TEE Service", version="2.1.0", lifespan=lifespan)

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


def _require_onchain_task() -> bool:
    return os.getenv("ANALYZE_REQUIRE_ONCHAIN_TASK", "1").strip() != "0"


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


@app.get("/health")
async def health():
    """Non-secret operational status for monitoring and ops tooling.

    Exposes only booleans, derived addresses, and mode labels — never key
    material, the RPC URL, or the LLM API key.
    """
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
        "attestation_mode": (
            "gcp-confidential-space" if os.getenv("ENV") == "prod" else "dev-simulated"
        ),
        "image_digest": os.getenv("TEE_IMAGE_DIGEST", "dev"),
        # Effective behaviour (false when no registry/relayer is configured,
        # since the task state cannot be read in that case).
        "analyze_requires_onchain_task": _gate_enabled(),
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
    # model work. A status of None means the registry read itself failed, so
    # we let the request through rather than turning an RPC hiccup into an
    # outage — the relayer re-checks the status before submitting.
    if _gate_enabled():
        status = await asyncio.to_thread(relayer.task_status, request.task_id)
        if status is not None and status != 1:
            name = ONCHAIN_TASK_STATUS_NAMES.get(status, str(status))
            raise HTTPException(
                status_code=409,
                detail=(
                    f"task {request.task_id} is not pending on-chain (status={name}); "
                    "call requestAnalysis first and retry while the task is Requested"
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
