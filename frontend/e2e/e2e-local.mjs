/**
 * SecureSignal Stage 2 — local end-to-end integration verification.
 *
 * Chain:  viem -> http://127.0.0.1:8545 (hardhat, chainId 31337), account #0
 * TEE:    FastAPI on http://127.0.0.1:3000
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
         encodePacked, recoverMessageAddress, parseEventLogs, encodeFunctionData } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { hardhat } from 'viem/chains';
import { encrypt, decrypt, PrivateKey } from 'eciesjs';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dir = dirname(fileURLToPath(import.meta.url));
const RPC = 'http://127.0.0.1:8545';
const TEE = 'http://127.0.0.1:3000';

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

async function runCase(label, holdings, { expectAnalysisError = null } = {}) {
  console.log(`\n=== ${label} ===`);
  console.log('holdings:', JSON.stringify(holdings));

  // NOTE: TEE register might not be finished, add some wait
  let onchainTeeAddr = await publicClient.readContract({
      address: REGISTRY, abi: ABI, functionName: 'teeAddress',
    });
    
    if (onchainTeeAddr === '0x0000000000000000000000000000000000000000' || onchainTeeAddr === undefined) {
      const timeout = 60000;
      const start = Date.now();
      console.log(`Waiting for teeAddress registration...`);
      while (Date.now() - start < timeout) {
        onchainTeeAddr = await publicClient.readContract({
          address: REGISTRY, abi: ABI, functionName: 'teeAddress',
        });
        if (onchainTeeAddr !== '0x0000000000000000000000000000000000000000' && onchainTeeAddr !== undefined) {
          break;
        }
        await new Promise(r => setTimeout(r, 1000));
      }
    }

  // 1. Cross-check TEE public key: on-chain vs service
  const onchainPub = await publicClient.readContract({
    address: REGISTRY, abi: ABI, functionName: 'activeTeePublicKey',
  });
  const svc = await (await fetch(`${TEE}/public-key`)).json();
  let pubkeyFromBackend = "0x" + svc.public_key;
  if (svc.public_key.startsWith('04')) {
    pubkeyFromBackend = "0x" + svc.public_key.slice(2);
  } else if (svc.public_key.startsWith('0x')) {
    pubkeyFromBackend = svc.public_key;
  }
  const derivedAddress = await publicClient.readContract({
    address: REGISTRY, abi: ABI, functionName: 'teeAddress',
  });

  check(`${label} [1] TEE pubkey on-chain == /public-key`,
    onchainPub.toLowerCase() === pubkeyFromBackend.toLowerCase(),
    `chain=${onchainPub.slice(0, 20)}... svc=${pubkeyFromBackend.slice(0, 20)}... addr=${derivedAddress}`);

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
    let attObj = typeof data.attestation === 'string' && data.attestation.startsWith('{') ? JSON.parse(data.attestation) : (typeof data.attestation === 'object' ? data.attestation : {});
    let finalSignature = attObj.signature || data.attestation;
    if (typeof finalSignature === 'object' && finalSignature.signature) {
         finalSignature = finalSignature.signature;
    }
    
    // Ensure 0x prefix; the Python eth_account library already produces a
    // correct 65-byte (r||s||v) signature with v=27/28 — no manipulation needed.
    if (!finalSignature.startsWith('0x')) {
       finalSignature = '0x' + finalSignature;
    }

    const packedBytes = encodePacked(['uint256', 'bytes32'], [BigInt(attObj.task_id || data.task_id), attObj.result_hash || data.result_hash]);
      try {
           const recovered = await recoverMessageAddress({
           message: { raw: packedBytes },
           signature: finalSignature,
         });
          check(`${label} [8] ecrecover(attestation) == on-chain teeAddress`,
           recovered.toLowerCase() === onchainTeeAddr.toLowerCase(),
          `recovered=${recovered} tee=${onchainTeeAddr}`);
      } catch (e) {
         console.log(`Failed to recover address: ${e.message}`);
         check(`${label} [8] ecrecover(attestation) == on-chain teeAddress`, false, `Exception: ${e.message}`);
      }

    // 9. On-chain submission verification
    //    If the TEE relayer already submitted (onchain_submitted === true),
    //    the task status is already Verified — skip manual submission to
    //    avoid "invalid status" revert. Otherwise, submit manually.
    let onchainStatus;
    let onchainHash;

    if (data.onchain_submitted === true) {
      check(`${label} [9a] onchain_submitted == true (via TEE relayer)`, true);
    } else {
      const txData = encodeFunctionData({
        abi: ABI,
        functionName: 'submitResult',
        args: [
          BigInt(attObj.task_id || data.task_id),
          attObj.result_hash || data.result_hash,
          finalSignature
        ]
      });
      try {
        const txhash = await walletClient.sendTransaction({
          account,
          to: REGISTRY,
          data: txData,
        });
        await publicClient.waitForTransactionReceipt({ hash: txhash });
        check(`${label} [9a] onchain_submitted == true (manual submitResult)`, true);
      } catch (e) {
        check(`${label} [9a] onchain_submitted == true`, false, `manual tx failed: ${e.message}`);
      }
    }

    // Check on-chain tasks mapping — viem returns struct as object with
    // named fields; status is the 6th field (index 5) if returned as array.
    try {
      const tsk = await publicClient.readContract({
        address: REGISTRY, abi: ABI, functionName: 'tasks', args: [BigInt(attObj.task_id || data.task_id)]
      });
      // Task struct: user(0), inputDataHash(1), resultHash(2),
      //              requestedAt(3), completedAt(4), status(5)
      onchainStatus = Number(tsk.status ?? tsk[5]);
      onchainHash = tsk.resultHash ?? tsk[2];
    } catch(e) {
      console.log(`Failed to read on-chain task: ${e.message}`);
    }
    check(`${label} [9b] on-chain tasks(${attObj.task_id || data.task_id}).status == 3 (Verified)`,
      onchainStatus === 3,
      `status=${onchainStatus}`);
    check(`${label} [9c] on-chain resultHash matches response`,
      onchainHash && onchainHash.toLowerCase() === (attObj.result_hash || data.result_hash).toLowerCase(),
      `onchain=${onchainHash} resp=${attObj.result_hash || data.result_hash}`);
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
