"""Verify an AWS Nitro Enclaves NSM attestation document."""

import argparse
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cbor2
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

DEFAULT_ROOT = (
    Path(__file__).resolve().parent.parent
    / "attestation"
    / "aws_nitro_root_g1.pem"
)
COSE_SIGN1_TAG = 18
ALG_ES384 = -35


class NitroAttestationError(RuntimeError):
    pass


def load_root_certificate(path: Path = DEFAULT_ROOT) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _verify_certificate_signature(
    child: x509.Certificate,
    issuer: x509.Certificate,
) -> None:
    public_key = issuer.public_key()
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(
                child.signature,
                child.tbs_certificate_bytes,
                padding.PKCS1v15(),
                child.signature_hash_algorithm,
            )
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(
                child.signature,
                child.tbs_certificate_bytes,
                ec.ECDSA(child.signature_hash_algorithm),
            )
        elif isinstance(public_key, ed25519.Ed25519PublicKey):
            public_key.verify(child.signature, child.tbs_certificate_bytes)
        else:
            raise NitroAttestationError("unsupported certificate public key")
    except InvalidSignature as exc:
        raise NitroAttestationError("certificate chain signature is invalid") from exc


def _verify_chain(
    leaf: x509.Certificate,
    cabundle_der: list[bytes],
    trusted_root: x509.Certificate,
    now,
) -> None:
    root_der = trusted_root.public_bytes(
        encoding=serialization.Encoding.DER
    )
    if not cabundle_der or cabundle_der[0] != root_der:
        raise NitroAttestationError("attestation CA bundle does not start at the pinned root")

    chain = [leaf] + [
        x509.load_der_x509_certificate(value) for value in cabundle_der[1:]
    ]
    for cert in chain + [trusted_root]:
        if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
            raise NitroAttestationError("attestation certificate is not currently valid")

    current = leaf
    untrusted = list(chain[1:])
    while True:
        if current.subject == trusted_root.subject:
            if current.public_bytes(serialization.Encoding.DER) != root_der:
                raise NitroAttestationError("unexpected root certificate")
            return
        issuer = next(
            (cert for cert in untrusted if cert.subject == current.issuer),
            None,
        )
        if issuer is None:
            if current.issuer == trusted_root.subject:
                issuer = trusted_root
            else:
                raise NitroAttestationError("incomplete attestation certificate chain")
        _verify_certificate_signature(current, issuer)
        current = issuer
        if current is not trusted_root:
            untrusted.remove(current)


def verify_document(
    document: bytes,
    *,
    expected_nonce: bytes | None = None,
    expected_user_data: bytes | None = None,
    expected_public_key: bytes | None = None,
    expected_pcr0: str | None = None,
    trusted_root: x509.Certificate | None = None,
    max_age_seconds: int = 600,
) -> dict:
    root = trusted_root or load_root_certificate()
    decoded = cbor2.loads(document)
    if isinstance(decoded, cbor2.CBORTag):
        if decoded.tag != COSE_SIGN1_TAG:
            raise NitroAttestationError(f"unexpected CBOR tag: {decoded.tag}")
        decoded = decoded.value
    if not isinstance(decoded, list) or len(decoded) != 4:
        raise NitroAttestationError("invalid COSE_Sign1 structure")
    protected_bytes, unprotected, payload_bytes, signature = decoded
    if unprotected:
        raise NitroAttestationError("unexpected unprotected COSE headers")
    protected = cbor2.loads(protected_bytes)
    if protected.get(1) != ALG_ES384:
        raise NitroAttestationError("attestation document is not signed with ES384")
    if not isinstance(payload_bytes, bytes) or not isinstance(signature, bytes):
        raise NitroAttestationError("invalid COSE payload or signature")

    payload = cbor2.loads(payload_bytes)
    required = {"module_id", "digest", "timestamp", "pcrs", "certificate", "cabundle"}
    missing = required - set(payload)
    if missing:
        raise NitroAttestationError(f"attestation document is missing: {sorted(missing)}")

    certificate = x509.load_der_x509_certificate(payload["certificate"])
    now = datetime.now(timezone.utc)
    _verify_chain(certificate, payload["cabundle"], root, now)

    sig_structure = cbor2.dumps(
        ["Signature1", protected_bytes, b"", payload_bytes]
    )
    public_key = certificate.public_key()
    if len(signature) == 96:
        signature = encode_dss_signature(
            int.from_bytes(signature[:48], "big"),
            int.from_bytes(signature[48:], "big"),
        )
    try:
        public_key.verify(
            signature,
            sig_structure,
            ec.ECDSA(hashes.SHA384()),
        )
    except InvalidSignature as exc:
        raise NitroAttestationError("attestation COSE signature is invalid") from exc

    timestamp_ms = payload["timestamp"]
    age = int(time.time() * 1000) - int(timestamp_ms)
    if age < -60_000 or age > max_age_seconds * 1000:
        raise NitroAttestationError(
            f"attestation timestamp is outside the {max_age_seconds}s window"
        )
    if payload.get("nonce") != expected_nonce and expected_nonce is not None:
        raise NitroAttestationError("attestation nonce mismatch")
    if payload.get("user_data") != expected_user_data and expected_user_data is not None:
        raise NitroAttestationError("attestation user_data mismatch")
    if (
        payload.get("public_key") != expected_public_key
        and expected_public_key is not None
    ):
        raise NitroAttestationError("attestation public key mismatch")

    pcrs = {
        int(index): value.hex()
        for index, value in payload["pcrs"].items()
    }
    if expected_pcr0 and pcrs.get(0) != expected_pcr0.lower().removeprefix("0x"):
        raise NitroAttestationError("attestation PCR0 mismatch")

    return {
        "module_id": payload["module_id"],
        "digest": payload["digest"],
        "timestamp": timestamp_ms,
        "pcrs": pcrs,
        "public_key": payload.get("public_key", b"").hex(),
        "user_data": payload.get("user_data", b"").hex(),
        "nonce": payload.get("nonce", b"").hex(),
        "certificate_subject": certificate.subject.rfc4514_string(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("document", nargs="?", help="base64 NSM document; stdin otherwise")
    parser.add_argument("--nonce-hex")
    parser.add_argument("--user-data-hex")
    parser.add_argument("--public-key-hex")
    parser.add_argument("--pcr0")
    parser.add_argument("--max-age-seconds", type=int, default=600)
    args = parser.parse_args()

    encoded = args.document or sys.stdin.read().strip()
    try:
        document = base64.b64decode(encoded, validate=True)
        summary = verify_document(
            document,
            expected_nonce=bytes.fromhex(args.nonce_hex) if args.nonce_hex else None,
            expected_user_data=(
                bytes.fromhex(args.user_data_hex) if args.user_data_hex else None
            ),
            expected_public_key=(
                bytes.fromhex(args.public_key_hex) if args.public_key_hex else None
            ),
            expected_pcr0=args.pcr0,
            max_age_seconds=args.max_age_seconds,
        )
    except (ValueError, NitroAttestationError) as exc:
        print(f"invalid AWS Nitro attestation: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
