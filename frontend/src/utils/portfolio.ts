// Pure portfolio helpers shared by the app and its unit tests. This module
// must stay free of React/Next imports so it can also run under Node's
// type-stripping test runner (`npm run test:unit`).

export interface HoldingItem {
  symbol: string
  amount: number
  value_usd: number
  weight_pct: number
}

export interface RebalanceItem {
  action: 'increase' | 'decrease' | 'hold'
  symbol: string
  reason: string
}

export interface AnalysisResult {
  status: 'success' | 'error'
  analysis_mode: 'llm' | 'rule-fallback'
  price_source: string
  prices_used: Record<string, number>
  total_value_usd: number
  holdings: HoldingItem[]
  risk_score: number // 0-100
  risk_level: 'low' | 'medium' | 'high'
  rebalance: RebalanceItem[]
  summary: string
  error?: string
}

// Client-side mirror of the TEE service's input limits (analysis/engine.py),
// so oversized or malformed portfolios fail fast with a readable message
// instead of after a wallet transaction.
export const MAX_HOLDINGS = 25
export const MAX_SYMBOL_LENGTH = 12
export const MAX_AMOUNT = 1e18
export const SYMBOL_RE = new RegExp(`^[A-Z0-9]{1,${MAX_SYMBOL_LENGTH}}$`)

/** Parse "2 BTC, 10 ETH" style input into { BTC: 2, ETH: 10 }. */
export function parseHoldings(input: string): Record<string, number> {
  const holdings: Record<string, number> = {}
  const parts = input
    .split(/[\n,;]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  for (const part of parts) {
    const m = part.match(/^([0-9]*\.?[0-9]+)\s*([A-Za-z][A-Za-z0-9]*)$/)
    if (!m) {
      throw new Error(`Cannot parse holding: "${part}" — expected format "2 BTC" (amount + symbol)`)
    }
    const symbol = m[2].toUpperCase()
    const amount = Number.parseFloat(m[1])
    if (!SYMBOL_RE.test(symbol)) {
      throw new Error(
        `Invalid symbol "${m[2]}": use 1-${MAX_SYMBOL_LENGTH} letters or digits, e.g. BTC`
      )
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      throw new Error(`Invalid amount for holding: "${part}"`)
    }
    if (amount > MAX_AMOUNT) {
      throw new Error(`Amount for ${symbol} is too large (max ${MAX_AMOUNT.toExponential(0)})`)
    }
    holdings[symbol] = (holdings[symbol] ?? 0) + amount
  }
  if (Object.keys(holdings).length === 0) {
    throw new Error('Enter at least one holding, e.g. "2 BTC, 10 ETH"')
  }
  if (Object.keys(holdings).length > MAX_HOLDINGS) {
    throw new Error(
      `Too many holdings (${Object.keys(holdings).length}); the TEE accepts at most ${MAX_HOLDINGS} distinct symbols`
    )
  }
  return holdings
}

function isRecord(x: unknown): x is Record<string, unknown> {
  return x !== null && typeof x === 'object' && !Array.isArray(x)
}

/**
 * The decrypted payload is untrusted input: a buggy or hostile service could
 * return a shape the UI would crash on. Validate the minimum contract before
 * anything renders it.
 */
export function assertAnalysisResult(value: unknown): AnalysisResult {
  if (!isRecord(value)) {
    throw new Error('TEE result is malformed: not a JSON object')
  }
  const rec = value
  if (rec.status !== 'success' && rec.status !== 'error') {
    throw new Error('TEE result is malformed: missing status field')
  }
  if (rec.status === 'success') {
    const numeric = ['total_value_usd', 'risk_score']
    if (numeric.some((k) => typeof rec[k] !== 'number' || !Number.isFinite(rec[k] as number))) {
      throw new Error('TEE result is malformed: valuation fields are not numbers')
    }
    if (!Array.isArray(rec.holdings) || !Array.isArray(rec.rebalance)) {
      throw new Error('TEE result is malformed: holdings/rebalance are not arrays')
    }
  }
  return value as unknown as AnalysisResult
}
