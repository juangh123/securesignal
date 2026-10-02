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
 *   EXPECT_ATTESTATION_MODE=aws-nitro-enclaves REQUIRE_REAL_TEE=1 node tools/ops-status.mjs
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
const { createPublicClient, http, defineChain, keccak256 } = viem

const FRONTEND = (process.env.FRONTEND_URL || 'https://securesignal.vercel.app').replace(/\/+$/, '')
const TEE = (process.env.TEE_URL || 'https://securesignal-tee.onrender.com').replace(/\/+$/, '')
const RPC = process.env.RPC_URL || 'https://coston2-api.flare.network/ext/C/rpc'
const EXPECT_ATTESTATION_MODE = (process.env.EXPECT_ATTESTATION_MODE || '').trim()
const REQUIRE_REAL_TEE = process.env.REQUIRE_REAL_TEE === '1'

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

let assets
try {
  const r = await getJson(TEE + '/assets')
  assets = r.body
  const symbols = assets?.symbols
  const valid =
    r.ok &&
    Array.isArray(symbols) &&
    symbols.length > 0 &&
    assets.count === symbols.length &&
    new Set(symbols).size === symbols.length &&
    symbols.every((s) => typeof s === 'string' && s === s.toUpperCase()) &&
    ['BTC', 'ETH', 'FLR'].every((s) => symbols.includes(s)) &&
    (!health || assets.price_source === health.price_mode)
  check('[4] TEE /assets capability contract', valid, r.ok ? `${assets?.count ?? '?'} symbols` : `HTTP ${r.status}`)
} catch (e) {
  check('[4] TEE /assets capability contract', false, String(e.message || e))
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
  check('[5] registry readable on-chain', true, `nextTaskId=${next}`)
} catch (e) {
  check('[5] registry readable on-chain', false, String(e.shortMessage || e.message || e))
}

if (svc && onchain) {
  check('[6] service pubkey == on-chain activeTeePublicKey',
    strip0x(svc.public_key).toLowerCase() === strip0x(onchain.pub).toLowerCase(),
    `${strip0x(svc.public_key).slice(0, 16)}...`)
  check('[7] service address == on-chain teeAddress',
    String(svc.address).toLowerCase() === String(onchain.teeAddress).toLowerCase(),
    String(onchain.teeAddress))
}
if (health && onchain) {
  check('[8] health.registry_address == configured registry',
    String(health.registry_address).toLowerCase() === String(REGISTRY).toLowerCase(),
    String(health.registry_address))
}

if (health) {
  info('[9] relayer configured', String(health.relayer_configured))
  info('[9] llm configured', health.llm_configured ? `true (${health.llm_model ?? 'model?'})` : 'false')
  if (health.llm_configured) {
    info('[9] llm per-attempt timeout', `${health.llm_timeout_seconds ?? '?'}s`)
    info('[9] llm total budget', `${health.llm_total_budget_seconds ?? '?'}s`)
  }
  info('[9] price mode', String(health.price_mode))
  info('[9] attestation mode', String(health.attestation_mode))
  info('[9] service version', String(health.version))
  info('[9] analyze on-chain gate', String(health.analyze_requires_onchain_task))
  info('[9] image digest (legacy)', String(health.image_digest))
  info('[9] attestation measurement type', String(health.attestation_measurement_type))
  info(
    '[9] attestation measurement',
    String(health.attestation_measurement ?? health.image_digest ?? '').slice(0, 24) + '...',
  )
}

if (health && EXPECT_ATTESTATION_MODE) {
  check(
    '[11] attestation mode matches expectation',
    health.attestation_mode === EXPECT_ATTESTATION_MODE,
    `expected=${EXPECT_ATTESTATION_MODE} actual=${health.attestation_mode}`,
  )
}
if (health && REQUIRE_REAL_TEE) {
  const realModes = ['aws-nitro-enclaves', 'gcp-confidential-space']
  check(
    '[12] real TEE attestation required',
    realModes.includes(health.attestation_mode),
    `mode=${health.attestation_mode}`,
  )
}
if (health && health.attestation_mode === 'aws-nitro-enclaves') {
  const measurement = String(health.attestation_measurement ?? health.image_digest ?? '').toLowerCase()
  check(
    '[13] attestation measurement is a PCR0',
    /^[0-9a-f]{96}$/.test(measurement),
    measurement ? measurement.slice(0, 24) + '...' : 'missing',
  )
  let recordedPcr0 = null
  try {
    const readme = readFileSync(join(__dir, '..', 'deploy', 'aws', 'README.md'), 'utf-8')
    const match = readme.match(/\|\s*PCR0\s*\|\s*`?([0-9a-fA-F]{96})`?\s*\|/)
    recordedPcr0 = match ? match[1].toLowerCase() : null
  } catch {
    recordedPcr0 = null
  }
  if (recordedPcr0) {
    check(
      '[14] PCR0 matches deploy/aws/README.md',
      measurement === recordedPcr0,
      `recorded=${recordedPcr0.slice(0, 24)}...`,
    )
  } else {
    info('[14] PCR0 deployment record', 'not found in deploy/aws/README.md')
  }
  if (onchain?.digest) {
    const digestOfPcr0 = keccak256('0x' + measurement)
    check(
      '[16] on-chain expectedImageDigest == keccak256(PCR0)',
      String(onchain.digest).toLowerCase() === digestOfPcr0.toLowerCase(),
      `chain=${String(onchain.digest).slice(0, 24)}...`,
    )
  }
}

if (health?.llm_configured && Number.isFinite(Number(health.llm_total_budget_seconds))) {
  check(
    '[15] LLM budget leaves headroom under CloudFront 60s origin timeout',
    Number(health.llm_total_budget_seconds) <= 45,
    `${health.llm_total_budget_seconds}s`,
  )
}

if (onchain && Number(onchain.next) > 0) {
  const id = Number(onchain.next) - 1
  const t = await publicClient.readContract({ address: REGISTRY, abi: ABI, functionName: 'tasks', args: [BigInt(id)] })
  const status = ['None', 'Requested', 'Completed', 'Verified'][Number(t[5] ?? t.status)]
  const at = Number(t[4] ?? t.completedAt)
  info('[10] last task', `#${id} status=${status} completed=${at ? new Date(at * 1000).toISOString() : '-'}`)
}

const failed = results.filter((r) => !r.ok)
console.log(`\n===== ${results.length - failed.length}/${results.length} checks passed =====`)
if (failed.length) { for (const f of failed) console.log('FAILED:', f.label, '|', f.evidence); process.exitCode = 1 }
