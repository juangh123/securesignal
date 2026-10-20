#!/usr/bin/env node
/**
 * Flare submission release gate. Read-only: no transactions and no file writes.
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const env = process.env
const failures = []
const warnings = []

function pass(label, evidence = '') {
  console.log(`PASS  ${label}${evidence ? ` | ${evidence}` : ''}`)
}

function fail(label, evidence = '') {
  failures.push({ label, evidence })
  console.log(`FAIL  ${label}${evidence ? ` | ${evidence}` : ''}`)
}

function warn(label, evidence = '') {
  warnings.push({ label, evidence })
  console.log(`WARN  ${label}${evidence ? ` | ${evidence}` : ''}`)
}

function readJson(relativePath) {
  return JSON.parse(readFileSync(join(root, relativePath), 'utf8'))
}

const requiredFiles = [
  'README.md',
  'SUBMISSION.md',
  'LICENSE',
  'deliverables/aws-nitro-attestation-22.json',
  'deliverables/aws-nitro-attestation-22-verification.json',
  'deliverables/coston2-smoke-task-19.json',
  'deliverables/ftso-live-check-2026-10-02.json',
  'video/dist/SecureSignal_demo_1080p_v3.mp4',
]

for (const relativePath of requiredFiles) {
  if (existsSync(join(root, relativePath))) pass(`required file: ${relativePath}`)
  else fail(`required file: ${relativePath}`)
}

try {
  const submission = readFileSync(join(root, 'SUBMISSION.md'), 'utf8')
  const markers = ['Flare Summer Signal', 'Bounty 2', '47690']
  const missing = markers.filter((marker) => !submission.includes(marker))
  if (missing.length === 0) pass('submission narrative is scoped to Flare')
  else fail('submission narrative is scoped to Flare', missing.join(', '))
} catch (error) {
  fail('submission narrative is readable', String(error.message || error))
}

try {
  const frontendAddresses = readJson('frontend/src/config/contract-addresses.json')
  const serviceAddresses = readJson('tee-service/config/contract-addresses.json')
  const frontendAbi = readJson('frontend/src/config/AnalysisRegistry.json')
  const serviceAbi = readJson('tee-service/config/AnalysisRegistry.json')
  const sameAddresses = JSON.stringify(frontendAddresses) === JSON.stringify(serviceAddresses)
  const sameAbi = JSON.stringify(frontendAbi) === JSON.stringify(serviceAbi)
  if (sameAddresses) pass('shared contract addresses are synchronized')
  else fail('shared contract addresses are synchronized')
  if (sameAbi) pass('shared contract ABI copies are synchronized')
  else fail('shared contract ABI copies are synchronized')
} catch (error) {
  fail('contract configuration is readable', String(error.message || error))
}

try {
  const verification = readJson('deliverables/aws-nitro-attestation-22-verification.json')
  if (verification.verified === true && verification.onchain_status === 3) {
    pass('archived Nitro verification is valid', `task=${verification.task_id}`)
  } else {
    fail('archived Nitro verification is valid')
  }
} catch (error) {
  fail('archived Nitro verification is readable', String(error.message || error))
}

try {
  const status = spawnSync('git', ['status', '--porcelain'], {
    cwd: root,
    encoding: 'utf8',
  })
  if (status.status !== 0) {
    warn('git working tree check unavailable', status.stderr.trim())
  } else if (!status.stdout.trim()) {
    pass('git working tree is clean')
  } else if (env.ALLOW_DIRTY === '1') {
    warn('git working tree has local changes', 'ALLOW_DIRTY=1')
  } else {
    fail('git working tree is clean', 'set ALLOW_DIRTY=1 for a pre-commit check')
  }
} catch (error) {
  warn('git working tree check unavailable', String(error.message || error))
}

const frontendUrl = env.FRONTEND_URL || 'https://securesignal.vercel.app'
try {
  const response = await fetch(frontendUrl, { signal: AbortSignal.timeout(60_000) })
  const html = await response.text()
  if (response.ok && html.includes('SecureSignal')) {
    pass('public frontend identifies SecureSignal', `HTTP ${response.status}`)
  } else {
    fail('public frontend identifies SecureSignal', `HTTP ${response.status}`)
  }
} catch (error) {
  fail('public frontend is reachable', String(error.message || error))
}

const liveCheck = spawnSync(process.execPath, [join(root, 'tools', 'ops-status.mjs')], {
  cwd: root,
  encoding: 'utf8',
  env: {
    ...env,
    TEE_URL: env.TEE_URL || 'https://d1tubqcwiwwev5.cloudfront.net',
    REQUIRE_REAL_TEE: '1',
    EXPECT_ATTESTATION_MODE: 'aws-nitro-enclaves',
    RELAYER_ADDRESS: env.RELAYER_ADDRESS || '0x0A3452C5B96396F186bD2d7ed793F8701A88fF72',
  },
})
process.stdout.write(liveCheck.stdout || '')
process.stderr.write(liveCheck.stderr || '')
if (liveCheck.status === 0) pass('live Flare and on-chain checks')
else fail('live Flare and on-chain checks', `exit=${liveCheck.status}`)

try {
  const response = await fetch(
    'https://www.youtube.com/oembed?url=https://youtu.be/1V5yuxIENvc&format=json',
    { signal: AbortSignal.timeout(45_000) },
  )
  const metadata = response.ok ? await response.json() : null
  if (metadata?.title?.includes('SecureSignal')) pass('YouTube demo metadata')
  else fail('YouTube demo metadata', `HTTP ${response.status}`)
} catch (error) {
  fail('YouTube demo metadata', String(error.message || error))
}

const videoUrl =
  'https://github.com/juangh123/securesignal/raw/main/video/dist/SecureSignal_demo_1080p_v3.mp4'
try {
  const response = await fetch(videoUrl, {
    method: 'HEAD',
    redirect: 'follow',
    signal: AbortSignal.timeout(60_000),
  })
  if (response.ok) pass('GitHub video fallback', `HTTP ${response.status}`)
  else fail('GitHub video fallback', `HTTP ${response.status}`)
} catch (error) {
  fail('GitHub video fallback', String(error.message || error))
}

console.log(
  `\n===== Flare release gate: ${failures.length === 0 ? 'PASS' : 'FAIL'} ` +
    `(${failures.length} failures, ${warnings.length} warnings) =====`,
)
if (failures.length > 0) {
  for (const item of failures) console.log(`FAILED: ${item.label} | ${item.evidence}`)
  process.exitCode = 1
}
