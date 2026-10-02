import cbor2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from datetime import datetime, timedelta, timezone

from tools import verify_aws_nitro_attestation as verifier


def _certificate(
    subject: x509.Name,
    subject_key,
    issuer: x509.Name | None,
    issuer_key,
    *,
    ca: bool,
) -> x509.Certificate:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer or subject)
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    return builder.sign(issuer_key, hashes.SHA384())


def _fixture_document(nonce: bytes, user_data: bytes, public_key: bytes):
    root_key = ec.generate_private_key(ec.SECP384R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "root")])
    root_cert = _certificate(root_name, root_key, None, root_key, ca=True)

    leaf_key = ec.generate_private_key(ec.SECP384R1())
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "leaf")])
    leaf_cert = _certificate(
        leaf_name,
        leaf_key,
        root_name,
        root_key,
        ca=False,
    )
    payload = {
        "module_id": "test-module",
        "digest": "SHA384",
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "pcrs": {
            0: b"a" * 48,
            1: b"b" * 48,
            2: b"c" * 48,
        },
        "certificate": leaf_cert.public_bytes(serialization.Encoding.DER),
        "cabundle": [root_cert.public_bytes(serialization.Encoding.DER)],
        "public_key": public_key,
        "user_data": user_data,
        "nonce": nonce,
    }
    protected = cbor2.dumps({1: -35})
    payload_bytes = cbor2.dumps(payload)
    signature = leaf_key.sign(
        cbor2.dumps(["Signature1", protected, b"", payload_bytes]),
        ec.ECDSA(hashes.SHA384()),
    )
    return cbor2.dumps(cbor2.CBORTag(18, [protected, {}, payload_bytes, signature])), root_cert


def test_verifies_nitro_attestation_document():
    nonce = b"n" * 32
    user_data = b"u" * 64
    public_key = b"p" * 65
    document, root = _fixture_document(nonce, user_data, public_key)

    result = verifier.verify_document(
        document,
        expected_nonce=nonce,
        expected_user_data=user_data,
        expected_public_key=public_key,
        expected_pcr0="61" * 48,
        trusted_root=root,
    )

    assert result["module_id"] == "test-module"
    assert result["pcrs"][0] == "61" * 48


def test_rejects_nonce_mismatch():
    document, root = _fixture_document(b"n" * 32, b"u" * 64, b"p" * 65)

    with pytest.raises(verifier.NitroAttestationError, match="nonce mismatch"):
        verifier.verify_document(
            document,
            expected_nonce=b"x" * 32,
            trusted_root=root,
        )
