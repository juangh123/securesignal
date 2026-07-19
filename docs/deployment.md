# SecureSignal 部署手册

> 适用范围：Coston2 testnet 部署、GCP Confidential Space 生产化接入、LLM 分析引擎启用。
> 本文所有命令、路径、环境变量名均已与代码逐一核对（`tee-service/main.py`、`analysis/llm.py`、`analysis/price_provider.py`、`flare/contracts.py`、`attestation/vtpm.py`、`contracts/scripts/*.ts`、`contracts/hardhat.config.ts`、`docker-compose.yml`、`frontend/.env.example`）。
> 最后核对日期：2026-07-19。

---

## 0. 组件与前置条件

| 组件 | 路径 | 技术栈 |
|---|---|---|
| 合约 | `contracts/` | Hardhat + Solidity 0.8.20（`AnalysisRegistry.sol`、`FtsoV2Reader.sol`） |
| TEE 服务 | `tee-service/` | Python 3.11 + FastAPI（uvicorn，端口 8000） |
| 前端 | `frontend/` | Next.js 16（端口 3000） |

前置条件：Node 20+、Python 3.11+、git bash（Windows）或任意 POSIX shell；Docker 部署需 Docker Desktop。

---

## 1. 环境变量全参考

### 1.1 合约端（`contracts/`，经 `hardhat.config.ts` 的 `dotenv.config()` 从 `contracts/.env` 读取）

| 变量 | 必填性 | 默认值 | 说明 | 示例 |
|---|---|---|---|---|
| `PRIVATE_KEY` | Coston2 部署**必填**；localhost 可选 | 未设时 `accounts: []`（coston2 网络无签名账户，`deploy.ts` 会在 `ethers.getSigners()` 处失败） | 部署者/owner 账户私钥，需持有 Coston2 测试币（C2FLR）付 gas | `0x<64 hex>`（**不要**复用 hardhat 公开账户） |

网络配置（硬编码于 `hardhat.config.ts`，非 env）：
- `localhost`: `http://127.0.0.1:8545`
- `coston2`: `https://coston2-api.flare.network/ext/C/rpc`，chainId `114`
- `deploy.ts` 只支持这两个网络，其他 `--network` 值会直接抛 `Unsupported network`。

### 1.2 TEE 服务端（`tee-service/`）

| 变量 | 必填性 | 默认值 | 说明 | 示例 |
|---|---|---|---|---|
| `TEE_PRIVATE_KEY` | 生产**必填** | 未设：进程内生成**临时**密钥并打印醒目警告（仅 dev；重启即换钥，链上登记随之失效） | TEE 的 secp256k1 私钥（ECIES 解密 + attestation 签名共用），32 字节 hex，可带 `0x` 前缀。见 `crypto/keys.py` | `0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d`（hardhat account #1，**仅本地**） |
| `PRIVATE_KEY` | 可选 | 未设：relayer 关闭，响应 `onchain_submitted=false`，启动日志打印 `Relayer NOT configured` | 结果上链 relayer 账户私钥（付 gas 调 `submitResult`）。见 `flare/contracts.py` | `0xac0974...f4f2ff80`（hardhat account #0，**仅本地**） |
| `RPC_URL` | 可选 | `https://coston2-api.flare.network/ext/C/rpc`（relayer 与 FTSO 读价共用同一默认值） | EVM JSON-RPC 端点 | `http://127.0.0.1:8545`（本地） |
| `ANALYSIS_OFFLINE` | 可选 | 未设 = 在线模式（真实 FTSO 读价） | 恰好等于 `"1"` 时启用 dev fixture 价（BTC 65000 / ETH 3500 / FLR 0.02，**非真实市价**），结果标注 `price_source="offline-fixture"`。见 `analysis/price_provider.py` | `1` |
| `LLM_API_KEY` | 可选 | 未设：LLM 关闭，使用确定性规则引擎（`analysis_mode="rule-fallback"`） | OpenAI 兼容 API key；设置即启用 LLM 分析。见 `analysis/llm.py` | `sk-...` |
| `LLM_BASE_URL` | 可选 | `https://api.openai.com/v1` | 任意 OpenAI 兼容端点（DeepSeek / Moonshot / 本地 mock 等） | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 可选 | `gpt-4o-mini` | 模型名 | `deepseek-chat` |
| `LLM_TIMEOUT` | 可选 | `30` | LLM 请求超时（秒）；非数字时回退 30 | `60` |
| `TEE_IMAGE_DIGEST` | 可选 | `dev` | 写入 attestation token 的 `image_digest` 字段。见 `attestation/vtpm.py` | `sha256:<镜像digest>` |
| `FTSO_READER_ADDRESS` | —（当前**未被代码消费**） | — | 仅 `docker-compose.yml` 透传预留。当前 `price_provider.py` 经 FlareContractRegistry 直读链上官方 `FtsoV2`，无需部署的 `FtsoV2Reader` 地址；该 env 属历史遗留 | — |
| `ANALYSIS_LIVE_TEST` | 可选（仅测试） | 未设 = 跳过联机单测 | 设为 `1` 时 `python -m unittest analysis.test_price_provider -v` 会执行真实 Coston2 RPC 联机用例 | `1` |

另有两个**配置文件**（非 env）：
- `tee-service/config/contract-addresses.json` — `AnalysisRegistry` / `FtsoV2Reader` 地址，由 `deploy.ts` 自动写入；relayer 据此判断 `is_configured()`。
- `tee-service/config/AnalysisRegistry.json` — 合约 artifact（提供 ABI）。

### 1.3 前端（`frontend/`，复制 `.env.example` 为 `.env.local`）

| 变量 | 必填性 | 默认值 | 说明 | 示例 |
|---|---|---|---|---|
| `NEXT_PUBLIC_PROJECT_ID` | **必填** | 无；缺失时 `src/config.ts` 直接 `throw`（构建/运行即失败） | WalletConnect Cloud project id，从 https://cloud.walletconnect.com 申请 | `a1b2c3...` |
| `NEXT_PUBLIC_TEE_URL` | 可选 | `http://localhost:8000` | TEE 服务 base URL（`src/app/page.tsx`） | `https://tee.example.com` |

注意：`docker-compose.yml` 的 frontend 服务**刻意不设置** `NEXT_PUBLIC_PROJECT_ID`，由挂载进容器的 `frontend/.env.local` 提供，避免 dummy 值覆盖真实 id。

---

## 2. Coston2 部署分步指南

### 2.1 获取测试币

1. 准备一个新账户（**不要用 hardhat 公开账户，不要复用任何主网账户**），导出私钥。
2. 打开 Flare 官方 faucet：https://faucet.flare.network ，选择 **Coston2**，粘贴地址领取 C2FLR（用于部署合约与 relayer 上链 gas）。
   已实测可用（2026-07-19）：每个地址每 24 小时可领 **100 C2FLR**（另可选 10 USDT0 / 10 FXRP）。
3. 到账确认：https://coston2-explorer.flare.network 查询地址余额。

### 2.2 配置部署账户

在 `contracts/` 下创建 `.env`（`hardhat.config.ts` 已加载 dotenv）：

```bash
# contracts/.env
PRIVATE_KEY=0x<你的64位hex私钥>
```

### 2.3 部署合约

```bash
cd contracts
npm install
npx hardhat run scripts/deploy.ts --network coston2
```

`deploy.ts` 的行为（与代码核对）：
- 先经 FlareContractRegistry（`0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019`，所有 Flare 网络同地址）解析官方 `FtsoV2` 地址，失败则**部署中止**（fail-fast）。
- 依次部署 `FtsoV2Reader` → `AnalysisRegistry`（初始 `expectedImageDigest` 为零值占位）。
- 把 `{"network":"coston2","AnalysisRegistry":"0x...","FtsoV2Reader":"0x..."}` 同时写入 `frontend/src/config/contract-addresses.json` 与 `tee-service/config/contract-addresses.json`。
- 控制台最后提示：`NEXT STEP: call rotateTeeKey(...)`。

### 2.4 登记 TEE 密钥（rotateTeeKey）

`AnalysisRegistry` 部署后必须执行 owner 调用 `rotateTeeKey(teePublicKey, imageDigest, teeAddress)`，否则 `teeAddress == 0`，所有 `submitResult` 都会因 `_verifyAttestation` 返回 false 而 revert（`"attestation failed"`）。

> ⚠️ **诚实提醒**：`scripts/setup-tee.ts` 当前**硬编码** hardhat dev account #1 作为 TEE 密钥，是 localhost 开发辅助脚本。**直接对 Coston2 运行会把公开密钥登记为生产 TEE 密钥——不要这样做。** 生产登记请按下述方式之一：
>
> - 复制 `setup-tee.ts` 改造：把 `TEE_PRIVATE_KEY` 改为从 env 读取（与你的 tee-service `TEE_PRIVATE_KEY` 一致），`imageDigest` 改为真实镜像 digest（见 §3.3），再 `npx hardhat run scripts/setup-tee-prod.ts --network coston2`；或
> - 用任意 web3 工具（ethers 脚本 / explorer 写合约）以 owner 身份直接调 `rotateTeeKey`：
>   - `newPublicKey`：TEE 公钥（65 字节未压缩 hex，`0x04` 前缀；服务启动日志 `[main] TEE public key:` 会打印）
>   - `newImageDigest`：`bytes32` 镜像 digest
>   - `newTeeAddress`：TEE 私钥对应地址（启动日志 `[main] TEE address:` 会打印）

脚本执行后会回读链上 `activeTeePublicKey` / `expectedImageDigest` / `teeAddress` 做一致性校验。

### 2.5 启动 tee-service（生产 env）

```bash
cd tee-service
pip install -r requirements.txt

export TEE_PRIVATE_KEY=0x<生产TEE私钥>        # 必须与链上登记一致，且持久固定
export PRIVATE_KEY=0x<relayer账户私钥>        # 需持有 C2FLR
# RPC_URL 默认已是 Coston2，无需设置；自建节点可覆盖
export TEE_IMAGE_DIGEST=sha256:<真实镜像digest>
# 在线 FTSO 读价：确保 ANALYSIS_OFFLINE 未设置
# 启用 LLM（可选，见 §4）：
# export LLM_API_KEY=sk-...
# export LLM_BASE_URL=... LLM_MODEL=...

uvicorn main:app --host 0.0.0.0 --port 8000
```

启动自检（日志）：`[main] Relayer configured: results will be submitted on-chain` 表示 relayer 就绪。

### 2.6 前端 `.env.local`

```bash
cd frontend
cp .env.example .env.local
```

```ini
NEXT_PUBLIC_PROJECT_ID=<WalletConnect Cloud project id>
NEXT_PUBLIC_TEE_URL=https://<你的tee-service域名或IP:端口>
```

```bash
npm install && npm run build && npm start   # 或 npm run dev
```

前端连接的钱包需切换到 Coston2（chainId 114，RPC `https://coston2-api.flare.network/ext/C/rpc`，explorer `https://coston2-explorer.flare.network`）。

### 2.7 部署验证清单

| # | 检查项 | 通过标准 |
|---|---|---|
| 1 | `curl https://<tee>/public-key` | 返回公钥 == 链上 `activeTeePublicKey`（去 `0x` 后逐字符一致） |
| 2 | tee-service 启动日志 | 出现 `Relayer configured`；无 `EPHEMERAL dev key` 警告 |
| 3 | 前端连接钱包（Coston2）→ 提交持仓 | `requestAnalysis` 交易在 explorer 可查，事件 `AnalysisRequested` 给出 `taskId` |
| 4 | `POST /analyze` 响应 | `onchain_submitted=true`；`price_source="coston2-ftso"`；`prices_used` 为正值真实价 |
| 5 | 链上核对 | explorer 上 `tasks(taskId).status == 3 (Verified)`，`resultHash` == 响应 `result_hash`；`ResultSubmitted` 事件可查 |
| 6 | 前端解密展示 | 会话私钥解密 `encrypted_result` 成功，显示 risk_score / rebalance / summary；`analysis_mode` 为 `llm` 或 `rule-fallback`（两者皆合法） |
| 7 | attestation | token JSON 中 `tee_address` == 链上 `teeAddress`，`mode` 字段如实标注（当前为 `dev-simulated`，见 §3） |

---

## 3. GCP Confidential Space 接入指南

### 3.1 现状与目标架构

**现状（诚实标注）**：当前 `attestation/vtpm.py` 产出的是结构化 JSON token（`mode: "dev-simulated"`）+ TEE secp256k1 签名；合约端 `_verifyAttestation` 用 `ecrecover` 校验签名者 == 登记的 `teeAddress`。这只能证明「持有登记私钥」，**不能**证明「代码运行在真实 enclave 中」。本地开发机上也没有真实 enclave。

**目标架构**：tee-service 镜像部署到 GCP Confidential Space（基于 AMD SEV 的 Confidential VM）：

```
frontend ──ECIES──> tee-service @ Confidential Space
                        │
                        ├─ 1. enclave 内生成/加载 secp256k1 密钥
                        ├─ 2. 向 metadata server 请求 OIDC attestation JWT
                        │     （audience 绑定，claims 含 image_digest / eat_nonce）
                        ├─ 3. JWT + 签名随 attestation 返回
                        └─ 4. 验证方确认 JWT 签名链（Google root）与 image_digest
                                     │
                                     v
                    AnalysisRegistry（Coston2/Flare）
                    teeAddress / expectedImageDigest 登记 + ecrecover 校验
```

### 3.2 代码中的 TODO 位置（改造锚点）

1. **`tee-service/attestation/vtpm.py` 模块 docstring（约第 24–31 行）**——`TODO(production)`：
   从 metadata server 获取 JWT（`http://metadata.google.internal/computeMetadata/v1/instance/attributes/attestation-token?audience=...`，以 GCP 官方文档为准），把 `report_data`（`task_id + result_hash` 的哈希）绑定进 token 的 `eat_nonce` claim，并由合约/验证方对 Google root certs 校验 JWT 签名链与 image digest。参考：https://cloud.google.com/confidential-computing/confidential-space/docs/attestation
2. **`contracts/contracts/AnalysisRegistry.sol` `_verifyAttestation`（约第 85–88 行）**——`TODO(production)`：
   除 ecrecover 外，还需验证 GCP Confidential Space JWT / Flare vTPM attestation 合约证明，确认 `teeAddress` 确实在 `expectedImageDigest` 对应的镜像内生成；仅签名检查只证明持有登记密钥。

### 3.3 改造路线（建议步骤）

**Step 1 — 镜像与密钥**
- 用现有 `tee-service/Dockerfile`（基础镜像已按 digest 锁定、`--require-hashes` 安装）构建 linux/amd64 镜像，推送到 Confidential Space 可用的 registry。
- 密钥策略二选一：
  a. enclave 启动时生成密钥，公钥/地址通过带 attestation 的登记流程上链（最贴近 TEE 信任模型）；
  b. 通过 Confidential Space 的 secret 挂载注入 `TEE_PRIVATE_KEY`（运维简单，但密钥在 enclave 外存在过）。

**Step 2 — `vtpm.py` 改造**
- 新增 `fetch_attestation_jwt(audience, nonce)`：按 TODO 注释中的 metadata server 端点请求 OIDC JWT；`nonce/eat_nonce` 绑定 `keccak256(task_id || result_hash)`。
- `generate_attestation_token` 扩展字段：`jwt`、`image_digest`（取 JWT claim 中的 `submods.container.image_digest`，同时设 `TEE_IMAGE_DIGEST` 为同值）、保留现有 `signature`（EIP-191，供链上 ecrecover）。
- token `mode` 改为 `"gcp-confidential-space"`（前端/审计可区分）。

**Step 3 — 链上验证方案选项**（对应合约 TODO，按信任假设与 gas 成本权衡）

| 方案 | 做法 | 信任假设 | 成本/复杂度 |
|---|---|---|---|
| A. 链下验证 + owner 登记 | 部署/轮换密钥时，链下验证 JWT（签名链、audience、image_digest、exp），通过则 owner 调 `rotateTeeKey` 登记新 `teeAddress` | 信任 owner 验证流程（一次性操作） | 最低；合约无需改动 |
| B. 链上验证 JWT | 合约内验 RS256 签名（需内置/预置 Google root 公钥，解析 JWT claims 比对 image_digest） | 仅信任 Google root | gas 极高，实现复杂 |
| C. 专用验证合约 / 预编译 | 借助 Flare 生态的 attestation 验证设施或独立 verifier 合约缓存已验证的 JWT 哈希 | 信任 verifier 合约实现 | 中等，取决于生态设施成熟度 |

**务实建议**：黑客松/首期上线用方案 A（合约保持现状，轮换流程文档化）；方案 B/C 作为后续路线。

**Step 4 — 部署形态**
- Confidential Space 要求 workload 为容器镜像 + 特定 VM 镜像与 launcher 配置；env（`PRIVATE_KEY`、`LLM_API_KEY` 等）通过 Confidential Space 的环境注入机制传入。
- 注意 `tee-service` 需出网访问：Flare RPC（FTSO + relayer）与 LLM API，需在 Confidential Space 网络策略中放行。

---

## 4. LLM 接入

### 4.1 启用（3 个 env 即可）

```bash
export LLM_API_KEY=sk-...                      # 唯一必填；设置即启用
export LLM_BASE_URL=https://api.openai.com/v1  # 可选，任意 OpenAI 兼容端点
export LLM_MODEL=gpt-4o-mini                   # 可选
export LLM_TIMEOUT=30                          # 可选，秒
```

行为（与 `analysis/llm.py` / `analysis/engine.py` 核对）：
- 分工：LLM 只产出判断字段（`risk_score` / `risk_level` / `rebalance` / 中文 `summary`）；全部组合数学（USD 市值、权重）由 engine 确定性计算并作为 ground truth 注入 prompt，连同实际使用的 FTSO 价格。
- 容错：任何失败（网络 / HTTP 错误 / 输出非 JSON / schema 校验失败）自动重试**一次**；HTTP 400 时第二次请求会去掉 `response_format` JSON mode（兼容部分网关）。再失败则回退规则引擎，响应 `analysis_mode="rule-fallback"` 且 summary 追加「LLM 分析不可用，已回退至规则引擎」。
- 输出契约：成功时 `analysis_mode="llm"`；两种路径输出 schema 完全一致，前端无需区分处理。

### 4.2 信任模型注意事项（重要）

- **ECIES 保护的是「浏览器 ↔ TEE」链路，不覆盖「TEE → LLM provider」链路。** prompt 内含用户持仓、市值、权重——即这些数据会离开 enclave 边界、披露给 LLM API 提供方。这与「Not even we can see your data」的端到端叙事存在张力，必须在产品文档中如实说明。
- **API key 存放**：`LLM_API_KEY` 只存在于 TEE 进程 env，不下发给前端；但在云厂商环境注入的场景下，密钥机密性依赖宿主/secret 管理设施。
- **缓解选项**：
  a. 在 enclave 内自托管开源模型（如量化后的本地 LLM），数据不出 enclave——成本最高、信任最优；
  b. 使用提供机密推理（confidential inference）的 API 服务；
  c. 接受披露权衡，仅发送聚合后的组合数据（当前实现即如此：不发送地址、交易历史等身份关联信息），并在隐私政策中声明。
- **确定性兜底**：无论 LLM 是否可用，服务始终可用（规则引擎兜底），且响应以 `analysis_mode` 如实标注走了哪条路径。

---

## 5. 故障排查表

| 报错/现象 | 原因 | 解决 |
|---|---|---|
| hardhat `Unsupported network "..."` | `deploy.ts` 只支持 `localhost` / `coston2` | `--network localhost` 或 `--network coston2` |
| coston2 部署报 insufficient funds / 无签名账户 | `contracts/.env` 缺 `PRIVATE_KEY`，或账户无 C2FLR | 配置 `PRIVATE_KEY`；去 https://faucet.flare.network 领测试币 |
| `FtsoV2 not found in FlareContractRegistry`（deploy 阶段） | RPC 异常或网络非 Flare 系 | 检查 RPC 连通性；确认 `--network coston2` |
| tee 日志 `Relayer NOT configured ... onchain_submitted will be false` | `PRIVATE_KEY` 未设或 `config/contract-addresses.json` 缺失/零地址 | 设置 relayer 私钥；重新跑 `deploy.ts` 生成地址文件 |
| tee 日志 `EPHEMERAL dev key` 警告 | `TEE_PRIVATE_KEY` 未设，进程临时密钥 | 生产必设固定 `TEE_PRIVATE_KEY`；**每次重启换钥后链上登记即失效**，需重新 `rotateTeeKey` |
| 链上 `submitResult` revert：`attestation failed` | 签名者 ≠ 链上 `teeAddress`（TEE 换钥/登记未做）；或签名非 65 字节 | 确认 tee 日志打印的 `TEE address` == 链上 `teeAddress`；执行 §2.4 登记 |
| tee 日志 `WARNING: on-chain submitResult failed: ...` | relayer 余额不足 / RPC 故障 / nonce 冲突 | 不影响 `/analyze` 响应（`onchain_submitted=false`）；检查 relayer 余额与 RPC |
| 结果 `{"status":"error","error":"price provider failed: ..."}` | FTSO RPC 不可达 / feed 数据异常（策略：绝不回退假价） | 检查 `RPC_URL` 与网络；本地开发设 `ANALYSIS_OFFLINE=1`（会标注 `offline-fixture`） |
| 结果 `price_source="offline-fixture"` 但以为是真实价 | `ANALYSIS_OFFLINE=1` 仍在 env 中（compose 默认值为 1） | 生产/联机环境取消该变量（compose：`ANALYSIS_OFFLINE=0 docker compose up`） |
| `unknown symbol(s) [...]` | 仅支持 BTC / ETH / FLR 三个 feed | 持仓限制在支持币种内，或扩展 `FEED_IDS` |
| `analysis_mode="rule-fallback"` 且已配 LLM | LLM key 无效 / 端点不可达 / 输出校验失败（已自动重试一次） | 查 tee 日志 LLMError；验证 key 与 `LLM_BASE_URL`；部分网关不支持 JSON mode（已自动兼容） |
| 前端抛 `NEXT_PUBLIC_PROJECT_ID is not defined` | `.env.local` 缺失或未填 | `cp .env.example .env.local` 并填入 WalletConnect project id |
| `POST /analyze` 400 `ECIES decryption failed` | 密文非发给当前 TEE 公钥（TEE 换钥后前端用了旧公钥），或线格式不符 | 前端重新 `GET /public-key` 并加密；确认两端 ecies 库版本 |
| `POST /analyze` 400 `client_pubkey must be 65B...` | 明文 payload 缺 `client_pubkey` 或格式错误 | 按协议：`04` 前缀、130 字符 hex、不带 `0x` |
| docker compose 卡在 deploy 服务 | 首次 `npm install` 慢（healthcheck `start_period: 180s`） | 等待；`docker compose logs deploy` 查看；网络差时重试 |
| 端口冲突（3000 / 8000 / 8545） | 本地已有服务占用 | 关闭占用进程或改 compose/启动端口 |
| Windows 下 `uvicorn` 找不到 | 依赖未装或不在 venv | `pip install -r requirements.txt`；用 `python -m uvicorn main:app --port 8000` |

---

## 6. 遗留外部依赖（无法在本仓库内闭环）

| 事项 | 阻塞原因 | 入口 |
|---|---|---|
| Coston2 真实部署 | 需用户私钥 + faucet 测试币 | §2 |
| GCP Confidential Space vTPM attestation | 需 GCP TEE 环境与项目配置 | §3 |
| 真实 LLM 调用 | 需 `LLM_API_KEY` | §4 |
| WalletConnect project id | 需 WalletConnect Cloud 账号 | §1.3 |
