'use client'

import { useState } from 'react'
import { useAccount, usePublicClient, useWriteContract } from 'wagmi'
import { decodeEventLog, keccak256, stringToHex } from 'viem'
import type { Abi, Hex } from 'viem'
import {
  generateSessionKeyPair,
  encryptForTee,
  decryptResult,
  normalizePubKeyHex,
} from '@/utils/crypto'
import addresses from '@/config/contract-addresses.json'
import AnalysisRegistryABI from '@/config/AnalysisRegistry.json'

const TEE_URL = process.env.NEXT_PUBLIC_TEE_URL ?? 'http://localhost:8000'
const REGISTRY_ADDRESS = addresses.AnalysisRegistry as `0x${string}`
const REGISTRY_ABI = AnalysisRegistryABI.abi as Abi

// ---------------------------------------------------------------------------
// TEE 引擎输出契约（全项目唯一标准，逐字遵守）
// ---------------------------------------------------------------------------

interface HoldingItem {
  symbol: string
  amount: number
  value_usd: number
  weight_pct: number
}

interface RebalanceItem {
  action: 'increase' | 'decrease' | 'hold'
  symbol: string
  reason: string
}

interface AnalysisResult {
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

interface AttestationParsed {
  result_hash?: string
  task_id?: number | string
  image_digest?: string
  tee_address?: string
  timestamp?: string | number
  mode?: string
  signature?: string
  [key: string]: unknown
}

interface AnalysisView {
  taskId: string
  txHash: string
  result: AnalysisResult
  resultHash?: string
  attestation?: AttestationParsed
  attestationRaw?: unknown
}

// ---------------------------------------------------------------------------
// 展示常量
// ---------------------------------------------------------------------------

const STEPS = ['连接钱包', '校验密钥', '链上登记', 'TEE 分析', '解密完成'] as const

const RISK_META: Record<
  AnalysisResult['risk_level'],
  { label: string; badge: string; bar: string; text: string }
> = {
  low: {
    label: '低风险',
    badge: 'bg-emerald-100 text-emerald-800',
    bar: 'bg-emerald-500',
    text: 'text-emerald-700',
  },
  medium: {
    label: '中风险',
    badge: 'bg-amber-100 text-amber-800',
    bar: 'bg-amber-500',
    text: 'text-amber-700',
  },
  high: {
    label: '高风险',
    badge: 'bg-rose-100 text-rose-800',
    bar: 'bg-rose-500',
    text: 'text-rose-700',
  },
}

const ACTION_META: Record<
  RebalanceItem['action'],
  { icon: string; label: string; chip: string }
> = {
  increase: { icon: '▲', label: '增持', chip: 'bg-emerald-100 text-emerald-800' },
  decrease: { icon: '▼', label: '减持', chip: 'bg-rose-100 text-rose-800' },
  hold: { icon: '●', label: '持有', chip: 'bg-slate-800 text-slate-300' },
}

function fmtUsd(n: number): string {
  return n.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  })
}

function fmtAmount(n: number): string {
  return n.toLocaleString('en-US', { maximumFractionDigits: 8 })
}

/** Parse "2 BTC, 10 ETH" style input into { BTC: 2, ETH: 10 }. */
function parseHoldings(input: string): Record<string, number> {
  const holdings: Record<string, number> = {}
  const parts = input
    .split(/[\n,;]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  for (const part of parts) {
    const m = part.match(/^([0-9]*\.?[0-9]+)\s*([A-Za-z][A-Za-z0-9]*)$/)
    if (!m) {
      throw new Error(`无法解析持仓条目："${part}"，格式应为 "2 BTC"（数量 + 币种）`)
    }
    const symbol = m[2].toUpperCase()
    const amount = Number.parseFloat(m[1])
    if (!Number.isFinite(amount) || amount <= 0) {
      throw new Error(`持仓数量无效："${part}"`)
    }
    holdings[symbol] = (holdings[symbol] ?? 0) + amount
  }
  if (Object.keys(holdings).length === 0) {
    throw new Error('请输入至少一条持仓，例如 "2 BTC, 10 ETH"')
  }
  return holdings
}

function asRecord(x: unknown): Record<string, unknown> {
  return x !== null && typeof x === 'object' && !Array.isArray(x)
    ? (x as Record<string, unknown>)
    : {}
}

function tryParseJson(x: unknown): AttestationParsed | undefined {
  if (x === null || x === undefined) return undefined
  if (typeof x === 'string') {
    try {
      return asRecord(JSON.parse(x)) as AttestationParsed
    } catch {
      return undefined
    }
  }
  return asRecord(x) as AttestationParsed
}

// ---------------------------------------------------------------------------
// 小组件
// ---------------------------------------------------------------------------

function Badge({ className, children }: { className: string; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold ${className}`}
    >
      {children}
    </span>
  )
}

function AnalysisModeBadge({ mode }: { mode: AnalysisResult['analysis_mode'] }) {
  if (mode === 'llm') {
    return <Badge className="bg-emerald-100 text-emerald-800">✦ AI 分析（LLM）</Badge>
  }
  return <Badge className="bg-slate-800 text-slate-300">⚙ 规则回退</Badge>
}

function PriceSourceBadge({ source }: { source: string }) {
  if (source === 'offline-fixture') {
    return <Badge className="bg-amber-100 text-amber-800">◈ fixture 价（离线）</Badge>
  }
  return <Badge className="bg-teal-100 text-teal-800">◈ 实时价 · {source}</Badge>
}

function StepBar({ step, failedStep }: { step: number; failedStep: number }) {
  return (
    <ol className="flex flex-wrap items-center gap-y-2">
      {STEPS.map((label, i) => {
        const n = i + 1
        const done = step > n
        const current = step === n && failedStep === 0
        const failed = failedStep === n
        const circle = done
          ? 'bg-emerald-600 text-white'
          : failed
            ? 'bg-rose-600 text-white'
            : current
              ? 'border-2 border-amber-600 text-amber-700 animate-pulse'
              : 'bg-slate-800 text-slate-400'
        return (
          <li key={label} className="flex items-center">
            <span
              className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${circle}`}
            >
              {done ? '✓' : failed ? '✗' : n}
            </span>
            <span
              className={`ml-1.5 text-xs ${
                failed
                  ? 'text-rose-700 font-semibold'
                  : current
                    ? 'text-amber-700 font-semibold'
                    : done
                      ? 'text-emerald-700'
                      : 'text-slate-500'
              }`}
            >
              {label}
            </span>
            {i < STEPS.length - 1 && (
              <span
                className={`mx-2 h-px w-5 ${done ? 'bg-emerald-400' : 'bg-slate-700'}`}
                aria-hidden
              />
            )}
          </li>
        )
      })}
    </ol>
  )
}

// ---------------------------------------------------------------------------
// 主页面
// ---------------------------------------------------------------------------

export default function Home() {
  const { address, isConnected } = useAccount()
  const publicClient = usePublicClient()
  const { writeContractAsync } = useWriteContract()

  const [portfolioText, setPortfolioText] = useState('0.5 BTC, 2 ETH, 10000 FLR')
  const [riskProfile, setRiskProfile] = useState('moderate')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState(0) // 0 = 未开始；1..5 = 当前步骤；6 = 全部完成
  const [failedStep, setFailedStep] = useState(0)
  const [result, setResult] = useState<AnalysisView | null>(null)

  const handleAnalyze = async () => {
    setError('')
    setResult(null)
    setBusy(true)
    setFailedStep(0)
    let current = 0
    const go = (n: number) => {
      current = n
      setStep(n)
    }
    try {
      go(1)
      if (!isConnected || !address) throw new Error('请先连接钱包')
      if (!publicClient) throw new Error('公共 RPC 客户端不可用，请检查网络配置')

      // 1. Read active TEE public key from the contract.
      go(2)
      setStatus('1/7 从合约读取 activeTeePublicKey…')
      const onChainRaw = (await publicClient.readContract({
        address: REGISTRY_ADDRESS,
        abi: REGISTRY_ABI,
        functionName: 'activeTeePublicKey',
      })) as Hex
      const onChainKey = normalizePubKeyHex(onChainRaw ?? '')
      if (!onChainKey) {
        throw new Error('合约 activeTeePublicKey 为空：尚未登记 TEE 公钥，流程终止')
      }

      // 2. Fetch TEE service public key and cross-check against the on-chain value.
      setStatus('2/7 获取 TEE 服务公钥并与链上值交叉校验…')
      const pkResp = await fetch(`${TEE_URL}/public-key`)
      if (!pkResp.ok) throw new Error(`GET /public-key 失败：HTTP ${pkResp.status}`)
      const pkJson = (await pkResp.json()) as { public_key?: string }
      if (!pkJson.public_key) throw new Error('TEE /public-key 响应缺少 public_key 字段')
      const teeKey = normalizePubKeyHex(pkJson.public_key)
      if (teeKey !== onChainKey) {
        throw new Error(
          `TEE 公钥与链上登记值不一致，已阻断请求（可能存在中间人风险）。\n` +
            `链上: ${onChainKey.slice(0, 24)}…${onChainKey.slice(-8)}\n` +
            `服务: ${teeKey.slice(0, 24)}…${teeKey.slice(-8)}`
        )
      }

      // 3. Retrieve or generate session key pair (private key never leaves the browser).
      go(3)
      setStatus('3/7 生成会话密钥对…')
      let session: ReturnType<typeof generateSessionKeyPair>;
      const cachedSession = sessionStorage.getItem('securesignal_session_key')
      if (cachedSession) {
        session = JSON.parse(cachedSession)
      } else {
        session = generateSessionKeyPair()
        sessionStorage.setItem('securesignal_session_key', JSON.stringify(session))
      }

      // 4. Build plaintext per protocol and encrypt for the TEE.
      setStatus('4/7 本地加密持仓数据（ECIES / secp256k1）…')
      const plaintext = {
        client_pubkey: session.publicKeyHex,
        holdings: parseHoldings(portfolioText),
        risk_profile: riskProfile,
      }
      const encryptedData = encryptForTee(teeKey, plaintext)
      const inputDataHash = keccak256(stringToHex(encryptedData))

      // 5. Send requestAnalysis transaction and wait for the receipt.
      setStatus('5/7 请在钱包中确认 requestAnalysis 交易…')
      const txHash = await writeContractAsync({
        address: REGISTRY_ADDRESS,
        abi: REGISTRY_ABI,
        functionName: 'requestAnalysis',
        args: [inputDataHash],
      })
      setStatus('5/7 等待交易确认…')
      const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash })
      if (receipt.status !== 'success') {
        throw new Error(`requestAnalysis 交易执行失败（reverted）。tx: ${txHash}`)
      }

      // 6. Parse the real taskId from the AnalysisRequested contract event.
      setStatus('6/7 从交易事件解析 taskId…')
      let taskId: bigint | null = null
      for (const log of receipt.logs) {
        try {
          const ev = decodeEventLog({ abi: REGISTRY_ABI, data: log.data, topics: log.topics })
          if (ev.eventName === 'AnalysisRequested') {
            taskId = (ev.args as unknown as { taskId: bigint }).taskId
            break
          }
        } catch {
          // not our event, keep scanning
        }
      }
      if (taskId === null) {
        throw new Error('交易回执中未找到 AnalysisRequested 事件，无法获取真实 taskId')
      }

      // 7. Submit encrypted payload to the TEE and decrypt the response.
      go(4)
      setStatus('7/7 提交加密数据至 TEE 并等待分析…')
      const analyzeResp = await fetch(`${TEE_URL}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: Number(taskId), encrypted_data: encryptedData }),
      })
      if (!analyzeResp.ok) {
        const body = await analyzeResp.text().catch(() => '')
        throw new Error(`POST /analyze 失败：HTTP ${analyzeResp.status} ${body}`)
      }
      const data = (await analyzeResp.json()) as {
        task_id: number
        encrypted_result: string
        attestation?: unknown
        result_hash?: string
      }
      if (!data.encrypted_result) throw new Error('TEE 响应缺少 encrypted_result 字段')

      go(5)
      setStatus('7/7 用会话私钥解密分析结果…')
      const decrypted = decryptResult<AnalysisResult>(session.privateKeyHex, data.encrypted_result)

      setResult({
        taskId: taskId.toString(),
        txHash,
        result: decrypted,
        resultHash: data.result_hash,
        attestation: tryParseJson(data.attestation),
        attestationRaw: data.attestation,
      })
      go(6)
      setStatus('')
    } catch (e) {
      setFailedStep(current)
      setError((e as Error).message)
      setStatus('')
    } finally {
      setBusy(false)
    }
  }

  const res = result?.result
  const risk = res ? RISK_META[res.risk_level] : undefined
  const att = result?.attestation
  const attHashMatch =
    att?.result_hash && result?.resultHash
      ? normalizePubKeyHex(String(att.result_hash)) === normalizePubKeyHex(String(result.resultHash))
      : undefined

  return (
    <main className="flex min-h-screen flex-col items-center p-12 bg-slate-900 text-slate-100">
      <div className="z-10 max-w-5xl w-full items-center justify-between font-mono text-sm lg:flex">
        <p className="fixed left-0 top-0 flex w-full justify-center border-b border-slate-600 bg-slate-800/80 pb-6 pt-8 backdrop-blur-2xl lg:static lg:w-auto lg:rounded-xl lg:border lg:bg-slate-800 lg:p-4">
          SecureSignal - Flare Confidential Compute
        </p>
        <div className="fixed bottom-0 left-0 flex h-48 w-full items-end justify-center lg:static lg:h-auto lg:w-auto">
          {/* @ts-expect-error Web3Modal component */}
          <w3m-button />
        </div>
      </div>

      <div className="relative flex place-items-center flex-col gap-8 w-full max-w-2xl mt-12">
        <div className="bg-white p-8 rounded-2xl shadow-xl w-full">
          <h2 className="text-2xl font-bold mb-6 text-slate-200">Your Portfolio</h2>

          {isConnected ? (
            <div className="flex flex-col gap-4">
              <div className="bg-amber-50 border border-amber-200 text-amber-800 text-sm p-3 rounded-lg mb-2">
                <strong>信任边界提示：</strong> 由于接入通用大模型机制（非机密推理API），提交数据的核心价值字段（如持仓数量、币种）将被作为 Prompt 被透明发送至模型推理方，脱离 TEE 的保密范畴。仅针对 TEE 至用户浏览器的传输过程具有机密保护。
              </div>
              <label className="text-sm font-medium text-slate-300">
                持仓（敏感数据，仅在浏览器本地加密后送出）
              </label>
              <textarea
                className="w-full p-4 border border-slate-600 rounded-lg focus:ring-2 focus:ring-amber-600 focus:border-amber-600 text-slate-100 h-32"
                value={portfolioText}
                onChange={(e) => setPortfolioText(e.target.value)}
                placeholder="例如：0.5 BTC, 2 ETH, 10000 FLR"
              />

              <label className="text-sm font-medium text-slate-300">风险偏好</label>
              <select
                className="w-full p-3 border border-slate-600 rounded-lg text-slate-100"
                value={riskProfile}
                onChange={(e) => setRiskProfile(e.target.value)}
              >
                <option value="conservative">conservative（保守）</option>
                <option value="moderate">moderate（稳健）</option>
                <option value="aggressive">aggressive（激进）</option>
              </select>

              <button
                onClick={handleAnalyze}
                disabled={busy}
                className="mt-4 bg-amber-700 hover:bg-amber-800 disabled:bg-stone-400 text-white font-bold py-3 px-6 rounded-lg transition-colors"
              >
                {busy ? '处理中…' : '加密并在 TEE 中分析'}
              </button>

              {(busy || step > 0) && (
                <div className="mt-2 p-4 bg-slate-800 border border-slate-700 rounded-lg">
                  <StepBar step={step} failedStep={failedStep} />
                  {busy && status && (
                    <p className="mt-3 text-sm text-amber-800 break-all whitespace-pre-wrap">
                      {status}
                    </p>
                  )}
                </div>
              )}

              {error && (
                <div className="mt-2 p-4 bg-rose-50 border border-rose-200 text-rose-700 rounded-lg text-sm break-all whitespace-pre-wrap">
                  <p className="font-semibold mb-1">流程中断</p>
                  {error}
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-8 text-slate-400">
              请连接钱包以使用 SecureSignal。
            </div>
          )}
        </div>

        {!result && !busy && isConnected && !error && (
          <div className="w-full border-2 border-dashed border-slate-600 rounded-2xl p-8 text-center text-slate-500 text-sm">
            分析结果将在此展示 —— 数据全程加密，仅您的会话私钥可解密
          </div>
        )}

        {result && res && (
          <div className="bg-white p-8 rounded-2xl shadow-xl w-full">
            <div className="flex flex-wrap items-center gap-2 mb-6">
              <h2 className="text-2xl font-bold text-slate-200 mr-auto">TEE 分析结果</h2>
              <AnalysisModeBadge mode={res.analysis_mode} />
              <PriceSourceBadge source={res.price_source} />
            </div>

            {res.status === 'error' ? (
              <div className="p-5 bg-rose-50 border-2 border-rose-300 rounded-xl">
                <p className="font-bold text-rose-800 mb-1">⚠ TEE 分析失败</p>
                <p className="text-sm text-rose-700 whitespace-pre-wrap">
                  {res.error ?? '未知错误（未携带 error 说明）'}
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-6 text-sm text-slate-200">
                {/* 总资产 */}
                <section className="bg-slate-800 border border-slate-700 rounded-xl p-5">
                  <h3 className="font-semibold text-slate-400 uppercase text-xs mb-1">
                    总资产估值
                  </h3>
                  <p className="text-3xl font-bold text-slate-100">
                    {fmtUsd(res.total_value_usd)}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {Object.entries(res.prices_used).map(([sym, price]) => (
                      <span
                        key={sym}
                        className="inline-block px-2 py-0.5 rounded bg-slate-800 text-slate-400 text-xs font-mono"
                      >
                        {sym} {fmtUsd(price)}
                      </span>
                    ))}
                  </div>
                </section>

                {/* 持仓明细 */}
                <section>
                  <h3 className="font-semibold text-slate-400 uppercase text-xs mb-2">
                    持仓明细
                  </h3>
                  {res.holdings.length === 0 ? (
                    <p className="text-slate-500">（无持仓数据）</p>
                  ) : (
                    <ul className="flex flex-col gap-3">
                      {res.holdings.map((h) => (
                        <li key={h.symbol}>
                          <div className="flex items-baseline justify-between mb-1">
                            <span className="font-semibold text-slate-200">
                              {h.symbol}
                              <span className="ml-2 font-normal text-slate-400 text-xs">
                                {fmtAmount(h.amount)} 枚 · {fmtUsd(h.value_usd)}
                              </span>
                            </span>
                            <span className="font-mono text-slate-300">
                              {h.weight_pct.toFixed(1)}%
                            </span>
                          </div>
                          <div className="h-2 w-full bg-slate-800 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-amber-600 rounded-full"
                              style={{
                                width: `${Math.min(100, Math.max(0, h.weight_pct))}%`,
                              }}
                            />
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                {/* 风险分 */}
                <section>
                  <h3 className="font-semibold text-slate-400 uppercase text-xs mb-2">
                    风险评分
                  </h3>
                  <div className="flex items-center gap-3">
                    <span className={`text-3xl font-bold ${risk?.text ?? ''}`}>
                      {res.risk_score}
                    </span>
                    <span className="text-slate-500 text-lg">/ 100</span>
                    {risk && <Badge className={risk.badge}>{risk.label}</Badge>}
                  </div>
                  <div className="mt-2 h-2 w-full bg-slate-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${risk?.bar ?? 'bg-stone-400'}`}
                      style={{ width: `${Math.min(100, Math.max(0, res.risk_score))}%` }}
                    />
                  </div>
                </section>

                {/* 再平衡建议 */}
                <section>
                  <h3 className="font-semibold text-slate-400 uppercase text-xs mb-2">
                    再平衡建议
                  </h3>
                  {res.rebalance.length === 0 ? (
                    <p className="text-slate-500">（无再平衡建议 —— 当前组合已均衡）</p>
                  ) : (
                    <ul className="flex flex-col gap-2">
                      {res.rebalance.map((r, i) => {
                        const meta = ACTION_META[r.action]
                        return (
                          <li
                            key={`${r.symbol}-${i}`}
                            className="flex items-start gap-3 p-3 bg-slate-800 border border-slate-700 rounded-lg"
                          >
                            <span
                              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold shrink-0 ${meta.chip}`}
                            >
                              {meta.icon} {meta.label}
                            </span>
                            <div>
                              <span className="font-semibold text-slate-200">{r.symbol}</span>
                              <p className="text-slate-400 mt-0.5">{r.reason}</p>
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </section>

                {/* 分析总结 */}
                <section>
                  <h3 className="font-semibold text-slate-400 uppercase text-xs mb-2">
                    分析总结
                  </h3>
                  <p className="whitespace-pre-wrap leading-relaxed text-slate-300">
                    {res.summary}
                  </p>
                </section>
              </div>
            )}

            <hr className="my-6 border-slate-700" />

            <div className="flex flex-col gap-5 text-sm text-slate-200">
              <section>
                <h3 className="font-semibold text-slate-400 uppercase text-xs mb-1">任务</h3>
                <p>
                  taskId: <span className="font-mono">{result.taskId}</span>
                </p>
                <p className="break-all">
                  tx: <span className="font-mono">{result.txHash}</span>
                </p>
              </section>

              <section>
                <h3 className="font-semibold text-slate-400 uppercase text-xs mb-1">
                  链上结果哈希（result_hash）
                </h3>
                {result.resultHash ? (
                  <p className="font-mono break-all">{result.resultHash}</p>
                ) : (
                  <p className="text-slate-500">（响应中无 result_hash 字段）</p>
                )}
              </section>

              <section>
                <h3 className="font-semibold text-slate-400 uppercase text-xs mb-1">
                  Attestation
                </h3>
                {att ? (
                  <div className="flex flex-col gap-1">
                    <p>
                      模式:{' '}
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${
                          att.mode === 'dev-simulated'
                            ? 'bg-amber-100 text-amber-800'
                            : 'bg-emerald-100 text-emerald-800'
                        }`}
                      >
                        {att.mode ?? 'unknown'}
                      </span>
                      {att.mode === 'dev-simulated' && (
                        <span className="ml-2 text-xs text-slate-400">
                          （开发期模拟证明，非生产级 TEE 证据）
                        </span>
                      )}
                    </p>
                    {att.tee_address && (
                      <p className="break-all">
                        TEE 地址: <span className="font-mono">{att.tee_address}</span>
                      </p>
                    )}
                    {att.timestamp !== undefined && (
                      <p>
                        时间戳: <span className="font-mono">{String(att.timestamp)}</span>
                      </p>
                    )}
                    {att.image_digest && (
                      <p className="break-all">
                        镜像摘要: <span className="font-mono">{att.image_digest}</span>
                      </p>
                    )}
                    {attHashMatch !== undefined && (
                      <p className={attHashMatch ? 'text-emerald-700' : 'text-rose-700'}>
                        attestation.result_hash 与响应 result_hash{' '}
                        {attHashMatch ? '一致 ✓' : '不一致 ✗'}
                      </p>
                    )}
                    {att.signature && (
                      <p className="break-all text-xs text-slate-400">
                        签名: <span className="font-mono">{att.signature}</span>
                      </p>
                    )}
                  </div>
                ) : result.attestationRaw ? (
                  <pre className="bg-slate-800 p-3 rounded-lg overflow-x-auto text-xs break-all whitespace-pre-wrap">
                    {typeof result.attestationRaw === 'string'
                      ? result.attestationRaw
                      : JSON.stringify(result.attestationRaw, null, 2)}
                  </pre>
                ) : (
                  <p className="text-slate-500">（响应中无 attestation 字段）</p>
                )}
              </section>

              <details className="text-xs text-slate-400">
                <summary className="cursor-pointer">查看完整解密结果 JSON</summary>
                <pre className="bg-slate-800 p-3 rounded-lg overflow-x-auto mt-2">
                  {JSON.stringify(result.result, null, 2)}
                </pre>
              </details>
            </div>
          </div>
        )}
      </div>
    </main>
  )
}

