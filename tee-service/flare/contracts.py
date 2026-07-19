"""
Web3 relayer: submits analysis results to the AnalysisRegistry contract.

Configuration:
  - PRIVATE_KEY (env, hex): relayer account that pays gas and calls
    submitResult. If unset, on-chain submission is disabled.
  - RPC_URL (env): defaults to Coston2.
  - config/AnalysisRegistry.json: contract artifact containing the ABI.
  - config/contract-addresses.json: {"AnalysisRegistry": "0x...", ...}.

Contract call (see plan.md):
  submitResult(uint256 taskId, bytes32 resultHash, bytes attestation)
  where `attestation` is the 65-byte TEE secp256k1 signature over
  (task_id, result_hash); the contract verifies it via ecrecover against
  the registered teeAddress.
"""

import json
import os
from pathlib import Path
from typing import Optional

from web3 import Web3

DEFAULT_RPC_URL = "https://coston2-api.flare.network/ext/C/rpc"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class RelayerNotConfigured(RuntimeError):
    pass


def _load_abi() -> list:
    artifact_path = CONFIG_DIR / "AnalysisRegistry.json"
    with open(artifact_path, "r", encoding="utf-8") as f:
        artifact = json.load(f)
    return artifact["abi"]


def _load_registry_address() -> str:
    addresses_path = CONFIG_DIR / "contract-addresses.json"
    with open(addresses_path, "r", encoding="utf-8") as f:
        addresses = json.load(f)
    return addresses.get("AnalysisRegistry", "")


def is_configured() -> bool:
    """True if relayer private key and a non-zero registry address exist."""
    if not os.environ.get("PRIVATE_KEY", "").strip():
        return False
    try:
        addr = _load_registry_address()
    except Exception:
        return False
    return bool(addr) and addr.lower() != ZERO_ADDRESS.lower()


def submit_result(
    task_id: int,
    result_hash: str,
    attestation_sig: str,
    rpc_url: Optional[str] = None,
) -> str:
    """
    Submit (task_id, result_hash, attestation signature) on-chain as the
    relayer. Returns the transaction hash (0x hex).

    Raises RelayerNotConfigured if PRIVATE_KEY / registry address missing,
    and propagates web3/RPC errors to the caller.
    """
    private_key = os.environ.get("PRIVATE_KEY", "").strip()
    if not private_key:
        raise RelayerNotConfigured("PRIVATE_KEY env not set; relayer disabled")

    registry_address = _load_registry_address()
    if not registry_address or registry_address.lower() == ZERO_ADDRESS.lower():
        raise RelayerNotConfigured(
            "AnalysisRegistry address not configured in "
            "config/contract-addresses.json (zero address)"
        )

    rpc_url = rpc_url or os.environ.get("RPC_URL", DEFAULT_RPC_URL)
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to RPC {rpc_url}")

    account = w3.eth.account.from_key(private_key)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(registry_address),
        abi=_load_abi(),
    )

    result_hash_bytes = bytes.fromhex(
        result_hash[2:] if result_hash.startswith("0x") else result_hash
    )
    if len(result_hash_bytes) != 32:
        raise ValueError("result_hash must be 32 bytes")
    attestation_bytes = bytes.fromhex(
        attestation_sig[2:] if attestation_sig.startswith("0x") else attestation_sig
    )

    nonce = w3.eth.get_transaction_count(account.address)
    tx = contract.functions.submitResult(
        int(task_id), result_hash_bytes, attestation_bytes
    ).build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "gas": 300_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
    )
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status != 1:
        raise RuntimeError(
            f"submitResult reverted on-chain (tx {tx_hash.hex()})"
        )
    return tx_hash.hex()
