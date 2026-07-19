/**
 * SecureSignal Stage 2 — local end-to-end integration verification.
 *
 * Chain:  viem -> http://127.0.0.1:8545 (hardhat, chainId 31337), account #0
 * TEE:    FastAPI on http://127.0.0.1:8000
 *
 * Flow per case:
 *  1. Read on-chain activeTeePublicKey, cross-check with GET /public-key
 *  2. Generate session keypair (eciesjs)
 *  3. Encrypt input -> keccak256(ciphertext) -> requestAnalysis(inputDataHash)
 *     -> parse taskId from AnalysisRequested event
 *  4. POST /analyze { task_id, encrypted_data }
 *  5. Decrypt encrypted_result with session SK
 *  6. Assert analysis content / price_source
 *  7. Recompute keccak256(result_json) == response result_hash
 *  8. EIP-191 ecrecover(attestation signature) == registered teeAddress
 *  9. Assert onchain_submitted == true; on-chain tasks(taskId).status == 3
 *     (Verified) and resultHash matches.
 *
 * Case A uses holdings {BTC, ETH, DOGE} — DOGE is NOT in the provider's
 * FEED_IDS, so it exercises the explicit error path (no fake price);
 * Case B uses {BTC, ETH}.
 *
 * REGRESSION NOTE (Stage A): FLR is now supported by the price provider
 * (FEED_IDS/FIXTURE_PRICES), so the pre-Stage-A Case A assertion
 * "FLR unsupported -> error" was stale; the unsupported-asset case now
 * uses DOGE to preserve the same honest-error coverage.
 */

import { createPublicClient, createWalletClient, http, keccak256, stringToBytes,
         encodePacked, recoverMessageAddress, parseEventLogs } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { hardhat } from 'viem/chains';
import { encrypt, decrypt, PrivateKey } from 'eciesjs';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dir = dirname(fileURLToPath(import.meta.url));
const RPC = 'http://127.0.0.1:8545';
const TEE = 'http://127.0.0.1:8000';

// hardhat account #0 (deployer / task requester)
const ACCOUNT0_PK = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';

const addresses = JSON.parse(readFileSync(join(__dir, '..', 'src', 'config', 'contract-addresses.json'), 'utf-8'));
const artifact = JSON.parse(readFileSync(join(__dir, '..', '..', 'tee-service', 'config', 'AnalysisRegistry.json'), 'utf-8'));
const ABI = artifact.abi;
const REGISTRY = addresses.AnalysisRegistry;

const account = privateKeyToAccount(ACCOUNT0_PK);
const publicClient = createPublicClient({ chain: hardhat, transport: http(RPC) });
const walletClient = createWalletClient({ account, chain: hardhat, transport: http(RPC) });

const results = [];
function check(label, ok, evidence = '') {
  results.push({ label, ok, evidence });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${evidence ? '  | ' + evidence : ''}`);
  if (!ok) process.exitCode = 1;
}

const b64encode = (u8) => Buffer.from(u8).toString('base64');
const b64decode = (b64) => new Uint8Array(Buffer.from(b64, 'base64'));
const strip0x = (h) => (h.startsWith('0x') ? h.slice(2) : h);

async function runCase(label, holdings, { expectAnalysisError = null } = {}) {
  console.log(`\n=== ${label} ===`);
  console.log('holdings:', JSON.stringify(holdings));

  // 1. Cross-check TEE public key: on-chain vs service
  const onchainPub = await publicClient.readContract({
    address: REGISTRY, abi: ABI, functionName: 'activeTeePublicKey',
  });
  const svc = await (await fetch(`${TEE}/public-key`)).json();
  check(`${label} [1] TEE pubkey on-chain == /public-key`,
    strip0x(onchainPub).toLowerCase() === svc.public_key.toLowerCase(),
    `chain=${strip0x(onchainPub).slice(0, 20)}... svc=${svc.public_key.slice(0, 20)}...`);

  const onchainTeeAddr = await publicClient.readContract({
    address: REGISTRY, abi: ABI, functionName: 'teeAddress',
  });

  // 2. Session keypair
  const sessionSK = new PrivateKey();
  const sessionSkHex = sessionSK.toHex();
  const sessionPkHex = sessionSK.publicKey.toHex(false); // 65B uncompressed, 04 prefix

  // 3. Encrypt input -> hash -> requestAnalysis -> taskId from event
  const plaintextObj = { client_pubkey: sessionPkHex, holdings, risk_profile: 'moderate' };
  const plaintext = JSON.stringify(plaintextObj);
  const ciphertext = encrypt(svc.public_key, stringToBytes(plaintext));
  const inputDataHash = keccak256(ciphertext);

  const txHash = await walletClient.writeContract({
    address: REGISTRY, abi: ABI, functionName: 'requestAnalysis', args: [inputDataHash],
  });
  const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
  const events = parseEventLogs({ abi: ABI, logs: receipt.logs });
  const ev = events.find((e) => e.eventName === 'AnalysisRequested');
  const taskId = ev.args.taskId;
  check(`${label} [3] requestAnalysis mined, taskId from event`,
    ev !== undefined && typeof taskId === 'bigint',
    `tx=${txHash.slice(0, 18)}... taskId=${taskId}`);

  // 4. POST /analyze
  const resp = await fetch(`${TEE}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: Number(taskId), encrypted_data: b64encode(ciphertext) }),
  });
  if (!resp.ok) {
    check(`${label} [4] POST /analyze 200`, false, `HTTP ${resp.status}: ${await resp.text()}`);
    return;
  }
  const data = await resp.json();
  check(`${label} [4] POST /analyze 200, fields present`,
    !!(data.encrypted_result && data.attestation && data.result_hash),
    `task_id=${data.task_id} onchain_submitted=${data.onchain_submitted}`);

  // 5. Decrypt result with session SK
  let resultJson;
  try {
    resultJson = new TextDecoder().decode(decrypt(sessionSkHex, b64decode(data.encrypted_result)));
    check(`${label} [5] encrypted_result decrypts with session SK`, true);
  } catch (e) {
    check(`${label} [5] encrypted_result decrypts with session SK`, false, String(e));
    return;
  }
  console.log('  result_json:', resultJson);
  const result = JSON.parse(resultJson);

  // 6. Analysis content assertions
  if (expectAnalysisError) {
    check(`${label} [6] analysis surfaces explicit error for unsupported asset (no fake price)`,
      result.status === 'error' && typeof result.error === 'string' &&
      result.error.includes(expectAnalysisError) && !('price_source' in result),
      `error="${result.error}"`);
  } else {
    const holdingsSeen = Object.keys(holdings).every((s) => resultJson.includes(s));
    check(`${label} [6a] plaintext holdings appear in analysis`, holdingsSeen);
    check(`${label} [6b] price_source == "offline-fixture" (no unlabeled fake price)`,
      result.price_source === 'offline-fixture', `price_source=${result.price_source}`);
  }

  // 7. result_hash integrity
  const recomputed = keccak256(stringToBytes(resultJson));
  check(`${label} [7] keccak256(result_json) == response result_hash`,
    recomputed.toLowerCase() === data.result_hash.toLowerCase(),
    `recomputed=${recomputed.slice(0, 18)}... resp=${data.result_hash.slice(0, 18)}...`);

  // 8. Attestation ecrecover == teeAddress (EIP-191 personal_sign over
  //    raw 64-byte abi.encodePacked(uint256 taskId, bytes32 resultHash), prefix "\n64")
  const att = JSON.parse(data.attestation);
  const packed = encodePacked(['uint256', 'bytes32'], [BigInt(data.task_id), data.result_hash]);
  const recovered = await recoverMessageAddress({
    message: { raw: packed },
    signature: att.signature,
  });
  check(`${label} [8] ecrecover(attestation) == on-chain teeAddress`,
    recovered.toLowerCase() === onchainTeeAddr.toLowerCase(),
    `recovered=${recovered} tee=${onchainTeeAddr}`);
  check(`${label} [8b] attestation fields consistent (task_id, result_hash, tee_address, mode)`,
    att.task_id === data.task_id && att.result_hash.toLowerCase() === data.result_hash.toLowerCase() &&
    att.tee_address.toLowerCase() === onchainTeeAddr.toLowerCase() && att.mode === 'dev-simulated');

  // 9. On-chain submission + task state
  check(`${label} [9a] onchain_submitted == true`, data.onchain_submitted === true);
  const task = await publicClient.readContract({
    address: REGISTRY, abi: ABI, functionName: 'tasks', args: [BigInt(data.task_id)],
  });
  const status = Number(task.status ?? task[5]);
  const chainResultHash = task.resultHash ?? task[2];
  check(`${label} [9b] on-chain tasks(${data.task_id}).status == 3 (Verified)`,
    status === 3, `status=${status}`);
  check(`${label} [9c] on-chain resultHash == response result_hash`,
    chainResultHash.toLowerCase() === data.result_hash.toLowerCase(),
    `chain=${chainResultHash.slice(0, 18)}...`);
}

console.log('Registry:', REGISTRY, '| network:', addresses.network);

// Case A — unsupported asset (DOGE not in FEED_IDS) -> explicit error path
await runCase('Case A (unsupported asset DOGE -> explicit error)',
  { BTC: 0.5, ETH: 2, DOGE: 100 }, { expectAnalysisError: 'DOGE' });

// Case B — engine-supported holdings, full happy-path assertions
await runCase('Case B (BTC+ETH full happy path)',
  { BTC: 0.5, ETH: 2 });

const passed = results.filter((r) => r.ok).length;
console.log(`\n===== SUMMARY: ${passed}/${results.length} checks passed =====`);
for (const r of results.filter((r) => !r.ok)) console.log('FAILED:', r.label, '|', r.evidence);
