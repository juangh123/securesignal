# Crypto Interface Specification (Single Source of Truth)

This document serves as the absolute contract for the interface between the TEE Service (Python) and the Frontend (TypeScript) components in the SecureSignal project.

> Wire-format authority: the unified protocol in `plan.md`（统一加密协议规范）and the
> two implementations (`frontend/src/utils/crypto.ts`, `tee-service/crypto/keys.py`).
> This document summarizes that contract; if it ever drifts, the code and `plan.md` win.

## 1. Context Payload (TeePayload)

Any payload transmitted to the TEE environment before encryption MUST adhere strictly to the following `TeePayload` structural definition. 

### Structure
```json
{
  "client_pubkey": "string",
  "holdings": {
    "BTC": 1.5,
    "ETH": 10.0
  },
  "risk_profile": "string"
}
```

### TypeScript Definition (Frontend)
```typescript
export interface TeePayload {
  client_pubkey: string;
  holdings: Record<string, number>;
  risk_profile: string;
}
```

### Python Definition (TEE Service)
```python
from typing import TypedDict, Dict

class TeePayload(TypedDict):
    client_pubkey: str
    holdings: Dict[str, float]
    risk_profile: str
```

### Request Envelope (`POST /analyze`)
```json
{
  "task_id": 18,
  "encrypted_data": "<base64 ECIES ciphertext of the JSON-serialized TeePayload>"
}
```

## 2. ECIES Encryption & Wire Formatting

**Algorithm:** secp256k1 ECIES = ephemeral ECDH → HKDF-SHA256 → AES-256-GCM.
Implemented by `eciesjs` (browser) and `eciespy` (TEE service); do not hand-roll either side.

### Wire format (both directions)
```
65B uncompressed ephemeral public key (0x04 prefix) || 16B nonce || 16B GCM tag || ciphertext
```
Transport encoding: **base64** of the bytes above (no `0x` prefix, not hex).

### Key format
- Public key: 65-byte uncompressed point hex with `04` prefix; may be passed with or without `0x`.
- Private key: 32-byte hex.

### Encryption process (Frontend -> TEE)
1. **JSON serialize**: Serialize the `TeePayload` to UTF-8 bytes.
2. **ECIES execution**: `encrypt(teePubKeyHex, plaintext)` with `eciesjs`.
3. **Transport encoding**: base64-encode the ciphertext bytes.
4. **Request**: `POST /analyze` with `{ task_id, encrypted_data }`. The plaintext
   includes `client_pubkey` so the TEE can encrypt the result back to this session.

### Decryption and response (TEE -> Frontend)
1. **Transport decode**: base64-decode `encrypted_data`.
2. **ECIES execution**: decrypt with `TEE_PRIVATE_KEY` via `eciespy`.
3. **JSON deserialize**: parse the plaintext and validate it matches the `TeePayload` contract.
4. **Result encryption**: encrypt the result JSON to `client_pubkey` with `eciespy`,
   base64-encode it, and return it as `encrypted_result` alongside `result_hash` and `attestation`.
5. **Result decryption**: the frontend decrypts `encrypted_result` with the session
   private key using `eciesjs` and parses the result JSON.
