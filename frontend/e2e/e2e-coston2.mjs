/**
 * SecureSignal — Coston2 production smoke test (real testnet).
 *
 * Chain:  viem -> https://coston2-api.flare.network/ext/C/rpc (chainId 114)
 * TEE:    local FastAPI on http://127.0.0.1:8000, started with production env
 *         (TEE_PRIVATE_KEY registered on-chain, relayer = deployer key,
 *          ANALYSIS_OFFLINE unset -> REAL FTSO prices from Coston2 FtsoV2)
 *
 * Account: deployer key read from contracts/.env (PRIVATE_KEY=0x...).
 *
 * Assertions mirror e2e-local.mjs except:
 *   [6] expects price_source == "coston2-ftso" and live prices > 0.
 * Spends real (testnet) gas: 1 requestAnalysis tx + relayer submitResult tx.
 */

import { createPublicClient, createWalletClient, http, keccak256, stringToBytes,
         encodePacked, recoverMessageAddress, parseEventLogs, defineChain } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { encrypt, decrypt, PrivateKey } from 'eciesjs';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dir = dirname(fileURLToPath(import.meta.url));
const RPC = 'https://coston2-api.flare.network/ext/C/rpc';
const TEE = 'http://127.0.0.1:8000';

const coston2 = defineChain({
  id: 114,
  name: 'Flare Testnet Coston2',
  nativeCurrency: { name: 'Coston2 Flare', symbol: 'C2FLR', decimals: 18 },
  rpcUrls: { default: { http: [RPC] } },
});

// --- read deployer key from contracts/.env (never hardcode) ---
const envText = readFileSync(join(__dir, '..', '..', 'contracts', '.env'), 'utf-8');
const pkLine = envText.split('\n').find((l) => l.startsWith('PRIVATE_KEY='));
if (!pkLine) throw new Error('PRIVATE_KEY not found in contracts/.env');
const DEPLOYER_PK = pkLine.split('=')[1].trim();

const addresses = JSON.parse(readFileSync(join(__dir, '..', 'src', 'config', 'contract-addresses.json'), 'utf-8'));
const artifact = JSON.parse(readFileSync(join(__dir, '..', '..', 'tee-service', 'config', 'AnalysisRegistry.json'), 'utf-8'));
const ABI = artifact.abi;
const REGISTRY = addresses.AnalysisRegistry;
if (addresses.network !== 'coston2') throw new Error(`expected network=coston2, got ${addresses.network}`);

const account = privateKeyToAccount(DEPLOYER_PK);
const publicClient = createPublicClient({ chain: coston2, transport: http(RPC) });
const walletClient = createWalletClient({ account, chain: coston2, transport: http(RPC) });

const results = [];
function check(label, ok, evidence = '') {
  results.push({ label, ok, evidence });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${evidence ? '  | ' + evidence : ''}`);
  if (!ok) process.exitCode = 1;
}

const b64encode = (u8) => Buffer.from(u8).toString('base64');
const b64decode = (b64) => new Uint8Array(Buffer.from(b64, 'base64'));
const strip0x = (h) => (h.startsWith('0x') ? h.slice(2) : h);

console.log('Registry:', REGISTRY, '| network:', addresses.network, '| account:', account.address);

const holdings = { BTC: 0.5, ETH: 2, FLR: 10000 };
// const label = 'Coston2 live case';

// 1. Cross-check TEE public key: on-chain vs service
const onchainPub = await publicClient.readContract({
  address: REGISTRY, abi: ABI, functionName: 'activeTeePublicKey',
});
const svc = await (await fetch(`${TEE}/public-key`)).json();
check('[1] TEE pubkey on-chain == /public-key',
  strip0x(onchainPub).toLowerCase() === svc.public_key.toLowerCase(),
  `chain=${strip0x(onchainPub).slice(0, 20)}... svc=${svc.public_key.slice(0, 20)}...`);

const onchainTeeAddr = await publicClient.readContract({
  address: REGISTRY, abi: ABI, functionName: 'teeAddress',
});

// 2. Session keypair
const sessionSK = new PrivateKey();
const sessionSkHex = sessionSK.toHex();
const sessionPkHex = sessionSK.publicKey.toHex(false);

// 3. Encrypt input -> hash -> requestAnalysis -> taskId from event
const plaintext = JSON.stringify({ client_pubkey: sessionPkHex, holdings, risk_profile: 'moderate' });
const ciphertext = encrypt(svc.public_key, stringToBytes(plaintext));
const inputDataHash = keccak256(ciphertext);

const txHash = await walletClient.writeContract({
  address: REGISTRY, abi: ABI, functionName: 'requestAnalysis', args: [inputDataHash],
});
console.log('  requestAnalysis tx:', txHash);
const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
const events = parseEventLogs({ abi: ABI, logs: receipt.logs });
const ev = events.find((e) => e.eventName === 'AnalysisRequested');
const taskId = ev.args.taskId;
check('[3] requestAnalysis mined on Coston2, taskId from event',
  ev !== undefined && typeof taskId === 'bigint', `tx=${txHash} taskId=${taskId}`);

// 4. POST /analyze
const resp = await fetch(`${TEE}/analyze`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ task_id: Number(taskId), encrypted_data: b64encode(ciphertext) }),
});
if (!resp.ok) {
  check('[4] POST /analyze 200', false, `HTTP ${resp.status}: ${await resp.text()}`);
  throw new Error('aborting');
}
const data = await resp.json();
check('[4] POST /analyze 200, fields present',
  !!(data.encrypted_result && data.attestation && data.result_hash),
  `task_id=${data.task_id} onchain_submitted=${data.onchain_submitted}`);

// 5. Decrypt result with session SK
const resultJson = new TextDecoder().decode(decrypt(sessionSkHex, b64decode(data.encrypted_result)));
check('[5] encrypted_result decrypts with session SK', true);
console.log('  result_json:', resultJson);
const result = JSON.parse(resultJson);

// 6. LIVE price assertions
const holdingsSeen = Object.keys(holdings).every((s) => resultJson.includes(s));
check('[6a] plaintext holdings appear in analysis', holdingsSeen);
check('[6b] price_source == "coston2-ftso" (REAL FTSO prices)',
  result.price_source === 'coston2-ftso', `price_source=${result.price_source}`);
const pricesOk = result.prices_used && ['BTC', 'ETH', 'FLR'].every((s) => result.prices_used[s] > 0);
check('[6c] live prices present and > 0', !!pricesOk, JSON.stringify(result.prices_used));

// 7. result_hash integrity
const recomputed = keccak256(stringToBytes(resultJson));
check('[7] keccak256(result_json) == response result_hash',
  recomputed.toLowerCase() === data.result_hash.toLowerCase(),
  `recomputed=${recomputed.slice(0, 18)}... resp=${data.result_hash.slice(0, 18)}...`);

// 8. Attestation ecrecover == teeAddress (EIP-191 "\n64" over packed taskId||resultHash)
const att = JSON.parse(data.attestation);
const packed = encodePacked(['uint256', 'bytes32'], [BigInt(data.task_id), data.result_hash]);
const recovered = await recoverMessageAddress({ message: { raw: packed }, signature: att.signature });
check('[8] ecrecover(attestation) == on-chain teeAddress',
  recovered.toLowerCase() === onchainTeeAddr.toLowerCase(),
  `recovered=${recovered} tee=${onchainTeeAddr}`);

// 9. On-chain submission + task state (wait for relayer tx to mine)
check('[9a] onchain_submitted == true', data.onchain_submitted === true);
await new Promise((r) => setTimeout(r, 8000)); // relayer submits async; wait for mining
const task = await publicClient.readContract({
  address: REGISTRY, abi: ABI, functionName: 'tasks', args: [BigInt(data.task_id)],
});
const status = Number(task.status ?? task[5]);
const chainResultHash = task.resultHash ?? task[2];
check('[9b] on-chain tasks().status == 3 (Verified) on Coston2', status === 3, `status=${status}`);
check('[9c] on-chain resultHash == response result_hash',
  chainResultHash.toLowerCase() === data.result_hash.toLowerCase(),
  `chain=${chainResultHash.slice(0, 18)}...`);

const passed = results.filter((r) => r.ok).length;
console.log(`\n===== COSTON2 SMOKE TEST: ${passed}/${results.length} checks passed =====`);
for (const r of results.filter((r) => !r.ok)) console.log('FAILED:', r.label, '|', r.evidence);
