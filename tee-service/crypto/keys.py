"""
TEE secp256k1 ECIES key management.

Protocol (see plan.md "统一加密协议规范"):
  - Algorithm: secp256k1 ECIES = ephemeral ECDH -> HKDF-SHA256 -> AES-256-GCM
  - Library: eciespy (pip package; import name `ecies`), wire-format
    compatible with frontend eciesjs.
  - Wire format: 65B uncompressed ephemeral pubkey (0x04 prefix) || 16B nonce
    || 16B GCM tag || ciphertext, base64-encoded for transport.
  - Public key format: 65-byte uncompressed point hex, with "04" prefix,
    WITHOUT "0x". Private key: 32-byte hex.

Key source:
  - Env var TEE_PRIVATE_KEY (hex, with or without 0x prefix).
  - If unset, an ephemeral key is generated for the process lifetime and a
    loud warning is printed (dev only).
"""

import os
import secrets
import json
from typing import TypedDict, Dict

from coincurve import PrivateKey
from ecies import decrypt as _ecies_decrypt
from ecies import encrypt as _ecies_encrypt
from eth_account import Account

class TeePayload(TypedDict):
    client_pubkey: str
    holdings: Dict[str, float]
    risk_profile: str

_private_key_bytes: bytes | None = None


def _load_private_key() -> bytes:
    global _private_key_bytes
    if _private_key_bytes is not None:
        return _private_key_bytes

    env_key = os.environ.get("TEE_PRIVATE_KEY", "").strip()
    if env_key:
        hex_key = env_key[2:] if env_key.lower().startswith("0x") else env_key
        key = bytes.fromhex(hex_key)
        if len(key) != 32:
            raise ValueError(
                f"TEE_PRIVATE_KEY must be 32 bytes hex, got {len(key)} bytes"
            )
        _private_key_bytes = key
        print("[keys] TEE private key loaded from env TEE_PRIVATE_KEY")
    else:
        if os.environ.get("ENV") == "prod":
            raise RuntimeError("CRITICAL: TEE_PRIVATE_KEY is missing in production. Refusing to fallback to ephemeral key (Fail-closed).")
            
        _private_key_bytes = secrets.token_bytes(32)
        # Ensure it is a valid secp256k1 scalar (extremely unlikely to retry).
        while not (1 < int.from_bytes(_private_key_bytes, "big")):
            _private_key_bytes = secrets.token_bytes(32)
        print(
            "[keys] WARNING: TEE_PRIVATE_KEY not set. Generated an EPHEMERAL "
            "dev key for this process only. Do NOT use in production."
        )
    return _private_key_bytes


def init_keys() -> None:
    """Eagerly load/generate keys (call at startup)."""
    _load_private_key()


def get_private_key_hex() -> str:
    """32-byte private key hex, no 0x prefix."""
    return _load_private_key().hex()


def get_public_key_hex() -> str:
    """65-byte uncompressed public key hex (04 prefix, no 0x)."""
    return PrivateKey(_load_private_key()).public_key.format(
        compressed=False
    ).hex()


def get_tee_address() -> str:
    """Checksummed Ethereum address derived from the TEE private key."""
    return Account.from_key(_load_private_key()).address


def decrypt(ciphertext: bytes) -> bytes:
    """ECIES decrypt with the TEE private key (eciespy/eciesjs wire format)."""
    # use get_private_key_hex to hand string to _ecies_decrypt
    return _ecies_decrypt(get_private_key_hex(), ciphertext)

def decrypt_payload(ciphertext_hex: str) -> TeePayload:
    """Decrypt and validate against TeePayload schema."""
    # Strip 0x if present
    if ciphertext_hex.lower().startswith("0x"):
        ciphertext_hex = ciphertext_hex[2:]
    
    # Decrypt
    plaintext_bytes = decrypt(bytes.fromhex(ciphertext_hex))
    
    # Deserialize & Cast
    data = json.loads(plaintext_bytes.decode('utf-8'))
    
    if "client_pubkey" not in data or "holdings" not in data or "risk_profile" not in data:
        raise ValueError("Invalid TeePayload structure")
        
    return TeePayload(
        client_pubkey=data["client_pubkey"],
        holdings=data["holdings"],
        risk_profile=data["risk_profile"]
    )


def encrypt(receiver_pubkey_hex: str, plaintext: bytes) -> bytes:
    """ECIES encrypt to a receiver public key (65B uncompressed hex, no 0x)."""
    return _ecies_encrypt(receiver_pubkey_hex, plaintext)


def encrypt_response(pubkey_hex: str, response_obj: dict) -> str:
    """Encrypt output data for the user."""
    if pubkey_hex.startswith("0x"):
        pubkey_hex = pubkey_hex[2:]
        
    plaintext = json.dumps(response_obj).encode('utf-8')
    ciphertext = _ecies_encrypt(pubkey_hex, plaintext)
    
    # Return as hex starting with '0x'
    return '0x' + ciphertext.hex()


def generate_ephemeral_keypair() -> tuple[str, str]:
    """Dev helper: generate a fresh secp256k1 keypair (priv_hex, pub_hex)."""
    key = PrivateKey()  # random valid secp256k1 key
    return key.secret.hex(), key.public_key.format(compressed=False).hex()
