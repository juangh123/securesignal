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

from coincurve import PrivateKey
from ecies import decrypt as _ecies_decrypt
from ecies import encrypt as _ecies_encrypt
from eth_account import Account

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
    return _ecies_decrypt(_load_private_key(), ciphertext)


def encrypt(receiver_pubkey_hex: str, plaintext: bytes) -> bytes:
    """ECIES encrypt to a receiver public key (65B uncompressed hex, no 0x)."""
    return _ecies_encrypt(receiver_pubkey_hex, plaintext)


def generate_ephemeral_keypair() -> tuple[str, str]:
    """Dev helper: generate a fresh secp256k1 keypair (priv_hex, pub_hex)."""
    key = PrivateKey()  # random valid secp256k1 key
    return key.secret.hex(), key.public_key.format(compressed=False).hex()
