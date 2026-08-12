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

from dotenv import load_dotenv

load_dotenv()

from eth_utils import keccak
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from analysis.engine import analyze_portfolio
from attestation.vtpm import generate_attestation_token, sign_result
from crypto import keys as tee_keys
from flare import contracts as relayer

app = FastAPI(title="SecureSignal TEE Service", version="2.0.0")

# CORS: production sets ALLOWED_ORIGINS to the frontend origin(s), e.g.
#   ALLOWED_ORIGINS=https://securesignal.vercel.app,https://www.securesignal.io
# Known frontend origins are always allowed so a stale/missing dashboard env var
# never breaks the live app; ALLOWED_ORIGINS may add extra origins on top.
# Note: allow_credentials=True is incompatible with "*" in browsers anyway.
_base_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://securesignal.vercel.app",
    "https://securesignal-hackathon.vercel.app",
    "https://securesignal-app.vercel.app",
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


class AnalysisResponse(BaseModel):
    task_id: int
    encrypted_result: str  # base64-encoded ECIES ciphertext for client_pubkey
    attestation: str       # structured attestation token (JSON string)
    result_hash: str       # 0x-prefixed keccak256 of result_json
    onchain_submitted: bool = False


@app.on_event("startup")
async def startup_event():
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


@app.get("/public-key")
async def public_key():
    """TEE ECIES public key: 65B uncompressed hex, 04 prefix, no 0x.
    Also returns the derived Ethereum address for on-chain registration."""
    return {
        "public_key": tee_keys.get_public_key_hex(),
        "address": tee_keys.get_tee_address(),
    }


@app.post("/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest):
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
