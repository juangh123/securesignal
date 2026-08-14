"""
Structured attestation token (dev-honest implementation).

Token format: JSON string
  {
    "task_id":      <uint256>,
    "result_hash":  "<0x-prefixed keccak256 hex of result_json>",
    "image_digest": "<env TEE_IMAGE_DIGEST or 'dev'>",
    "tee_address":  "<checksummed address of TEE signing key>",
    "timestamp":    <unix seconds>,
    "mode":         "dev-simulated",
    "signature":    "<0x-prefixed 65B secp256k1 signature hex>"
  }

Signature scheme (MUST match contract _verifyAttestation, see
AnalysisRegistry.sol:97-100 and plan.md):
  message = abi.encodePacked(task_id:uint256, result_hash:bytes32)  (64 raw bytes)
  digest  = keccak256("\\x19Ethereum Signed Message:\\n64" || message)
  => EIP-191 personal_sign over the RAW 64-byte packed message (prefix
     length is "\\n64"). Do NOT keccak256 the message first — that would
     change the prefix to "\\n32" and break on-chain ecrecover.
  => contract ecrecover(digest, v, r, s) == teeAddress

TODO(production): replace this module with a real GCP Confidential Space
attestation flow — request a JWT from the metadata server
(http://metadata.google.internal/computeMetadata/v1/instance/attributes/
attestation-token?audience=...), bind `report_data` (hash of task_id +
result_hash) into the token's `eat_nonce` claim, and have the contract /
verifier validate the JWT signature chain against Google's root certs and
the image digest. See:
https://cloud.google.com/confidential-computing/confidential-space/docs/attestation
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from eth_account import Account
from eth_account.messages import encode_defunct

from crypto.keys import get_private_key_hex, get_tee_address

def fetch_attestation_jwt(audience: str, nonce: str) -> str:
    """
    Fetch OIDC Attestation JWT from Google Cloud Confidential Space metadata server.
    Binds the provided nonce (e.g. hash of task_id + result_hash) to the token's eat_nonce claim.
    """
    if os.environ.get("ENV") != "prod":
        return "simulate-gcp-jwt-token"

    query = urllib.parse.urlencode({"audience": audience, "nonce": nonce})
    url = (
        "http://metadata.google.internal/computeMetadata/v1/instance/attributes/"
        f"attestation-token?{query}"
    )
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=5.0) as response:
            return response.read().decode("utf-8")
    except urllib.error.URLError as e:
        print(f"Failed to fetch attestation JWT from metadata server: {e}")
        return ""


def sign_result(task_id: int, result_hash: str) -> str:
    """
    Sign (task_id, result_hash) with the TEE private key using EIP-191
    personal_sign over the RAW 64-byte packed message:

        message = abi.encodePacked(uint256 taskId, bytes32 resultHash)
        digest  = keccak256("\\x19Ethereum Signed Message:\\n64" || message)

    This matches AnalysisRegistry._verifyAttestation exactly
    (prefix length "\\n64"). Do NOT keccak256 the message beforehand.

    Returns 0x-prefixed 65-byte signature hex (r || s || v).
    """
    result_hash_bytes = bytes.fromhex(
        result_hash[2:] if result_hash.startswith("0x") else result_hash
    )
    if len(result_hash_bytes) != 32:
        raise ValueError("result_hash must be 32 bytes")
    # Raw 64-byte packed message; encode_defunct applies the EIP-191
    # "\x19Ethereum Signed Message:\n64" prefix automatically.
    message = task_id.to_bytes(32, "big") + result_hash_bytes
    signable = encode_defunct(primitive=message)
    signed = Account.sign_message(signable, private_key=get_private_key_hex())
    # Normalize to 0x-prefixed hex per this module's contract; hexbytes
    # versions differ on whether .hex() includes the prefix.
    sig_hex = signed.signature.hex()
    return sig_hex if sig_hex.startswith("0x") else "0x" + sig_hex


def generate_attestation_token(
    task_id: int,
    result_hash: str,
    tee_address: str | None = None,
    image_digest: str | None = None,
) -> str:
    """
    Build the structured attestation token JSON string (dev-simulated).

    In production (GCP Confidential Space), this would be a real vTPM/JWT
    attestation — see module docstring TODO.
    """
    if tee_address is None:
        tee_address = get_tee_address()
    if image_digest is None:
        image_digest = os.environ.get("TEE_IMAGE_DIGEST", "dev")

    signature = sign_result(task_id, result_hash)

    # Compute nonce for JWT: bind the task/result hash to the attestation.
    nonce_data = f"{task_id}:{result_hash}".encode("utf-8")
    nonce = hashlib.sha256(nonce_data).hexdigest()

    jwt_token = fetch_attestation_jwt(audience="Flare_SecureSignal", nonce=nonce)

    mode_str = (
        "gcp-confidential-space"
        if os.environ.get("ENV") == "prod"
        else "dev-simulated"
    )

    token = {
        "task_id": task_id,
        "result_hash": result_hash,
        "image_digest": image_digest,
        "tee_address": tee_address,
        "timestamp": int(time.time()),
        "mode": mode_str,
        "signature": signature,
        "jwt": jwt_token,
    }
    return json.dumps(token, separators=(",", ":"))
