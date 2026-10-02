"""Verify a GCP Confidential Space OIDC attestation JWT.

This is the relying-party check used before trusting a production attestation.
It validates the RS256 signature against Google's published JWKS and then
checks the claims that matter to SecureSignal.
"""

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attestation.vtpm import (  # noqa: E402
    CONFIDENTIAL_SPACE_ISSUER,
    AttestationError,
    inspect_attestation_jwt,
)

OIDC_CONFIG_URL = (
    f"{CONFIDENTIAL_SPACE_ISSUER}/.well-known/openid-configuration"
)


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _decode_json_segment(segment: str) -> dict:
    return json.loads(_b64url_decode(segment).decode("utf-8"))


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "SecureSignal/1"})
    with urllib.request.urlopen(request, timeout=10.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _public_key_from_jwk(jwk: dict) -> rsa.RSAPublicKey:
    if jwk.get("kty") != "RSA":
        raise AttestationError("JWKS contains a non-RSA key")
    n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
    e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    return rsa.RSAPublicNumbers(e, n).public_key()


def verify_signature(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise AttestationError("attestation token is not a JWT")
    header = _decode_json_segment(parts[0])
    if header.get("alg") != "RS256":
        raise AttestationError("attestation JWT must use RS256")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise AttestationError("attestation JWT header has no kid")

    config = _fetch_json(OIDC_CONFIG_URL)
    jwks_uri = config.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri:
        raise AttestationError("OIDC configuration has no jwks_uri")
    jwks = _fetch_json(jwks_uri)
    matching = [key for key in jwks.get("keys", []) if key.get("kid") == kid]
    if len(matching) != 1:
        raise AttestationError(f"expected one JWKS key for kid {kid!r}")

    public_key = _public_key_from_jwk(matching[0])
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    signature = _b64url_decode(parts[2])
    try:
        public_key.verify(
            signature,
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        raise AttestationError("attestation JWT signature verification failed") from exc
    return _decode_json_segment(parts[1])


def verify_token(
    token: str,
    *,
    audience: str,
    nonce: str,
    image_digest: str | None = None,
) -> dict:
    claims = verify_signature(token)
    inspect_attestation_jwt(
        token,
        expected_audience=audience,
        expected_nonce=nonce,
    )
    measured = claims["submods"]["container"]["image_digest"]
    if image_digest and measured != image_digest:
        raise AttestationError(
            f"image digest mismatch: expected {image_digest}, got {measured}"
        )
    return {
        "iss": claims.get("iss"),
        "aud": claims.get("aud"),
        "sub": claims.get("sub"),
        "swname": claims.get("swname"),
        "swversion": claims.get("swversion"),
        "dbgstat": claims.get("dbgstat"),
        "eat_nonce": claims.get("eat_nonce"),
        "image_digest": measured,
        "image_reference": claims["submods"]["container"].get("image_reference"),
        "gce": claims.get("submods", {}).get("gce"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("token", nargs="?", help="JWT value; stdin when omitted")
    parser.add_argument("--audience", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--image-digest")
    args = parser.parse_args()

    token = args.token or sys.stdin.read().strip()
    try:
        summary = verify_token(
            token,
            audience=args.audience,
            nonce=args.nonce,
            image_digest=args.image_digest,
        )
    except AttestationError as exc:
        print(f"invalid attestation: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
