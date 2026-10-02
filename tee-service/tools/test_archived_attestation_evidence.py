"""Cryptographic regression test for the archived AWS Nitro evidence.

AWS NSM leaf certificates are only valid for a few hours, so archived
documents are verified at their recorded issuance instant. Every cryptographic
binding (pinned root chain, ES384 COSE signature, PCR0, nonce, user data and
the ECIES public key) is still enforced.
"""

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from eth_utils import keccak

from tools import verify_aws_nitro_attestation as verifier

REPO_ROOT = Path(__file__).resolve().parents[2]
DELIVERABLES = REPO_ROOT / "deliverables"

# keccak256(PCR0) anchored on Coston2 by rotateTeeKey. Task 18 predates the
# measurement commitment; task 20's digest is recorded in plan.md without the
# literal value, so only the two documented commitments are asserted here.
CHAIN_COMMITMENTS = {
    21: "92ba6b1956182011f2cf46fa032d9c45b64f779e66697775439e21b93614be0f",
    22: "c9ff301894c99edbab0f2e673c0e7363c8de67b481f7466fc43a0333207a7331",
}


def _load(name: str) -> dict:
    return json.loads((DELIVERABLES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("task_id", (18, 20, 21, 22))
def test_archived_attestation_is_cryptographically_valid(task_id):
    evidence = _load(f"aws-nitro-attestation-{task_id}.json")
    recorded = _load(f"aws-nitro-attestation-{task_id}-verification.json")
    attestation = evidence["attestation"]

    document = base64.b64decode(attestation["nsm_document"], validate=True)
    assert hashlib.sha256(document).hexdigest() == attestation["nsm_document_sha256"]

    issued_at = datetime.fromtimestamp(
        int(recorded["nsm"]["timestamp"]) / 1000, tz=timezone.utc
    )
    summary = verifier.verify_document(
        document,
        expected_nonce=bytes.fromhex(recorded["nsm"]["nonce"]),
        expected_user_data=bytes.fromhex(recorded["nsm"]["user_data"]),
        expected_public_key=bytes.fromhex(recorded["nsm"]["public_key"]),
        expected_pcr0=recorded["nsm"]["pcrs"]["0"],
        max_age_seconds=60,
        now=issued_at,
    )

    assert summary["verified_at"] == issued_at.isoformat()
    assert summary["module_id"] == recorded["nsm"]["module_id"]
    assert summary["digest"] == recorded["nsm"]["digest"]
    for index in ("0", "1", "2"):
        assert summary["pcrs"][int(index)] == recorded["nsm"]["pcrs"][index]
    assert summary["nonce"] == recorded["nsm"]["nonce"]
    assert summary["user_data"] == recorded["nsm"]["user_data"]
    assert summary["public_key"] == recorded["nsm"]["public_key"]

    commitment = CHAIN_COMMITMENTS.get(task_id)
    if commitment is not None:
        assert keccak(bytes.fromhex(summary["pcrs"][0])).hex() == commitment
    # Task 18 predates the `onchain_status` key and records it as `status`.
    assert evidence.get("onchain_status", evidence.get("status")) == 3
    assert recorded["onchain_status"] == 3
    assert recorded["verified"] is True
    assert recorded["result_hash"] == evidence["result_hash"]
    assert evidence["result_hash"] == attestation["result_hash"]
