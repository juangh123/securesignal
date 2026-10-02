"""
Attestation token generation.

Development mode returns an explicitly labelled JSON token
(``mode: "dev-simulated"``) signed by the TEE key. Production mode runs inside
GCP Confidential Space and obtains a Google-signed OIDC attestation token from
the launcher's Unix-domain socket. The result is still signed with the TEE key
for the existing on-chain EIP-191 check, while the JWT proves the workload image
and binds the task/result nonce to Confidential Space.

Confidential Space token request:
  POST http://localhost/v1/token over /run/container_launcher/teeserver.sock
  {"audience":"...", "token_type":"OIDC", "nonces":["..."]}

The JWT is verified by a relying party against the issuer's JWKS. This module
parses the returned claims only to bind the expected nonce/audience and expose
the measured image digest in the response.
"""

import base64
import hashlib
import http.client
import json
import os
import socket
import time

from eth_account import Account
from eth_account.messages import encode_defunct

from attestation import aws_nsm
from crypto.keys import get_private_key_hex, get_public_key_hex, get_tee_address

DEFAULT_AUDIENCE = "https://securesignal.app"
DEFAULT_SOCKET_PATH = "/run/container_launcher/teeserver.sock"
TOKEN_PATH = "/v1/token"
CONFIDENTIAL_SPACE_ISSUER = "https://confidentialcomputing.googleapis.com"


class AttestationError(RuntimeError):
    """Raised when a production attestation cannot be obtained or validated."""


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that sends the request over a Unix-domain socket."""

    def __init__(self, socket_path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def is_production_mode() -> bool:
    return os.environ.get("ENV", "").strip().lower() == "prod"


def attestation_provider() -> str:
    explicit = os.environ.get("ATTESTATION_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("AWS_NITRO_ENCLAVES", "").strip() == "1":
        return "aws-nitro-enclaves"
    return "gcp-confidential-space" if is_production_mode() else "dev-simulated"


def attestation_mode() -> str:
    return attestation_provider()


def attestation_socket_path() -> str:
    return os.environ.get("GCP_ATTESTATION_SOCKET", DEFAULT_SOCKET_PATH)


def attestation_audience() -> str:
    return os.environ.get("GCP_ATTESTATION_AUDIENCE", DEFAULT_AUDIENCE)


def ensure_attestation_runtime() -> None:
    """Fail closed when the selected production provider is unavailable."""
    provider = attestation_provider()
    if provider == "dev-simulated":
        return
    if provider == "aws-nitro-enclaves":
        try:
            aws_nsm.ensure_nitro_runtime()
        except aws_nsm.NsmAttestationError as exc:
            raise RuntimeError(str(exc)) from exc
        return
    if provider != "gcp-confidential-space":
        raise RuntimeError(f"unsupported attestation provider: {provider}")
    socket_path = attestation_socket_path()
    if not os.path.exists(socket_path):
        raise RuntimeError(
            "ENV=prod requires the GCP Confidential Space launcher socket at "
            f"{socket_path}; refusing to advertise a simulated attestation"
        )


def ensure_confidential_space_runtime() -> None:
    """Backward-compatible alias for callers from earlier builds."""
    ensure_attestation_runtime()


def fetch_attestation_jwt(
    audience: str,
    nonce: str,
    *,
    socket_path: str | None = None,
    timeout: float = 5.0,
) -> str:
    """Request a Google-signed OIDC attestation token from the launcher."""
    if not (10 <= len(nonce) <= 74):
        raise ValueError("Confidential Space nonce must be 10-74 bytes")

    path = socket_path or attestation_socket_path()
    body = json.dumps(
        {"audience": audience, "token_type": "OIDC", "nonces": [nonce]},
        separators=(",", ":"),
    ).encode("utf-8")
    connection = _UnixHTTPConnection(path, timeout)
    try:
        connection.request(
            "POST",
            TOKEN_PATH,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read()
        if response.status != 200:
            detail = raw.decode("utf-8", errors="replace")[:500]
            raise AttestationError(
                "Confidential Space token endpoint returned "
                f"HTTP {response.status}: {detail}"
            )
    except (OSError, socket.timeout, http.client.HTTPException) as exc:
        raise AttestationError(
            f"failed to reach Confidential Space token socket {path}: {exc}"
        ) from exc
    finally:
        connection.close()

    token = raw.decode("utf-8").strip()
    if token.startswith('"') and token.endswith('"'):
        try:
            token = json.loads(token)
        except json.JSONDecodeError as exc:
            raise AttestationError("token endpoint returned invalid JSON") from exc
    if not isinstance(token, str) or token.count(".") != 2:
        raise AttestationError("token endpoint did not return a JWT")
    return token


def _decode_jwt_segment(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(segment + padding)
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttestationError("attestation JWT contains invalid JSON") from exc
    if not isinstance(value, dict):
        raise AttestationError("attestation JWT segment must be a JSON object")
    return value


def inspect_attestation_jwt(
    token: str,
    *,
    expected_audience: str,
    expected_nonce: str,
    now: int | None = None,
) -> dict:
    """Parse and sanity-check claims before exposing the signed JWT.

    Signature-chain validation is performed by the relying party. These checks
    ensure the local workload did not return an unrelated/stale token.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise AttestationError("attestation token is not a JWT")
    header = _decode_jwt_segment(parts[0])
    claims = _decode_jwt_segment(parts[1])

    if header.get("alg") != "RS256":
        raise AttestationError("attestation JWT must use RS256")
    if claims.get("iss") != CONFIDENTIAL_SPACE_ISSUER:
        raise AttestationError("attestation JWT has an unexpected issuer")
    if claims.get("aud") != expected_audience:
        raise AttestationError("attestation JWT audience mismatch")
    nonces = claims.get("eat_nonce")
    if not isinstance(nonces, list) or expected_nonce not in nonces:
        raise AttestationError("attestation JWT nonce mismatch")
    if claims.get("swname") != "CONFIDENTIAL_SPACE":
        raise AttestationError("attestation JWT is not a Confidential Space token")

    current_time = int(time.time()) if now is None else now
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= current_time:
        raise AttestationError("attestation JWT is expired")
    if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > current_time + 60:
        raise AttestationError("attestation JWT is not valid yet")

    dbgstat = claims.get("dbgstat")
    if dbgstat not in {"disabled-since-boot", "disabled"}:
        raise AttestationError(
            f"Confidential Space debug policy is not production-safe: {dbgstat!r}"
        )

    try:
        image_digest = claims["submods"]["container"]["image_digest"]
    except (KeyError, TypeError) as exc:
        raise AttestationError("attestation JWT is missing container.image_digest") from exc
    if not isinstance(image_digest, str) or not image_digest.startswith("sha256:"):
        raise AttestationError("attestation JWT image digest is invalid")
    return claims


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
    Build the attestation token JSON string.

    ``dev-simulated`` is honest local development output. Production tokens
    include a hardware-rooted attestation document from either GCP Confidential
    Space or AWS Nitro Enclaves; failures are fatal, never downgraded.
    """
    if tee_address is None:
        tee_address = get_tee_address()

    signature = sign_result(task_id, result_hash)

    # Compute nonce for JWT: bind the task/result hash to the attestation.
    nonce_data = f"{task_id}:{result_hash}".encode("utf-8")
    nonce = hashlib.sha256(nonce_data).hexdigest()

    provider = attestation_provider()
    mode_str = attestation_mode()
    jwt_token: str | None = None
    aws_fields: dict = {}
    if provider == "gcp-confidential-space":
        audience = attestation_audience()
        jwt_token = fetch_attestation_jwt(audience, nonce)
        claims = inspect_attestation_jwt(
            jwt_token,
            expected_audience=audience,
            expected_nonce=nonce,
        )
        measured_digest = claims["submods"]["container"]["image_digest"]
        configured_digest = image_digest or os.environ.get("TEE_IMAGE_DIGEST")
        if (
            configured_digest
            and configured_digest not in {"dev", "sha256:unknown"}
            and configured_digest != measured_digest
        ):
            raise AttestationError(
                "TEE_IMAGE_DIGEST does not match the Confidential Space "
                f"measurement ({configured_digest!r} != {measured_digest!r})"
            )
        image_digest = measured_digest
    elif provider == "aws-nitro-enclaves":
        result_hash_bytes = bytes.fromhex(
            result_hash[2:] if result_hash.startswith("0x") else result_hash
        )
        user_data = task_id.to_bytes(32, "big") + result_hash_bytes
        aws_fields = aws_nsm.attestation_fields(
            nonce=bytes.fromhex(nonce),
            user_data=user_data,
            public_key=bytes.fromhex(get_public_key_hex()),
        )
        image_digest = os.environ.get("AWS_NITRO_PCR0", image_digest or "prod")
    elif provider != "dev-simulated":
        raise AttestationError(f"unsupported attestation provider: {provider}")
    elif image_digest is None:
        image_digest = os.environ.get("TEE_IMAGE_DIGEST", "dev")

    token = {
        "task_id": task_id,
        "result_hash": result_hash,
        "image_digest": image_digest,
        "tee_address": tee_address,
        "timestamp": int(time.time()),
        "mode": mode_str,
        "signature": signature,
    }
    if jwt_token is not None:
        token["jwt"] = jwt_token
        token["attestation_audience"] = attestation_audience()
        token["attestation_nonce"] = nonce
    if aws_fields:
        token.update(aws_fields)
    return json.dumps(token, separators=(",", ":"))
