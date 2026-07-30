# Crypto Interface Specification (Single Source of Truth)

This document serves as the absolute contract for the interface between the TEE Service (Python) and the Frontend (TypeScript) components in the SecureSignal project.

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

## 2. ECIES Encryption & Wire Formatting

To reduce cognitive load regarding how exact byte streams pass over the network, standard ECIES constraints are adhered to:

### Encryption process (Frontend -> TEE)
1. **Input JSON Serialize**: Serialize the exactly defined `TeePayload` into a JSON string, then into raw bytes.
2. **Public Key Processing**: Resolve the `tee_pub_key_hex` (it can flexibly support with `0x` prefix or without; internally parse it into `Buffer`). Ensure it represents standard uncompressed secp256k1 public key dimensions.
3. **ECIES Execution**: Encrypt using `eciesjs`.
4. **Wire Wrapping**: Encoded in `.hex()` output buffer format, prepended with `0x`.

### Decryption process (TEE -> TEE/Frontend)
1. **Hex decode**: Strip `0x` prefix (if any) and unhexlify.
2. **ECIES Execution**: Decrypt using `eciespy` initialized by the local secp256k1 private key.
3. **JSON Deserialize**: Decode resulting bytes -> JSON string.
4. **Cast**: Load dict structure. Verify properties match the `TeePayload` contract prior to subsequent operations.
