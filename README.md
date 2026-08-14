# SecureSignal 🔐📊

> 隐私保护 AI 投资组合顾问 — powered by Flare Confidential Compute

**Flare Summer Signal Hackathon — Bounty 2: Confidential Compute Apps**

## 问题
今天想获得个性化加密组合建议，意味着要把全部持仓与策略交给一个你无法审计的中心化服务。

## 方案
SecureSignal 把分析引擎运行在 TEE（Trusted Execution Environment）中：持仓在客户端加密，
仅在 enclave 内解密；分析结果附带 attestation，其签名者由链上合约对照登记的 TEE 密钥验证。
**Not even we can see your data.**

## 为什么必须是 Flare
1. **Confidential Compute**：链上 attestation 验证 —— 免信任地证明 TEE 运行的是已公开代码
2. **FTSO**：免费、去中心化的实时价格喂价，直接供分析引擎消费
3. **EVM 兼容**：标准钱包 UX 完成加密密钥交换

## 架构
完整走读见 [docs/architecture.md](docs/architecture.md)；**部署手册（env 全参考 / Coston2 分步 / GCP Confidential Space 接入 / LLM 接入 / 故障排查）见 [docs/deployment.md](docs/deployment.md)**。

三个组件：

| 路径 | 说明 |
|---|---|
| `contracts/` | Hardhat 项目：`AnalysisRegistry`（任务锚定、TEE 密钥登记、attestation 验签的结果提交）+ `FtsoV2Reader` |
| `tee-service/` | Python FastAPI 服务：ECIES 解密、组合分析（LLM + 规则引擎）、FTSO 读价、attestation 签名、结果 relayer 上链 |
| `frontend/` | Next.js 16 应用：钱包连接、客户端 ECIES 加密、结果解密与展示 |

## 实现状态（诚实标注）

**真实实现且已验证：**
- 合约端 `ecrecover` attestation 验签：`rotateTeeKey`（onlyOwner）登记 TEE 密钥与 `teeAddress`；
  `_verifyAttestation` 校验签名者 == 登记地址；`submitResult` 拒绝伪造签名（含负例测试，
  `npx hardhat test` 全绿）。
- ECIES 端到端加密：`eciesjs`（浏览器）↔ `eciespy`（TEE 服务），secp256k1，线格式逐字节兼容，
  双向交叉解密测试向量通过。
- 结果 relayer 上链：TEE 服务以 relayer 身份调 `submitResult(taskId, resultHash, attestation)`，
  客户端可对链核对 `ResultSubmitted` 事件与 `resultHash`。
- **LLM 分析引擎**：OpenAI 兼容 API（env `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`）。
  LLM 只产出判断字段（risk_score / risk_level / rebalance / 中文 summary），组合数学全部由
  `analysis/engine.py` 确定性计算；失败自动重试一次后回退规则引擎，响应以
  `analysis_mode: "llm" | "rule-fallback"` 如实标注。未配置 API key 时静默使用规则引擎。
- **FTSO 真实读价**：`analysis/price_provider.py` 经 FlareContractRegistry 直读 Coston2 官方
  `FtsoV2`（bytes21 feed ID，60s TTL 缓存，10s RPC 超时），**无静默回退假价**——失败显式报错。
  联机实测（2026-07-19，`ANALYSIS_LIVE_TEST=1 python -m unittest analysis.test_price_provider`，
  完整输出见 `tee-service/ftso-live-test.log`）：
  BTC/USD **$64,649.78**、ETH/USD **$1,866.52**、FLR/USD **$0.006560**
  （feed 时间戳 2026-07-19 04:03 UTC，`price_source="coston2-ftso"`）。
  仅当显式 `ANALYSIS_OFFLINE=1` 时使用 dev fixture 价，且标注 `price_source: "offline-fixture"`。
- 本地端到端集成验证 **23/23 断言通过**（`frontend/e2e/e2e-local-run.log`）：
  起链 → 部署 → 加密 → `/analyze` → 解密 → 链上 resultHash 一致 → ecrecover 一致。
- 完整 Web 流程：连接钱包 → `requestAnalysis` → 加密持仓 → `POST /analyze` → 解密结果 → 展示。
- `docker compose up --build` 一键起全栈（含 `deploy` 初始化服务：健康检查 → 部署 → 登记 TEE 密钥）。

**dev-simulated（代码与响应中均有明确标注，非生产声明）：**
- attestation token 是结构化 JSON（`mode: "dev-simulated"`）+ TEE secp256k1 签名，
  **不是** GCP Confidential Space vTPM JWT；接真实 vTPM 的改造路线见
  `tee-service/attestation/vtpm.py` 的 TODO 与 [docs/deployment.md](docs/deployment.md) §3。
- 本地运行时"TEE"只是普通 FastAPI 进程 —— 开发机上没有真实 enclave。
- 本地链没有 FTSO，localhost 默认走 `ANALYSIS_OFFLINE=1` fixture 价（有标注）。
- 本地链上登记的 `expectedImageDigest` 是 `keccak256("dev-image")` 占位值。

**尚无法本地闭环的外部依赖**（接入指南均在 docs/deployment.md）：
Coston2 真实部署（需私钥 + faucet 测试币）、GCP Confidential Space（需 GCP TEE 环境）、
真实 LLM 调用（需 API key）。

## 环境变量快速配置

完整参考表见 [docs/deployment.md](docs/deployment.md) §1，这里只列最小集：

```bash
# tee-service（本地最小集；生产把 RPC_URL 换成 Coston2 或省略即默认 Coston2）
export TEE_PRIVATE_KEY=0x<TEE私钥>     # 生产必填；未设=进程临时密钥（仅 dev）
export PRIVATE_KEY=0x<relayer私钥>     # 可选；未设则 onchain_submitted=false
export RPC_URL=http://127.0.0.1:8545   # 可选；默认 https://coston2-api.flare.network/ext/C/rpc
export ANALYSIS_OFFLINE=1              # 可选；=1 用 fixture 价（本地链必须）
# 启用 LLM（可选）：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL

# contracts（仅 Coston2 部署需要，写入 contracts/.env）
PRIVATE_KEY=0x<部署者私钥>

# frontend/.env.local（cp .env.example .env.local）
NEXT_PUBLIC_PROJECT_ID=<WalletConnect Cloud project id>   # 必填，无 fallback
NEXT_PUBLIC_TEE_URL=http://localhost:8000                 # 可选
```

## 本地运行（五步）

前置：Node 20+、Python 3.11+、git bash（Windows）或任意 POSIX shell。

```bash
# 1. 起本地链（终端 A）
cd contracts
npm install
npx hardhat node

# 2. 部署合约 + 登记 dev TEE 密钥（终端 B）
cd contracts
npx hardhat run scripts/deploy.ts --network localhost
npx hardhat run scripts/setup-tee.ts --network localhost
#    → 真实地址写入 frontend/src/config/contract-addresses.json
#      与 tee-service/config/contract-addresses.json

# 3. 起 TEE 服务（终端 B）
cd ../tee-service
pip install -r requirements.txt
export TEE_PRIVATE_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
export RPC_URL=http://127.0.0.1:8545
export ANALYSIS_OFFLINE=1
uvicorn main:app --host 0.0.0.0 --port 8000

# 4. 起前端（终端 C）
cd ../frontend
npm install
cp .env.example .env.local   # 填入 NEXT_PUBLIC_PROJECT_ID（WalletConnect Cloud）
npm run dev

# 5. 打开 http://localhost:3000
```

> 上述两个私钥是 hardhat 公开 dev 账户（#1 和 #0），**仅限本地使用**。

**Docker 一键起**：`docker compose up --build` 启动同一套栈 —— `deploy` 初始化服务等待链健康检查，
依次执行 `deploy.ts` 与 `setup-tee.ts`，成功后 `tee-service` 与 `frontend` 才启动。详见
`docker-compose.yml` 注释（compose 配置经结构校验，本机无 docker 未实机启动）。compose 默认 `ANALYSIS_OFFLINE=1`（fixture 价，有标注）；
要真实 FTSO 读价请 `ANALYSIS_OFFLINE=0 RPC_URL=https://coston2-api.flare.network/ext/C/rpc docker compose up --build`
（注意此时 relayer 指向的链需与地址配置一致，本地链场景请保持默认）。

## 自行验证
生产信任模型：TEE 镜像 digest 锚定在链上，attestation 证明 enclave 运行的正是已公开代码。

1. 构建镜像：`cd tee-service && docker build -t securesignal-tee .`
   （可复现：基础镜像按 digest 锁定，pip 依赖经 `requirements-lock.txt` 哈希锁定）
2. 比对镜像 digest 与链上 `expectedImageDigest`。
3. 不一致 = enclave 运行的不是已公开代码。

当前 dev 构建中第 2–3 步用的是 `setup-tee.ts` 登记的占位 digest；真实 vTPM 度量是上述 TODO。

## Live Demo
- **Contracts（Coston2 测试网，chainId 114）— 已部署并端到端验证（2026-07-19）**：
  - AnalysisRegistry: [`0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482`](https://coston2-explorer.flare.network/address/0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482)
  - FtsoV2Reader: [`0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a`](https://coston2-explorer.flare.network/address/0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a)
  - 登记 TEE 地址: `0xEe4975C290FBF46757A1D90F02c3CF555163556E`
  - 生产冒烟测试 **12/12 通过**（`frontend/e2e/e2e-coston2.mjs`）：真实 FTSO 喂价、attestation ecrecover == TEE 地址、链上 status=Verified
- App: https://securesignal.vercel.app
- TEE 后端: https://securesignal-tee.onrender.com（`/public-key` 在线，已实测）
- 演示视频（2:19，英文配音+字幕，含真实 Coston2 交易）: https://youtu.be/1V5yuxIENvc
- 视频直链（备用）: https://github.com/juangh123/securesignal/raw/main/video/dist/SecureSignal_demo_1080p_v3.mp4
- TEE 公钥: `04088c6f6e685b84d396521b59d8b8ff794f4d6a27d47d487b716eced258fa76644e36bee0f46525f9920c9b6dd9f9ef1773d6aff610b0f944d29b0624f4cc10b6`
- Contracts (Flare Mainnet): *(未部署)*

## Roadmap
1. **Q3 2026**：真实 GCP Confidential Space vTPM attestation；钱包自动导入持仓；FAssets (FXRP) 分析
2. **Q4 2026**：DAO treasury 多签报告模式
3. **2027**：申请 Flare 生态 grant；向其他 builder 开放 TEE 分析 API

