/**
 * Stage 2 task 5b: Node/eciesjs decrypts a ciphertext produced by
 * Python/eciespy (e2e/py-to-js-vector.json). Direction PY -> JS.
 */
import { decrypt } from 'eciesjs';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dir = dirname(fileURLToPath(import.meta.url));
const v = JSON.parse(readFileSync(join(__dir, 'py-to-js-vector.json'), 'utf-8'));

const ct = new Uint8Array(Buffer.from(v.ciphertext_base64, 'base64'));
const pt = new TextDecoder().decode(decrypt(v.tee_private_key_hex, ct));

if (pt !== v.plaintext) {
  console.log('5b PY->JS: FAIL');
  console.log('expected:', v.plaintext);
  console.log('got     :', pt);
  process.exit(1);
}
console.log('5b PY->JS: PASS');
console.log('decrypted:', pt);
