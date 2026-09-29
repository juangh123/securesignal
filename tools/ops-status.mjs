#!/usr/bin/env node
/**
 * SecureSignal - live operational status check (read-only).
 *
 * Reports the state of the three deployed components and cross-checks the
 * TEE service against the on-chain registry. Performs no transactions and
 * mutates no state.
 *
 * Usage:
 *   node tools/ops-status.mjs
 *   FRONTEND_URL=... TEE_URL=... RPC_URL=... node tools/ops-status.mjs
 *
 * Requires frontend dependencies (viem) installed:
 *   npm --prefix frontend install
 */
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dir = dirname(fileURLToPath(import.meta.url))
const require = createRequire(join(__dir, '..', 'frontend', 'package.json'))
let viem
try {
  viem = require('viem')
} catch {
  console.error('viem not found - run: npm --prefix frontend install')
  process.exit(2)
}
const { createPublicClient, http, defineChain } = viem

const FRONTEND = (process.env.FRONTEND_URL || 'https://securesignal.vercel.app').replace(/\/+$/, '')
const TEE = (process.env.TEE_URL || 'https://securesignal-tee.onrender.com').replace(/\/+$/, '')
const RPC = process.env.RPC_URL || 'https://coston2-api.flare.network/ext/C/rpc'

const addresses = JSON.parse(readFileSync(join(__dir, '..', 'frontend', 'src', 'config', 'contract-addresses.json'), 'utf-8'))
const artifact = JSON.parse(readFileSync(join(__dir, '..', 'tee-service', 'config', 'AnalysisRegistry.json'), 'utf-8'))
const REGISTRY = addresses.AnalysisRegistry
const ABI = artifact.abi

const chain = defineChain({
  id: 114,
  name: 'Flare Testnet Coston2',
  nativeCurrency: { name: 'Coston2 Flare', symbol: 'C2FLR', decimals: 18 },
  rpcUrls: { default: { http: [RPC] } },
})
const publicClient = createPublicClient({ chain, transport: http(RPC) })

const strip0x = (h) => (typeof h === 'string' && h.startsWith('0x') ? h.slice(2) : h)
const results = []
function check(label, ok, evidence = '') {
  results.push({ label, ok, evidence })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${evidence ? '  | ' + evidence : ''}`)
}
function info(label, value) {
  console.log(`INFO  ${label}  | ${value}`)
}
async function getJson(url) {
  const r = await fetch(url, { signal: AbortSignal.timeout(90000) })
  return { status: r.status, ok: r.ok, body: await r.json().catch(() => null) }
}

console.log(`Frontend : ${FRONTEND}`)
console.log(`TEE      : ${TEE}`)
console.log(`Registry : ${REGISTRY} (${addresses.network})\n`)

try {
  const r = await fetch(FRONTEND, { signal: AbortSignal.timeout(60000) })
  const html = await r.text()
  check('[1] frontend reachable', r.ok, `HTTP ${r.status}`)
  check('[1b] frontend identifies app', html.includes('SecureSignal'), 'title/metadata present')
} catch (e) {
  check('[1] frontend reachable', false, String(e.message || e))
}

let svc
try {
  const r = await getJson(TEE + '/public-key')
  svc = r.body
  check('[2] TEE /public-key', r.ok && !!svc?.public_key, `HTTP ${r.status}`)
} catch (e) {
  check('[2] TEE /public-key', false, String(e.message || e))
}

let health
try {
  const r = await getJson(TEE + '/health')
  if (r.status === 404) info('[3] TEE /health', 'not deployed (optional)')
  else { health = r.body; check('[3] TEE /health', r.ok && health?.status === 'ok', `HTTP ${r.status}`) }
} catch (e) {
  info('[3] TEE /health', String(e.message || e))
}

let onchain
try {
  const [owner, teeAddress, pub, digest, next] = await Promise.all([
    publicClient.readContract({ address: REGISTRY, abi: ABI, functionName: 'owner' }),
    publicClient.readContract({ address: REGISTRY, abi: ABI, functionName: 'teeAddress' }),
    publicClient.readContract({ address: REGISTRY, abi: ABI, functionName: 'activeTeePublicKey' }),
    publicClient.readContract({ address: REGISTRY, abi: ABI, functionName: 'expectedImageDigest' }),
    publicClient.readContract({ address: REGISTRY, abi: ABI, functionName: 'nextTaskId' }),
  ])
  onchain = { owner, teeAddress, pub, digest, next }
  check('[4] registry readable on-chain', true, `nextTaskId=${next}`)
} catch (e) {
  check('[4] registry readable on-chain', false, String(e.shortMessage || e.message || e))
}

if (svc && onchain) {
  check('[5] service pubkey == on-chain activeTeePublicKey',
    strip0x(svc.public_key).toLowerCase() === strip0x(onchain.pub).toLowerCase(),
    `${strip0x(svc.public_key).slice(0, 16)}...`)
  check('[6] service address == on-chain teeAddress',
    String(svc.address).toLowerCase() === String(onchain.teeAddress).toLowerCase(),
    String(onchain.teeAddress))
}
if (health && onchain) {
  check('[7] health.registry_address == configured registry',
    String(health.registry_address).toLowerCase() === String(REGISTRY).toLowerCase(),
    String(health.registry_address))
}

if (health) {
  info('[8] relayer configured', String(health.relayer_configured))
  info('[8] llm configured', health.llm_configured ? `true (${health.llm_model ?? 'model?'})` : 'false')
  info('[8] price mode', String(health.price_mode))
  info('[8] attestation mode', String(health.attestation_mode))
  info('[8] image digest', String(health.image_digest))
}

if (onchain && Number(onchain.next) > 0) {
  const id = Number(onchain.next) - 1
  const t = await publicClient.readContract({ address: REGISTRY, abi: ABI, functionName: 'tasks', args: [BigInt(id)] })
  const status = ['None', 'Requested', 'Completed', 'Verified'][Number(t[5] ?? t.status)]
  const at = Number(t[4] ?? t.completedAt)
  info('[9] last task', `#${id} status=${status} completed=${at ? new Date(at * 1000).toISOString() : '-'}`)
}

const failed = results.filter((r) => !r.ok)
console.log(`\n===== ${results.length - failed.length}/${results.length} checks passed =====`)
if (failed.length) { for (const f of failed) console.log('FAILED:', f.label, '|', f.evidence); process.exitCode = 1 }
