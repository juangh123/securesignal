# SecureSignal 开发修复计划

> 基于 2026-07-18 完成度审计（总完成度约 30–35%）。目标：打通端到端真实链路，落地核心安全价值，工程化收尾。
>
> **状态总览（2026-07-19 更新）**：Stage 1 ✅ 完成（合约安全修复 / ECIES 端到端加密 / 结果上链 relayer）；Stage 2 ✅ 完成（本地端到端验证 23/23 断言通过）；Stage 3 ✅ 完成（工程化收尾，见下文验收勾选）；Stage A ✅ 完成（LLM 分析引擎）；Stage B ✅ 完成（FTSO 真实读价联机实测）。**仅剩外部依赖事项**（Coston2 真实部署 / GCP Confidential Space / 真实 LLM key），接入手册见 `docs/deployment.md`。

## 统一加密协议规范（所有 Worker 必须严格遵守）

**算法**：secp256k1 ECIES = 临时密钥 ECDH → HKDF-SHA256 → AES-256-GCM
**实现库**：前端 `eciesjs`（npm）、后端 `eciespy`（pip）。两者同作者、线格式互通，禁止手搓。
**线格式**（与 eciesjs/eciespy 默认一致）：`65B 未压缩临时公钥(0x04前缀) || 16B nonce || 16B GCM tag || 密文`，整体 base64 传输。
**密钥格式**：公钥 = 65 字节未压缩点 hex（含 `04` 前缀，不带 `0x`）；私钥 = 32 字节 hex。

**请求/响应协议**：
1. 浏览器生成会话密钥对（每次会话）
2. `GET /public-key` → TEE 公钥 hex；前端与链上 `activeTeePublicKey` 交叉校验
3. 前端构造明文 JSON：`{ "client_pubkey": "<会话公钥hex>", "holdings": {...}, "risk_profile": "..." }` → `eciesjs.encrypt(teePubHex, ...)` → base64 → `POST /analyze { task_id, encrypted_data }`
4. TEE：`eciespy.decrypt(tee_priv, ...)` → 分析 → 结果 JSON → `eciespy.encrypt(client_pubkey, result)` → base64
5. TEE 计算 `result_hash = keccak256(result_json)`，用 TEE 签名私钥签名 `(task_id, result_hash)` 作为 attestation，并以 relayer 身份调用 `submitResult(task_id, result_hash, attestation)` 上链
6. 响应：`{ task_id, encrypted_result, attestation, result_hash, onchain_submitted }`；前端用会话私钥解密、并可对链校验 result_hash

**Attestation（开发期诚实实现）**：结构化 JSON `{ result_hash, task_id, image_digest, tee_address, timestamp, mode: "dev-simulated" }` + TEE secp256k1 签名。合约端 `_verifyAttestation` 用 ecrecover 校验签名者 == 登记的 `teeAddress`。生产接 GCP Confidential Space 的真实 JWT 留 TODO 注释。

## 阶段划分

### Stage 1 — 并行修复（3 个 coder 同时开工，目录互不重叠）

| Worker | 范围 | 任务 |
|---|---|---|
| A 合约修复工程师 | `contracts/` | ① `rotateTeeKey` 加 `onlyOwner`，同步登记 `teeAddress`（签名者地址）② `_verifyAttestation` 改 ecrecover 校验 TEE 签名 ③ `submitResult` 校验恢复地址 == teeAddress ④ 补测试：权限拒绝、错误签名拒绝、完整 happy path ⑤ 删除 `hardhat.config.js` 残留 ⑥ 修 `deploy.ts`：支持 localhost/coston2 参数，FTSO 地址从 FlareContractRegistry 解析，部署后自动回写 `frontend/src/config/contract-addresses.json` 与 `tee-service/config/contract-addresses.json` ⑦ 用本地依赖跑通 `npx hardhat test`（解决 HHE22） |
| B TEE链路工程师 | `tee-service/` | ① `crypto/keys.py` 改 secp256k1 ECIES（eciespy，私钥来自环境变量 `TEE_PRIVATE_KEY`，无则临时生成并警告）② `main.py` 按上方协议实现解密/回加密/result_hash/attestation/relayer 上链 ③ `attestation/vtpm.py` 结构化 token + secp256k1 签名，诚实标注 dev-simulated ④ `analysis/engine.py`：FTSO 读取移除静默 fallback——失败要报错；仅当显式 `ANALYSIS_OFFLINE=1` 时用 fixture 价且响应标注 `price_source` ⑤ 实现 `flare/contracts.py`：web3 调 `submitResult`（relayer，私钥来自 `PRIVATE_KEY` env）⑥ 修复 tee.log 所示"系统找不到指定的路径"启动问题 ⑦ 验证 `uvicorn main:app` 能启动、/public-key 与 /analyze 可用（用 eciespy 自加密测试）⑧ requirements.txt 增加 eciespy 等依赖 |
| C 前端加密工程师 | `frontend/` | ① `utils/crypto.ts` 用 eciesjs 实现真实 ECIES（encrypt/decrypt + 会话密钥对管理）② `page.tsx`：按协议发请求（明文内含 client_pubkey）、用链上事件取真实 taskId、解密响应并展示结果、显示 attestation 与 result_hash ③ TEE 地址、projectId 等改环境变量（`NEXT_PUBLIC_*`）④ 删除敏感 console.log ⑤ 删除 test-wc*.js、test-ws.js 探测脚本与误建的 `E:\AI WORK\...\frontend` 零字节文件 ⑥ 交叉校验 TEE 公钥与链上值 ⑦ 确保 `npm run build` 通过 ⑧ 生成跨端测试向量：用 node + eciesjs 加密固定明文存 `frontend/e2e/test-vector.json`（含明文、TEE测试私钥、密文），供后端/集成验证解密 |

**共享约束**：加密协议以本文件为准；两端密钥格式、线格式必须逐字节兼容；Stage 2 将用 test-vector.json 做交叉解密验证，不兼容即返工。

### Stage 2 — 端到端集成验证（1 个 coder，Stage 1 全部通过后启动）

集成验证工程师：① 起本地 hardhat node → 跑 deploy.ts → 确认两份 contract-addresses.json 被真实地址覆盖 ② 配置 env 起 tee-service ③ 用 Node 脚本模拟前端完整流程：eciesjs 加密 → POST /analyze → 解密响应 → 校验 result_hash 与链上 submitResult 记录一致 ④ 用 test-vector.json 验证 Python 能解 JS 密文、JS 能解 Python 密文（双向）⑤ 输出逐项通过/失败的验证报告。**只验证与做小修（路径、env、配置），不改架构；发现结构性问题精确定位报告，由 Orchestrator 返工对应 Worker。**

### Stage 3 — 工程化收尾（1 个 coder，Stage 2 通过后启动）✅ 已完成（2026-07-19）

交付收尾工程师：① `tee-service/Dockerfile` 锁定基础镜像 digest + `pip install --require-hashes` ✅（digest 经 Registry API 查实；`requirements-lock.txt` 77 包 sha256 锁定，目标 linux/amd64 py3.11）② `docker-compose.yml` 增加 deploy 初始化服务（hardhat 健康检查 → deploy.ts → setup-tee.ts → tee-service/frontend 依赖启动）✅，修复无效 env（移除无人消费的 `RUNNING_IN_TEE`、移除覆盖真实值的 `NEXT_PUBLIC_PROJECT_ID=dummy`，tee-service 补齐 `ANALYSIS_OFFLINE/RPC_URL/TEE_PRIVATE_KEY/PRIVATE_KEY/FTSO_READER_ADDRESS/TEE_IMAGE_DIGEST` 透传）✅ ③ 根 `README.md` 如实更新：真实完成状态、五步本地运行指南、dev-simulated 诚实标注、Live Demo 标注待部署 ✅ ④ 更新 `docs/architecture.md` 与实现对齐（第 5/6 步按实现重写、Next.js 16 等事实修正）✅ ⑤ 全项目 TODO/占位符最终扫描 ✅（无假数据残留，合法 TODO 保留并登记）⑥ 更新本 plan.md 状态为完成 ✅

## 验收标准
- [x] `npx hardhat test` 全绿（含权限/签名负例）— Stage 1 完成
- [x] 本地 hardhat 部署后两份地址配置为真实地址 — Stage 2 验证
- [x] test-vector.json 双向交叉解密成功 — Stage 2 验证
- [x] 模拟前端脚本 → /analyze → 解密成功 → 链上 result_hash 一致 — Stage 2 验证（23/23 断言）
- [x] FTSO 失败时服务报错而非返回假价 — Stage 1 engine 策略（仅 `ANALYSIS_OFFLINE=1` 显式使用 fixture 且标注 `price_source`）
- [x] `npm run build` 通过 — Stage 1 完成
- [x] README 无虚假声明 — Stage 3 完成（真实实现与 dev-simulated 分列，Live Demo 标注待部署）
- [x] LLM 未配置时规则引擎兜底且如实标注 `analysis_mode` — Stage A 验证
- [x] FTSO 联机读价成功且价格新鲜（feed ts < 24h）— Stage B 验证（2026-07-19 实测）

## Stage A — LLM 分析引擎 ✅ 已完成（2026-07-19）

**范围**：`tee-service/analysis/llm.py`（新建）、`analysis/engine.py`（接入）。

完成项：
- OpenAI 兼容 API 直连（`requests`，无 openai SDK 依赖）；env 仅 `LLM_API_KEY` 必填启用，`LLM_BASE_URL`（默认 `https://api.openai.com/v1`）、`LLM_MODEL`（默认 `gpt-4o-mini`）、`LLM_TIMEOUT`（默认 30s）可选。
- 分工边界：LLM 只产出判断字段（`risk_score` / `risk_level` / `rebalance` / 中文 `summary`）；组合数学（USD 市值、权重）由 engine 确定性计算并作为 ground truth 注入 prompt，连同实际使用的 FTSO 价格。
- 容错：失败重试一次（HTTP 400 时第二次去掉 `response_format` JSON mode 以兼容部分网关）；仍失败回退规则引擎。两条路径输出 schema 完全一致，以 `analysis_mode: "llm" | "rule-fallback"` 如实标注。
- 输出校验：risk_score 0–100 整数、risk_level/rebalance action 枚举、symbol 大写归一化；非法输出视为失败走重试/回退。
- 回归验证：LLM 未配置时服务正常起、规则引擎输出不变（见 `tee-service/uvicorn-llm-test.log`、`uvicorn-regression.log`）。

## Stage B — FTSO 真实读价 ✅ 已完成（2026-07-19）

**范围**：`tee-service/analysis/price_provider.py`（新建直读官方 FtsoV2）、`analysis/test_price_provider.py`（单测）。

完成项：
- 在线模式：经 FlareContractRegistry（`0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019`）解析链上官方 `FtsoV2`，`getFeedById(bytes21)` 读 BTC/ETH/FLR 三个 feed，`value / 10^decimals` 换算；10s RPC 超时、60s TTL 缓存。
- 失败策略：任何网络/RPC/数据异常抛 `PriceProviderError`，**无静默回退假价**；未知 symbol 在任何网络访问前抛 `ValueError`。
- 离线模式：仅 `ANALYSIS_OFFLINE=1` 用 fixture 价（BTC 65000 / ETH 3500 / FLR 0.02），`price_source="offline-fixture"` 明确标注非真实市价。
- 单测：offline 5 例 + mocked online 14 例全过；联机用例 `ANALYSIS_LIVE_TEST=1` 门控。
- **联机实测（2026-07-19，`LiveCoston2Tests` 通过，完整输出见 `tee-service/ftso-live-test.log`）**：BTC/USD $64,649.78、ETH/USD $1,866.52、FLR/USD $0.006560；feed 时间戳 2026-07-19 04:03 UTC（新鲜度秒级）；`price_source="coston2-ftso"`。

## 遗留外部依赖清单（本地无法闭环，接入手册：`docs/deployment.md`）

| 事项 | 阻塞原因 | 手册章节 |
|---|---|---|
| ~~Coston2 真实部署~~ ✅ **已完成 2026-07-19**：AnalysisRegistry `0xfA3126Ca8f6F4CEc3cf3a6266B9cd71d4B7fB531`、FtsoV2Reader `0xe60745669C54b66F67ae85Ce031D4bDED4311163`、登记 TEE `0xEe4975C290FBF46757A1D90F02c3CF555163556E`；生产冒烟测试 12/12 通过（`frontend/e2e/e2e-coston2.mjs`，真实 FTSO 喂价 + 链上 Verified） | 已完成 | deployment.md §2.5 |
| GCP Confidential Space vTPM attestation | 需 GCP TEE 环境；改造锚点：`tee-service/attestation/vtpm.py` docstring TODO、`AnalysisRegistry.sol` `_verifyAttestation` TODO | deployment.md §3 |
| 真实 LLM 调用 | 需 `LLM_API_KEY`（信任模型注意事项见手册 §4.2） | deployment.md §4 |
| ~~`scripts/setup-tee.ts` 生产化~~ ✅ 已完成：脚本已网络感知（localhost 用 dev key，其余网络走 `TEE_PRIVATE_KEY`/`TEE_IMAGE_DIGEST` env） | 已完成 | deployment.md §2.4 |
| WalletConnect project id | 需 WalletConnect Cloud 账号（前端必填 env） | deployment.md §1.3 |
| Live Demo（App / 视频 / 主网合约） | 依赖上述部署完成 | README「Live Demo」节 |
