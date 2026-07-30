// Real ECIES encryption for the SecureSignal frontend.
// Protocol spec: see plan.md 「统一加密协议规范」
//   secp256k1 ECIES = ephemeral ECDH -> HKDF-SHA256 -> AES-256-GCM
//   Wire format (eciesjs/eciespy default, cross-compatible):
//     65B uncompressed ephemeral pubkey (0x04 prefix) || 16B nonce || 16B GCM tag || ciphertext
//   Transport encoding: base64 of the above bytes.
// Key format: public key = 65-byte uncompressed point hex ("04" prefix, no "0x");
//             private key = 32-byte hex (no "0x").

import { PrivateKey, encrypt, decrypt } from 'eciesjs'

export interface SessionKeyPair {
  /** 32-byte hex, no 0x prefix. NEVER sent anywhere. */
  privateKeyHex: string
  /** 65-byte uncompressed point hex with "04" prefix, no 0x prefix. */
  publicKeyHex: string
}

export interface TeePayload {
  client_pubkey: string;
  holdings: Record<string, number>;
  risk_profile: string;
}

/** Generate a fresh per-session secp256k1 key pair. */
export function generateSessionKeyPair(): SessionKeyPair {
  const sk = new PrivateKey()
  return {
    privateKeyHex: sk.toHex(),
    publicKeyHex: sk.publicKey.toHex(false), // uncompressed, 04-prefixed
  }
}

/**
 * Encrypt a payload object for the TEE.
 * @param teePubHex TEE public key, 65-byte uncompressed hex ("04" prefix, no "0x")
 * @param payloadObj plaintext object per protocol:
 *        { client_pubkey: string, holdings: Record<string, number>, risk_profile: string }
 * @returns hex string of (ephemeral pubkey || nonce || tag || ciphertext)
 */
export function encryptForTee(teePubHex: string, payloadObj: TeePayload): string {
  const plaintext = new TextEncoder().encode(JSON.stringify(payloadObj))
  const ciphertext = encrypt(teePubHex, plaintext)
  // return as hex to match python behavior instead of base64
  return '0x' + Buffer.from(ciphertext).toString('hex');
}

/**
 * Decrypt the TEE's encrypted_result with the session private key.
 * @param sessionPrivHex session private key hex (no 0x)
 * @param ciphertextHex hex wire payload from the TEE
 * @returns parsed result object
 */
export function decryptResult<T = unknown>(sessionPrivHex: string, ciphertextHex: string): T {
  const cleanHex = ciphertextHex.replace(/^0x/i, '');
  const ciphertext = Buffer.from(cleanHex, 'hex');
  const plaintext = decrypt(sessionPrivHex, ciphertext)
  return JSON.parse(new TextDecoder().decode(plaintext)) as T
}

/** Normalize a public key hex for comparison: strip 0x, lowercase. */
export function normalizePubKeyHex(hex: string): string {
  return hex.replace(/^0x/i, '').toLowerCase()
}
