// Unit tests for the pure portfolio helpers, run with Node's built-in test
// runner and type stripping (no extra dev dependencies):
//   npm run test:unit

import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  MAX_AMOUNT,
  MAX_HOLDINGS,
  assertAnalysisResult,
  parseHoldings,
} from '../src/utils/portfolio.ts'

test('parseHoldings splits commas, semicolons and newlines', () => {
  assert.deepEqual(parseHoldings('2 BTC, 10 ETH; 5 SOL\n1 FLR'), {
    BTC: 2,
    ETH: 10,
    SOL: 5,
    FLR: 1,
  })
})

test('parseHoldings merges duplicate symbols case-insensitively', () => {
  assert.deepEqual(parseHoldings('1 btc, 2 BTC'), { BTC: 3 })
})

test('parseHoldings rejects unparseable entries', () => {
  assert.throws(() => parseHoldings('BTC'), /Cannot parse holding/)
  assert.throws(() => parseHoldings('2 B-TC'), /Cannot parse holding/)
})

test('parseHoldings enforces the TEE symbol limits', () => {
  assert.throws(() => parseHoldings('1 WAYTOOLONGSYMBOL'), /Invalid symbol/)
  assert.throws(() => parseHoldings('1 B TC'), /Cannot parse holding/)
})

test('parseHoldings rejects empty and non-positive amounts', () => {
  assert.throws(() => parseHoldings(''), /Enter at least one holding/)
  assert.throws(() => parseHoldings('0 BTC'), /Invalid amount/)
  assert.throws(() => parseHoldings('-1 BTC'), /Cannot parse holding/)
})

test('parseHoldings rejects amounts over the limit', () => {
  assert.throws(
    () => parseHoldings(`${MAX_AMOUNT * 2} BTC`),
    /too large/
  )
})

test('parseHoldings rejects too many distinct symbols', () => {
  const input = Array.from(
    { length: MAX_HOLDINGS + 1 },
    (_, index) => `1 S${index}`
  ).join(', ')

  assert.throws(() => parseHoldings(input), /Too many holdings/)
})

test('parseHoldings accepts exactly the maximum number of symbols', () => {
  const input = Array.from(
    { length: MAX_HOLDINGS },
    (_, index) => `1 S${index}`
  ).join(', ')

  assert.equal(Object.keys(parseHoldings(input)).length, MAX_HOLDINGS)
})

test('assertAnalysisResult accepts a valid success payload', () => {
  const payload = {
    status: 'success',
    analysis_mode: 'rule-fallback',
    price_source: 'offline-fixture',
    prices_used: { BTC: 65000 },
    total_value_usd: 65000,
    holdings: [],
    risk_score: 42,
    risk_level: 'medium',
    rebalance: [],
    summary: 'ok',
  }

  assert.deepEqual(assertAnalysisResult(payload), payload)
})

test('assertAnalysisResult accepts the minimal error payload', () => {
  const payload = { status: 'error', error: 'price provider failed' }
  assert.deepEqual(assertAnalysisResult(payload), payload)
})

test('assertAnalysisResult rejects malformed payloads', () => {
  assert.throws(() => assertAnalysisResult(null), /not a JSON object/)
  assert.throws(() => assertAnalysisResult([]), /not a JSON object/)
  assert.throws(() => assertAnalysisResult({ status: 'weird' }), /missing status/)
  assert.throws(
    () =>
      assertAnalysisResult({
        status: 'success',
        total_value_usd: '65000',
        risk_score: 42,
        holdings: [],
        rebalance: [],
      }),
    /not numbers/
  )
  assert.throws(
    () =>
      assertAnalysisResult({
        status: 'success',
        total_value_usd: 65000,
        risk_score: 42,
      }),
    /not arrays/
  )
})
