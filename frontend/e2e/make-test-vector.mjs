// Generates frontend/e2e/test-vector.json for Stage 2 cross-stack verification.
// Stage 2 will check that Python (eciespy) can decrypt this JS (eciesjs) ciphertext.
// Protocol: plan.md 「统一加密协议规范」 — secp256k1 ECIES, wire format
//   65B ephemeral pubkey (0x04) || 16B nonce || 16B GCM tag || ciphertext, base64-encoded.

import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { PrivateKey, encrypt, decrypt } from 'eciesjs'

// Fixed test keys (DO NOT use anywhere else).
const TEE_PRIVATE_KEY_HEX =
  '0000000000000000000000000000000000000000000000000000000000000001'
const CLIENT_PRIVATE_KEY_HEX =
  '0000000000000000000000000000000000000000000000000000000000000002'

const teePrivateKey = PrivateKey.fromHex(TEE_PRIVATE_KEY_HEX)
const teePublicKeyHex = teePrivateKey.publicKey.toHex(false) // uncompressed, 04-prefixed
const clientPublicKeyHex = PrivateKey.fromHex(CLIENT_PRIVATE_KEY_HEX).publicKey.toHex(false)

// Fixed plaintext payload per protocol (client_pubkey + holdings + risk_profile).
const plaintext = JSON.stringify({
  client_pubkey: clientPublicKeyHex,
  holdings: { BTC: 2, ETH: 10, USDT: 5000 },
  risk_profile: 'moderate',
})

const ciphertext = encrypt(teePublicKeyHex, new TextEncoder().encode(plaintext))
const ciphertextBase64 = Buffer.from(ciphertext).toString('base64')

// Self-check: eciesjs must be able to decrypt its own ciphertext.
const roundTrip = new TextDecoder().decode(
  decrypt(TEE_PRIVATE_KEY_HEX, Buffer.from(ciphertextBase64, 'base64')),
)
if (roundTrip !== plaintext) {
  throw new Error('self-check failed: eciesjs round-trip mismatch')
}

const vector = {
  description:
    'SecureSignal cross-stack ECIES test vector (plan.md 统一加密协议规范). ' +
    'Stage 2: Python/eciespy must decrypt ciphertext_base64 with tee_private_key_hex back to plaintext.',
  tee_private_key_hex: TEE_PRIVATE_KEY_HEX,
  tee_public_key_hex: teePublicKeyHex,
  plaintext,
  ciphertext_base64: ciphertextBase64,
}

const outPath = join(dirname(fileURLToPath(import.meta.url)), 'test-vector.json')
writeFileSync(outPath, JSON.stringify(vector, null, 2) + '\n')
console.log('wrote', outPath)
console.log('tee_public_key_hex:', teePublicKeyHex)
console.log('ciphertext bytes:', ciphertext.length, '(65 ephemeral pub + 16 nonce + 16 tag +', plaintext.length, 'plaintext)')
console.log('self-check: eciesjs decrypt round-trip OK')
