"""
Attestation token generation.

Development mode returns an explicitly labelled JSON token
(``mode: "dev-simulated"``) signed by the TEE key. Production mode runs inside
GCP Confidential Space and obtains a Google-signed OIDC attestation token from
the launcher's Unix-domain socket. The result is still signed with the TEE key
for the on-chain check, while the JWT proves the workload image and binds the
task/result nonce to Confidential Space.

On-chain signature convention (kept in lockstep with AnalysisRegistry):
  * ``eip712-v2``     - EIP-712 typed data, domain
                        {name:"SecureSignal", version:"2", chainId,
                         verifyingContract: registry}, type
                        Attestation(uint256 taskId, bytes32 resultHash).
                        The domain binds the signature to one chain + registry,
                        so it cannot be replayed against another deployment.
  * ``eip191-legacy`` - EIP-191 personal_sign over the raw 64-byte
                        abi.encodePacked(uint256 taskId, bytes32 resultHash).
                        Registries deployed before the v2 revision only accept
                        this; it has no domain binding.
The active convention is discovered from the registry itself (see
``attestation_scheme``) so the same service build works against both revisions.

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
import math
import os
import socket
import time

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_utils import keccak, to_checksum_address

from attestation import aws_nsm
from crypto.keys import get_private_key_hex, get_public_key_hex, get_tee_address
from flare.contracts import SCHEME_EIP191_LEGACY, SCHEME_EIP712_V2

DEFAULT_AUDIENCE = "https://securesignal.app"
DEFAULT_SOCKET_PATH = "/run/container_launcher/teeserver.sock"
TOKEN_PATH = "/v1/token"
CONFIDENTIAL_SPACE_ISSUER = "https://confidentialcomputing.googleapis.com"

EIP712_DOMAIN_NAME = "SecureSignal"
EIP712_DOMAIN_VERSION = "2"
EIP712_ATTESTATION_TYPES = {
    "Attestation": [
        {"name": "taskId", "type": "uint256"},
        {"name": "resultHash", "type": "bytes32"},
    ]
}

# How long an inconclusive probe is trusted before it is retried. A definitive
# answer (env override, or a registry that answered) is cached for the process
# lifetime; an RPC outage must not pin the service to the wrong convention.
SCHEME_RECHECK_SECONDS = 300.0

_scheme_cache: str | None = None
_scheme_cache_expires_at: float = 0.0
# True when _scheme_cache came from a definitive source (env override or the
# registry marker); False for the provisional legacy default. The signing path
# refuses to use a provisional value on a cold start.
_scheme_cache_definitive = False
# Last definitive answer seen in this process, kept so a transient RPC outage
# cannot downgrade an already-known v2 deployment to legacy signing.
_scheme_last_definitive: str | None = None
_eip712_domain_cache: dict | None = None


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


def _parse_result_hash(result_hash: str) -> bytes:
    """Decode a 0x-prefixed (or bare) 32-byte hex result hash."""
    raw_hex = result_hash[2:] if result_hash.startswith("0x") else result_hash
    try:
        decoded = bytes.fromhex(raw_hex)
    except ValueError as exc:
        raise ValueError("result_hash must be hex") from exc
    if len(decoded) != 32:
        raise ValueError("result_hash must be 32 bytes")
    return decoded


def _env_scheme_override() -> str | None:
    """Explicit ATTESTATION_SCHEME override, if the operator set one."""
    raw = os.environ.get("ATTESTATION_SCHEME", "").strip().lower()
    if raw in {"eip191", "eip191-legacy", "legacy"}:
        return SCHEME_EIP191_LEGACY
    if raw in {"eip712", "eip712-v2"}:
        return SCHEME_EIP712_V2
    return None


def attestation_scheme(refresh: bool = False, strict: bool = False) -> str:
    """Which on-chain signature convention to use.

    Resolution order:
      1. ``ATTESTATION_SCHEME`` env override (``eip191`` / ``eip712``)
      2. the registry's own ``ATTESTATION_SCHEME()`` marker (v2 revisions)
      3. legacy EIP-191 - what every registry deployed before the marker accepts

    The probe is skipped entirely when no relayer is configured: without one
    there is no on-chain submission for the signature to match, and offline
    dev/test runs must not pay an RPC round trip per attestation.

    Definitive answers are cached for the process lifetime; an inconclusive
    probe is only trusted for ``SCHEME_RECHECK_SECONDS`` so a recovered RPC can
    still upgrade the service to the v2 convention.

    ``strict=True`` (used by the signing path) never guesses: when the probe is
    inconclusive and no previous definitive answer exists, it raises instead of
    producing a signature the target registry would reject. Non-strict callers
    such as ``/health`` keep reporting the legacy default so monitoring stays
    available.
    """
    global _scheme_cache, _scheme_cache_expires_at
    global _scheme_cache_definitive, _scheme_last_definitive
    now = time.monotonic()
    if (
        _scheme_cache is not None
        and not refresh
        and now < _scheme_cache_expires_at
        and (_scheme_cache_definitive or not strict)
    ):
        return _scheme_cache

    override = _env_scheme_override()
    if override is not None:
        _scheme_cache = override
        _scheme_cache_expires_at = math.inf
        _scheme_cache_definitive = True
        _scheme_last_definitive = override
        return override

    try:
        from flare.contracts import is_configured as relayer_configured

        relayer_ready = relayer_configured()
    except Exception:
        relayer_ready = False

    if not relayer_ready:
        _scheme_cache = SCHEME_EIP191_LEGACY
        _scheme_cache_expires_at = now + SCHEME_RECHECK_SECONDS
        _scheme_cache_definitive = False
        return _scheme_cache

    detected: str | None = None
    try:
        # Imported lazily: attestation must stay importable without the relayer
        # (and its web3/RPC configuration) being present, e.g. in unit tests.
        from flare.contracts import registry_attestation_scheme

        detected = registry_attestation_scheme()
    except Exception:
        detected = None

    if detected in {SCHEME_EIP712_V2, SCHEME_EIP191_LEGACY}:
        _scheme_cache = detected
        _scheme_cache_expires_at = math.inf
        _scheme_cache_definitive = True
        _scheme_last_definitive = detected
        return detected

    if _scheme_last_definitive is not None:
        # A definitive answer was seen earlier in this process: keep signing
        # with it while the probe is failing, and retry after the window.
        _scheme_cache = _scheme_last_definitive
        _scheme_cache_expires_at = now + SCHEME_RECHECK_SECONDS
        _scheme_cache_definitive = True
        return _scheme_cache

    if strict:
        raise AttestationError(
            "cannot determine the registry attestation scheme: the RPC probe "
            "failed and no previous answer is cached; refusing to sign a result "
            "the registry would reject. Set ATTESTATION_SCHEME to pin it."
        )

    _scheme_cache = SCHEME_EIP191_LEGACY
    _scheme_cache_expires_at = now + SCHEME_RECHECK_SECONDS
    _scheme_cache_definitive = False
    return _scheme_cache


def _eip712_domain(refresh: bool = False) -> dict:
    """EIP-712 domain of the configured AnalysisRegistry deployment.

    Cached after the first successful resolution: the domain (chain id +
    registry address) only changes when the deployment is re-pointed, which
    requires a restart anyway, and signing must not depend on a live RPC call
    for every attestation.
    """
    global _eip712_domain_cache
    if _eip712_domain_cache is not None and not refresh:
        return dict(_eip712_domain_cache)

    from flare.contracts import registry_address, registry_chain_id

    contract = registry_address()
    chain_id = registry_chain_id()
    if not contract or chain_id is None:
        raise AttestationError(
            "eip712-v2 attestation requires the registry address "
            "(config/contract-addresses.json) and a reachable RPC for the chain "
            "id; set ATTESTATION_SCHEME=eip191 to force the legacy convention"
        )
    _eip712_domain_cache = {
        "name": EIP712_DOMAIN_NAME,
        "version": EIP712_DOMAIN_VERSION,
        "chainId": int(chain_id),
        "verifyingContract": to_checksum_address(contract),
    }
    return dict(_eip712_domain_cache)


def _eip712_signable(task_id: int, result_hash_bytes: bytes):
    """SignableMessage for the v2 Attestation struct under the deployed domain."""
    return encode_typed_data(
        domain_data=_eip712_domain(),
        message_types=EIP712_ATTESTATION_TYPES,
        message_data={
            "taskId": int(task_id),
            "resultHash": result_hash_bytes,
        },
    )


def eip712_attestation_digest(task_id: int, result_hash: str) -> bytes:
    """EIP-712 digest the registry hashes for ``Attestation(taskId, resultHash)``.

    Exposed so tests (and cross-language fixtures) can pin the exact 32 bytes the
    contract must recover, without re-implementing the hashing rules.

    ``eth_account`` encodes typed data as a SignableMessage whose version/header/
    body are exactly ``\\x01 || domainSeparator || hashStruct``, so the EIP-191
    envelope rule ``keccak256("\\x19" || version || header || body)`` yields the
    same digest as OpenZeppelin's ``_hashTypedDataV4``.
    """
    signable = _eip712_signable(int(task_id), _parse_result_hash(result_hash))
    return keccak(
        b"\x19" + bytes(signable.version) + bytes(signable.header) + bytes(signable.body)
    )


def sign_result(task_id: int, result_hash: str) -> str:
    """
    Sign (task_id, result_hash) with the TEE private key.

    The convention is selected by ``attestation_scheme()``:

      * ``eip712-v2``: EIP-712 typed data. The digest is
            keccak256("\\x19\\x01" || domainSeparator || hashStruct(Attestation))
        with domain {name, version, chainId, verifyingContract}.
      * ``eip191-legacy``: EIP-191 personal_sign over the raw 64-byte packed
            message = abi.encodePacked(uint256 taskId, bytes32 resultHash)
        which the pre-v2 AnalysisRegistry verifies as
            keccak256("\\x19Ethereum Signed Message:\\n64" || message)
        Do NOT keccak256 the message beforehand.

    Returns 0x-prefixed 65-byte signature hex (r || s || v).
    """
    result_hash_bytes = _parse_result_hash(result_hash)
    # Never guess on the signing path: a wrong convention produces a signature
    # the target registry rejects (and a wasted relayer transaction).
    scheme = attestation_scheme(strict=True)

    if scheme == SCHEME_EIP712_V2:
        signable = _eip712_signable(task_id, result_hash_bytes)
    else:
        # Raw 64-byte packed message; encode_defunct applies the EIP-191
        # "\x19Ethereum Signed Message:\n64" prefix automatically.
        message = int(task_id).to_bytes(32, "big") + result_hash_bytes
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
        # Prefer the PCR0 parsed from the signed NSM document; the env copy is
        # only a fallback (and is cross-checked against the document in
        # aws_nsm.attestation_fields).
        image_digest = (
            aws_fields.get("pcr0")
            or os.environ.get("AWS_NITRO_PCR0")
            or image_digest
            or "prod"
        )
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
        # Which on-chain convention `signature` follows, so clients/e2e can
        # verify it without guessing (older builds omit the field = legacy).
        "attestation_scheme": attestation_scheme(),
        "signature": signature,
    }
    if jwt_token is not None:
        token["jwt"] = jwt_token
        token["attestation_audience"] = attestation_audience()
        token["attestation_nonce"] = nonce
    if aws_fields:
        token.update(aws_fields)
    return json.dumps(token, separators=(",", ":"))
