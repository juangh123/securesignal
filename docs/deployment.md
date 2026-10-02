# SecureSignal 部署手册

> 适用范围：Coston2 testnet 部署、GCP Confidential Space 生产化接入、LLM 分析引擎启用。
> 本文所有命令、路径、环境变量名均已与代码逐一核对（`tee-service/main.py`、`analysis/llm.py`、`analysis/price_provider.py`、`flare/contracts.py`、`attestation/vtpm.py`、`contracts/scripts/*.ts`、`contracts/hardhat.config.ts`、`docker-compose.yml`、`frontend/.env.example`）。
> 最后核对日期：2026-10-01。

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
| `TEE_PRIVATE_KEY` | 生产**必填** | 未设且 `ENV=prod`：启动直接抛错（**fail-closed**，禁止临时密钥兜底）；仅 dev 模式允许生成进程内临时密钥并打印醒目警告（重启即换钥，链上登记随之失效） | TEE 的 secp256k1 私钥（ECIES 解密 + attestation 签名共用），32 字节 hex，可带 `0x` 前缀。见 `crypto/keys.py` 与 `crypto/test_keys.py` | `0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d`（hardhat account #1，**仅本地**） |
| `PRIVATE_KEY` | 可选 | 未设：relayer 关闭，响应 `onchain_submitted=false`，启动日志打印 `Relayer NOT configured` | 结果上链 relayer 账户私钥（付 gas 调 `submitResult`）。见 `flare/contracts.py` | `0xac0974...f4f2ff80`（hardhat account #0，**仅本地**） |
| `RPC_URL` | 可选 | `https://coston2-api.flare.network/ext/C/rpc`（relayer 与 FTSO 读价共用同一默认值） | EVM JSON-RPC 端点 | `http://127.0.0.1:8545`（本地） |
| `ANALYSIS_OFFLINE` | 可选 | 未设 = 在线模式（真实 FTSO 读价） | 恰好等于 `"1"` 时启用 dev fixture 价（BTC 65000 / ETH 3500 / FLR 0.02，**非真实市价**），结果标注 `price_source="offline-fixture"`。见 `analysis/price_provider.py` | `1` |
| `LLM_API_KEY` | 可选 | 未设：LLM 关闭，使用确定性规则引擎（`analysis_mode="rule-fallback"`） | OpenAI 兼容 API key；设置即启用 LLM 分析。见 `analysis/llm.py` | `sk-...` |
| `LLM_BASE_URL` | 可选 | `https://api.openai.com/v1` | 任意 OpenAI 兼容端点（DeepSeek / Moonshot / 本地 mock 等） | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 可选 | `gpt-4o-mini` | 模型名 | `deepseek-flash` |
| `LLM_TIMEOUT` | 可选 | `20` | 单次 LLM HTTP 请求超时（秒）；非数字时回退 20。见 `analysis/llm.py` | `20` |
| `LLM_TOTAL_BUDGET` | 可选 | `35` | 两次尝试共享的墙钟预算（秒）；剩余预算不足 1 秒时不再发起重试。为避免打穿 CloudFront 60 秒源站读超时，取值被硬性钳制在 45 秒以内 | `35` |
| `ANALYZE_REQUIRE_ONCHAIN_TASK` | 可选 | 未设 = 开启（`"0"` 关闭） | `/analyze` 是公开无鉴权端点，启用 LLM 后每次调用都产生费用。开启时只分析链上真实处于 `Requested` 的任务，刷接口必须先付 C2FLR gas 注册任务。未配置 relayer（读不到 registry）时门禁自动失效 | `0` |
| `ANALYZE_TASK_MAX_AGE_SECONDS` | 可选 | `900`（`0` = 不限时） | 任务时效窗口：只分析 `requestedAt` 在窗口内的 `Requested` 任务。防止长期卡在 `Requested` 的僵尸任务被当成免费 LLM 触发器反复调用 | `3600` |
| `TEE_IMAGE_DIGEST` | 生产推荐 | `dev` | 期望的 Confidential Space workload 镜像 digest；生产必须与 JWT 的 `submods.container.image_digest` 一致，否则 attestation 失败。见 `attestation/vtpm.py` | `sha256:<64 hex>` |
| `GCP_ATTESTATION_AUDIENCE` | 生产可选 | `https://securesignal.app` | Confidential Space OIDC token 的自定义 audience；验证方必须使用同值。 | `https://securesignal.app` |
| `GCP_ATTESTATION_SOCKET` | 可选 | `/run/container_launcher/teeserver.sock` | Confidential Space launcher 暴露的 Unix socket。 | 默认值 |
| `GCP_SECRETS_ENABLED` | GCP 部署必填 | `0` | 设为 `1` 时，启动脚本先经 Secret Manager 加载下方映射；缺 mapping 会 fail-closed。 | `1` |
| `GCP_SECRET_TEE_PRIVATE_KEY` | GCP Secret Manager 模式必填 | 无 | `TEE_PRIVATE_KEY` 对应的 Secret Manager secret resource name（不是值）。 | `securesignal-tee-private-key` |
| `GCP_SECRET_PRIVATE_KEY` | GCP Secret Manager 模式必填 | 无 | relayer `PRIVATE_KEY` 对应的 Secret Manager secret resource name。 | `securesignal-relayer-private-key` |
| `GCP_SECRET_LLM_API_KEY` | 可选 | 无 | `LLM_API_KEY` 对应的 Secret Manager secret resource name；留空则不启用 LLM secret 拉取。 | `securesignal-llm-api-key` |
| `ATTESTATION_PROVIDER` | 生产必填 | `dev-simulated`（本地）；`ENV=prod` 时默认 GCP | `dev-simulated` / `gcp-confidential-space` / `aws-nitro-enclaves`。 | `aws-nitro-enclaves` |
| `AWS_NITRO_ENCLAVES` | AWS 模式兼容开关 | `0` | 设为 `1` 时也选择 AWS Nitro attestation provider。 | `1` |
| `AWS_NITRO_PCR0` | AWS 生产必填 | 无 | EIF 构建输出的 PCR0；父实例启动时写入 runtime bundle，并随 `nsm_document` 返回。 | `<96 hex>` |
| `AWS_NITRO_PCR1` / `AWS_NITRO_PCR2` | AWS 生产可选 | 无 | EIF 构建输出的 PCR1/PCR2，随 attestation token 返回。 | `<96 hex>` |
| `AWS_NSM_HELPER` | 可选 | `/usr/local/bin/nsm-attest` | 镜像内调用 `/dev/nsm` 的 helper 路径。 | 默认值 |
| `AWS_NITRO_SECRET_PORT` | 可选 | `8001` | 父实例通过 vsock 给 enclave 注入 runtime bundle 的端口。 | `8001` |
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

> `scripts/setup-tee.ts` 已支持网络感知：**localhost/hardhat 网络**自动使用 hardhat dev account #1（仅本地）；**其他网络（如 coston2）**必须从 env 读取 `TEE_PRIVATE_KEY`（0x + 64 hex，与 tee-service 的 `TEE_PRIVATE_KEY` 一致），可选 `TEE_IMAGE_DIGEST`（bytes32 hex，缺省为 `keccak256("dev-image")` 占位，生产应改为真实镜像 digest，见 §3.3）：
>
> ```bash
> TEE_PRIVATE_KEY=0x<TEE私钥> npx hardhat run scripts/setup-tee.ts --network coston2
> ```
>
> 脚本会回读链上 `activeTeePublicKey` / `expectedImageDigest` / `teeAddress` 并逐一比对。
> 也可用任意 web3 工具（ethers 脚本 / explorer 写合约）以 owner 身份直接调 `rotateTeeKey`：
>
> - `newPublicKey`：TEE 公钥（65 字节未压缩 hex，`0x04` 前缀；服务启动日志 `[main] TEE public key:` 会打印）
> - `newImageDigest`：`bytes32` 镜像 digest
> - `newTeeAddress`：TEE 私钥对应地址（启动日志 `[main] TEE address:` 会打印）

### 2.5 已部署实例（Coston2）

当前生效部署（与 `frontend/src/config/contract-addresses.json`、`tee-service/config/contract-addresses.json` 一致，也是线上前端与 TEE 服务指向的实例）：

| 项 | 值 |
|---|---|
| AnalysisRegistry | `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482` |
| FtsoV2Reader | `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a` |
| 登记 TEE 地址 | `0xEe4975C290FBF46757A1D90F02c3CF555163556E` |
| 链上 `expectedImageDigest` | `0x139c95b7fe1e269feaa9290b9c8e902553bfeda8875631afe705515d2180ca52` = `keccak256(AWS Nitro PCR0)`（2026-10-02 通过 `rotateTeeKey` tx `0x1de5dcd04bee67f6039d83b1efb139275b2252b1ce909bd374bed4d1fd5064c5` 提交；PCR0 为 48 字节，链上只能存 bytes32 承诺。合约仍只做 EIP-191 验签，不做链上 NSM 验证，真实度量由 NSM document + 链下 verifier 校验，见 §3.5） |
| 最近成功任务 | task #18，2026-10-02，链上 `status=Verified`（AWS Nitro Enclave 运行，证据见 `deliverables/aws-nitro-attestation-18.json`） |
| 最近公开 Demo 冒烟 | task #19，2026-10-02，链上 `status=Verified`（Render `dev-simulated` + 真实 DeepSeek `analysis_mode="llm"` + 批量 FTSO，12/12；证据见 `deliverables/coston2-smoke-task-19.json`） |
| 冒烟测试 | `frontend/e2e/e2e-coston2.mjs`（真实 FTSO 喂价 `price_source="coston2-ftso"`、ecrecover == TEE 地址、链上 status=Verified） |

> 早期部署（2026-07-19 首次上线）为 `AnalysisRegistry 0xfA3126Ca8f6F4CEc3cf3a6266B9cd71d4B7fB531` / `FtsoV2Reader 0xe60745669C54b66F67ae85Ce031D4bDED4311163`，已被上表部署取代，勿再引用。

脚本执行后会回读链上 `activeTeePublicKey` / `expectedImageDigest` / `teeAddress` 做一致性校验。

### 2.6 启动 tee-service（生产 env）

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

### 2.7 前端 `.env.local`

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

### 2.8 部署验证清单

| # | 检查项 | 通过标准 |
|---|---|---|
| 1 | `curl https://<tee>/public-key` | 返回公钥 == 链上 `activeTeePublicKey`（去 `0x` 后逐字符一致） |
| 2 | tee-service 启动日志 | 出现 `Relayer configured`；无 `EPHEMERAL dev key` 警告 |
| 3 | 前端连接钱包（Coston2）→ 提交持仓 | `requestAnalysis` 交易在 explorer 可查，事件 `AnalysisRequested` 给出 `taskId` |
| 4 | `POST /analyze` 响应 | `onchain_submitted=true`；`price_source="coston2-ftso"`；`prices_used` 为正值真实价 |
| 5 | 链上核对 | explorer 上 `tasks(taskId).status == 3 (Verified)`，`resultHash` == 响应 `result_hash`；`ResultSubmitted` 事件可查 |
| 6 | 前端解密展示 | 会话私钥解密 `encrypted_result` 成功，显示 risk_score / rebalance / summary；`analysis_mode` 为 `llm` 或 `rule-fallback`（两者皆合法） |
| 7 | attestation | token JSON 中 `tee_address` == 链上 `teeAddress`，`mode` 字段如实标注：公共 Render Demo 为 `dev-simulated`；AWS Nitro 生产路径为 `aws-nitro-enclaves`，并带 NSM document、PCR0/PCR1/PCR2 与 `nsm_document_sha256`（见 §3.5） |
| 8 | `curl https://<tee>/health` | 返回 `status:"ok"`；`relayer_configured` / `llm_configured` / `price_mode` / `attestation_mode` 与实际部署一致（响应不含任何密钥） |
| 9 | `curl https://<tee>/assets` | 返回可定价资产清单（当前 31 个，含 BTC/ETH/FLR）；前端用它拦截不支持的 symbol，避免用户为必然失败的请求付 gas |
| 10 | `POST /analyze` 传一个不存在的 taskId | 返回 `409 not pending on-chain`（除非显式设了 `ANALYZE_REQUIRE_ONCHAIN_TASK=0`） |

---

## 3. GCP Confidential Space 接入

### 3.1 当前实现

`tee-service/attestation/vtpm.py` 现在同时支持两条路径：

- `ENV != prod`：返回 `mode: "dev-simulated"` 的结构化 JSON + TEE secp256k1 签名。
- `ENV=prod`：通过 Confidential Space launcher 的 Unix socket
  `/run/container_launcher/teeserver.sock` 向 `http://localhost/v1/token`
  发 `POST`，请求 Google 签名的 OIDC attestation JWT。请求包含自定义
  audience 与绑定 `(task_id, result_hash)` 的 `eat_nonce`；返回 token 同时带有链上
  ecrecover 所需的 EIP-191 签名。

生产模式是 fail-closed：launcher socket 不存在、token endpoint 失败、audience/nonce
不匹配或 `TEE_IMAGE_DIGEST` 与 JWT 的
`submods.container.image_digest` 不一致时，服务会报错而不会降级为
`gcp-confidential-space` 伪 attestation。

完整部署脚本和操作步骤见
[`deploy/gcp/README.md`](../deploy/gcp/README.md)。脚本会：

1. 用 Cloud Build 构建 `linux/amd64` 镜像并取得 `sha256:<digest>`。
2. 把私钥写入 Secret Manager，以 resource name 形式传给 VM。
3. 容器启动时由 `gcp_secrets_bootstrap.py` 通过 workload service account
   拉取实际值，避免私钥进入 Confidential Space attestation JWT 的
   `container.env` claims。
4. 以 `tee-image-reference=<image>@<digest>` 创建/更新 Confidential Space VM。

### 3.2 部署

```powershell
gcloud auth login
gcloud config set project PROJECT_ID
.\deploy\gcp\deploy-confidential-space.ps1 -ProjectId PROJECT_ID
```

默认创建 `us-central1-a` 下的 `n2d-standard-2` Confidential Space VM，并开放
`tcp:8000` 供首轮联调。生产前端应改用 HTTPS 负载均衡或受控反向代理。

### 3.3 验证 JWT 与登记链上 digest

使用仓库自带 verifier 校验 Google OIDC 签名链与关键 claims：

```bash
cd tee-service
python tools/verify_confidential_space_token.py \
  --audience https://securesignal.app \
  --nonce <attestation_nonce> \
  --image-digest sha256:<digest> \
  "<jwt>"
```

把 `sha256:<64 hex>` 或等价的 `0x<64 hex>` 传给登记脚本：

```bash
cd contracts
TEE_PRIVATE_KEY=0x<TEE私钥> \
TEE_IMAGE_DIGEST=sha256:<digest> \
npx hardhat run scripts/setup-tee.ts --network coston2
```

当次黑客松/首期上线采用「链下验证 JWT + owner 调用 `rotateTeeKey`」方案；
合约仍只验证 EIP-191 签名和登记地址，因此登记流程的审计证据必须保存。
链上直接验证 JWT 或接入 Flare attestation verifier 属于后续增强。

### 3.4 信任边界说明

当前部署从 Secret Manager 注入固定 `TEE_PRIVATE_KEY`。JWT 可以证明 workload
镜像/VM 运行在 Confidential Space，但**不证明该 secp256k1 私钥是在 enclave 内生成
或由 KMS 解封的**。要达到更完整的密钥来源证明，需要后续改用 enclave 内生成密钥，
或通过 Confidential Space attestation + KMS key release 解封密钥。

### 3.5 AWS Nitro Enclaves（无 GCP 可用时的生产路径）

`ATTESTATION_PROVIDER=aws-nitro-enclaves` 时，服务通过镜像内的
`nsm-attest` helper 调用 `/dev/nsm`，为每次 `(task_id, result_hash)` 请求
NSM attestation document：

- `user_data` = `abi.encodePacked(task_id, result_hash)` 的原始 64 字节；
- `nonce` = `sha256(task_id + ":" + result_hash)`；
- `public_key` = 当前 TEE ECIES 公钥（65 字节未压缩点）；
- token 额外返回 `nsm_document`、`nsm_document_sha256`、PCR0/PCR1/PCR2。

部署与验证：

```powershell
aws configure sso
$env:AWS_PROFILE = "<profile>"
aws sts get-caller-identity

.\deploy\aws\deploy-nitro-enclaves.ps1 `
  -Region us-east-1 `
  -ApiCidr "<your IP>/32"

python tee-service\tools\verify_aws_nitro_attestation.py `
  --nonce-hex <nsm_nonce> `
  --user-data-hex <nsm_user_data> `
  --public-key-hex <TEE public key> `
  --pcr0 <pcr0> `
  <nsm_document>
```

当前已验证部署（2026-10-02）：

| 项目 | 值 |
|---|---|
| 区域 / 父实例 | `us-east-1` / `i-08c3255e1c96ae343` |
| Enclave | `i-08c3255e1c96ae343-enc01a0fc912c2bd846`，非 debug（`Flags: NONE`），`attestation_mode="aws-nitro-enclaves"` |
| API | `https://d1tubqcwiwwev5.cloudfront.net`（CloudFront HTTPS，源站只接受 CloudFront 前缀列表 + 操作员 IP） |
| PCR0 | `c126dc6db19cefcda5c0a412fecd692d5f12d801cf5ea5b262d954424a455cf25e6189ace615ad00be541d8295864279` |
| PCR1 | `4b4d5b3661b3efc12920900c80e126e4ce783c522de6c02a2a5bf7af3a2b9327b86776f188e4be1c1c404a129dbda493` |
| PCR2 | `ee61bc92db0b07d247c054e0402bea829d6272d7d6427892de7ef678e367081f5ee21c2d3eefdbacf84c71ceaf677bfb` |
| 端到端验证 | Coston2 冒烟测试 `12/12`；task 20 链上 `Verified`；证据 `deliverables/aws-nitro-attestation-20.json` + 独立 NSM 验证 |
| 链上测量承诺 | `keccak256(PCR0)` = `0x139c95b7fe1e269feaa9290b9c8e902553bfeda8875631afe705515d2180ca52`（`rotateTeeKey` tx `0x1de5dcd04bee67f6039d83b1efb139275b2252b1ce909bd374bed4d1fd5064c5`；合约只存承诺，不做链上 NSM 验证） |

> **构建溯源（2026-10-02）**：上述非 debug EIF 由 2.5.0 源码（批量 FTSO、LLM 总预算、
> API 安全响应头）构建。TEE 密钥未变，因此链上 `teeAddress` / `activeTeePublicKey`
> 在重建后仍然有效；任何后续源码改动都需要新 EIF、新 PCR0，并重新验证和更新链上承诺。

enclave 自身只监听 HTTP，且安全组最初只放行操作员 IP。公开 HTTPS 入口使用
`deploy/aws/expose-https-cloudfront.ps1`：脚本会创建或复用 CloudFront 分发，
把 CloudFront origin-facing 托管前缀列表加入安全组，并输出
`https://<distribution>.cloudfront.net`。当前已部署分发 `E3I9PI0XZFXX88`，
HTTPS 端点为 `https://d1tubqcwiwwev5.cloudfront.net`。重新部署时运行：

```powershell
.\deploy\aws\expose-https-cloudfront.ps1 `
  -InstanceId i-08c3255e1c96ae343 `
  -Region us-east-1
```

分发把源站读超时设为 CloudFront 允许的最大值 **60 秒**。链路内部预算与之对齐：
FTSO 对整批资产只发一次 JSON-RPC 批量请求（31 feed 实测 4.88 秒），LLM 阶段受
`LLM_TOTAL_BUDGET`（默认 35 秒）约束，二者相加仍留有足够余量。

AWS 根证书固定在
`tee-service/attestation/aws_nitro_root_g1.pem`，verifier 会校验
COSE_Sign1 ES384 签名、证书链、有效期、nonce/user_data/public_key 与 PCR0。

**信任边界**：首版部署由父实例通过 vsock 转发 runtime secret bundle。
父实例在 enclave 信任边界之外，可以拒绝服务，因此更强方案应把
`TEE_PRIVATE_KEY` 改为 KMS 封存密钥，并用 PCR0/PCR3/PCR8 限制 `kms:Decrypt`。

---

## 4. LLM 接入

> 线上当前使用的供应商是 **DeepSeek**（OpenAI 兼容端点）。`render.yaml` 已把
> `LLM_BASE_URL` / `LLM_MODEL` 默认指向 DeepSeek，`LLM_API_KEY` 为 `sync: false`
> 需在 Render Dashboard 手填。`GET /health` 会返回 `llm_configured` 与
> `llm_model`（只暴露模型名，不含 key/base URL），可直接用来确认是否真的启用成功。

### 4.1 启用（3 个 env 即可）

```bash
export LLM_API_KEY=sk-...                        # 唯一必填；设置即启用
export LLM_BASE_URL=https://api.deepseek.com/v1  # 可选，任意 OpenAI 兼容端点
export LLM_MODEL=deepseek-flash                  # 可选（DeepSeek 当前可用：deepseek-flash / deepseek-v4-pro）
export LLM_TIMEOUT=20                            # 可选，单次尝试超时（秒）
export LLM_TOTAL_BUDGET=35                       # 可选，两次尝试合计预算（秒）
```

行为（与 `analysis/llm.py` / `analysis/engine.py` 核对）：
- 分工：LLM 只产出判断字段（`risk_score` / `risk_level` / `rebalance` / 英文 `summary`）；全部组合数学（USD 市值、权重）由 engine 确定性计算并作为 ground truth 注入 prompt，连同实际使用的 FTSO 价格。
- 容错：任何失败（网络 / HTTP 错误 / 输出非 JSON / schema 校验失败）在总预算允许时自动重试**一次**；HTTP 400 时第二次请求会去掉 `response_format` JSON mode（兼容部分网关）。预算耗尽或再次失败则回退规则引擎，响应 `analysis_mode="rule-fallback"` 且 summary 追加「LLM 分析不可用，已回退至规则引擎」。
- 超时边界：每次请求的超时取 `min(LLM_TIMEOUT, 剩余总预算)`，因此最坏情况下 LLM 阶段不超过 `LLM_TOTAL_BUDGET`。这是为了在 CloudFront（源站读超时最大 60 秒）后面仍能稳定返回；`GET /health` 会暴露实际生效的 `llm_timeout_seconds` / `llm_total_budget_seconds`。
- 预算实测（2026-10-02）：25 资产真实 Coston2 FTSO 批量读 + 模拟 10 秒 LLM，`/analyze` 全路径 **15.23 秒**（`analysis_mode="llm"`，`price_source="coston2-ftso"`）；31 feed 全量批量读 4.88 秒。
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
| `unknown symbol(s) [...]` | 该资产在 Coston2 上没有 FTSO feed。当前支持 31 个（清单见 `GET /assets`，或 `analysis/price_provider.py` 的 `SUPPORTED_SYMBOLS`） | 用 `GET /assets` 返回的符号；新增资产前先用 `getFeedById` 确认链上确有该 feed，再补进 `SUPPORTED_SYMBOLS` + `FIXTURE_PRICES` |
| `analysis_mode="rule-fallback"` 且已配 LLM | LLM key 无效 / 端点不可达 / 输出校验失败（已自动重试一次） | 查 tee 日志 LLMError；验证 key 与 `LLM_BASE_URL`；部分网关不支持 JSON mode（已自动兼容） |
| `POST /analyze` 413 `encrypted_data exceeds the 131072-character limit` | 请求体超过 128 KB 上限（真实组合密文仅数百字节） | 检查客户端是否误传大对象；上限常量见 `main.py` 的 `MAX_ENCRYPTED_DATA_CHARS` |
| `POST /analyze` 400 `payload.holdings must contain at most 25 entries` / `holdings symbol ... is invalid` | 明文持仓超过 25 个，或 symbol 不符合 `[A-Z0-9]{1,12}` | 前端 `parseHoldings` 已镜像同一组上限（见 `analysis/engine.py`）；资产过多时请分批分析 |
| `POST /analyze` 400 `payload.risk_profile must be at most 64 characters` | `risk_profile` 是进入 LLM prompt 的自由文本，已在边界处限长 64 并去除控制字符 | 传短标签（如 `moderate`），不要把长文本塞进该字段 |
| `POST /analyze` 409 `task N is not pending on-chain (status=...)` | 链上任务门禁生效：该 taskId 不存在，或已 `Completed`/`Verified` | 正常客户端先发 `requestAnalysis`，再用返回的 taskId 调用；只有离线开发才设 `ANALYZE_REQUIRE_ONCHAIN_TASK=0` |
| `POST /analyze` 409 `task N was requested Xs ago, which is older than the 900s analysis window` | 该任务发起太早（例如页面挂了一晚上才提交，或僵尸任务） | 重新发一次 `requestAnalysis` 拿新 taskId；确需放宽就调大 `ANALYZE_TASK_MAX_AGE_SECONDS` |
| 前端抛 `NEXT_PUBLIC_PROJECT_ID is not defined` | `.env.local` 缺失或未填 | `cp .env.example .env.local` 并填入 WalletConnect project id |
| `POST /analyze` 400 `ECIES decryption failed` | 密文非发给当前 TEE 公钥（TEE 换钥后前端用了旧公钥），或线格式不符 | 前端重新 `GET /public-key` 并加密；确认两端 ecies 库版本 |
| `POST /analyze` 400 `client_pubkey must be 65B...` | 明文 payload 缺 `client_pubkey` 或格式错误 | 按协议：`04` 前缀、130 字符 hex、不带 `0x` |
| docker compose 卡在 deploy 服务 | 首次 `npm install` 慢（healthcheck `start_period: 180s`） | 等待；`docker compose logs deploy` 查看；网络差时重试 |
| 端口冲突（3000 / 8000 / 8545） | 本地已有服务占用 | 关闭占用进程或改 compose/启动端口 |
| Windows 下 `uvicorn` 找不到 | 依赖未装或不在 venv | `pip install -r requirements.txt`；用 `python -m uvicorn main:app --port 8000` |

---

## 6. 云端托管（方案 A：Vercel + Render，零成本）

前置：仓库已推送到 GitHub（公开）。

### 6.1 TEE 服务 → Render

> 本节是公开 `dev-simulated` Demo 的部署方式（可选 DeepSeek LLM）。
> App 当前实际使用的是 §3.5 的真实 AWS Nitro Enclave CloudFront 端点
> `https://d1tubqcwiwwev5.cloudfront.net`；Render 端点仅作为对照/降级演示。

仓库根含 `render.yaml`（Blueprint）：Docker 运行时、`dockerContext=./tee-service`、
健康检查 `/public-key`、非密钥 env 已预填。

1. render.com 注册（可用 GitHub 登录）→ **New → Blueprint** → 选本仓库
2. 在 Dashboard 手填两个密钥 env（`sync:false`，不会出现在仓库）：
   - `TEE_PRIVATE_KEY` = 生产 TEE 密钥（与 Coston2 链上 `teeAddress` 对应的那把）
   - `PRIVATE_KEY` = relayer/部署账户私钥
3. 部署后得到 `https://securesignal-tee.onrender.com` 形式的 URL，
   访问 `/public-key` 应返回与链上 `activeTeePublicKey` 一致的 130 位 hex。
4. 把 Vercel 域名写进 `ALLOWED_ORIGINS`（见 §6.2 第 4 步）。

> 免费档闲置 15 分钟休眠、冷启动 30–60s：演示前先手动访问一次 `/public-key` 预热。

### 6.2 前端 → Vercel

1. vercel.com 注册（GitHub 登录）→ **Add New → Project** → Import 本仓库
2. **Root Directory 设为 `frontend`**（关键，否则构建失败）
3. 环境变量：
   - `NEXT_PUBLIC_PROJECT_ID` = WalletConnect Cloud project id（同本地 `.env.local`）
   - `NEXT_PUBLIC_TEE_URL` = §6.1 得到的 Render URL
4. Deploy 得到 `https://<项目>.vercel.app`，回 Render 把该域名写入
   `ALLOWED_ORIGINS`（CORS 白名单，多域名逗号分隔），触发 tee 服务重新部署
5. WalletConnect Cloud 后台把 Vercel 域名加入允许列表

### 6.3 托管后复验

- 浏览器打开 Vercel URL → 连接钱包（Coston2）→ 提交分析 → 结果展示
  （`price_source` 应为 `coston2-ftso`）
- 一键只读巡检（推荐）：`node tools/ops-status.mjs`（可用 `FRONTEND_URL` / `TEE_URL` / `RPC_URL` 覆盖默认地址；不发起交易、不改变链上状态）
- 或跑完整端到端冒烟：`TEE_URL=https://<tee> node frontend/e2e/e2e-coston2.mjs`（会发起真实 Coston2 测试网交易）

### 6.4 用 Render API 配置密钥（可选，替代 Dashboard 手填）

Dashboard 的手填步骤可以用 API 自动化完成（适合 CI 或让 agent 代劳）：

```bash
# 1. 取 API key：Render Dashboard → Account Settings → API Keys
export RENDER_API_KEY=rnd_xxxx
AUTH="Authorization: Bearer $RENDER_API_KEY"

# 2. 找到服务 id（name 里的 securesignal-tee）
curl -s -H "$AUTH" -H "Accept: application/json" \
  'https://api.render.com/v1/services?limit=50' | jq -r '.[].service | "\(.id) \(.name)"'

# 3. 写单个 env（无需先读出全部变量，不会覆盖其它变量）
curl -s -X PUT -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"value":"sk-xxxx"}' \
  'https://api.render.com/v1/services/<serviceId>/env-vars/LLM_API_KEY'

# 4. 触发部署（关键：API 改 env 不会自动重新部署，Dashboard 里保存才会）
curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"clearCache":"do_not_clear"}' \
  'https://api.render.com/v1/services/<serviceId>/deploys'
```

复验：`curl -s https://securesignal-tee.onrender.com/health` 应返回 `status=ok`；
启用 LLM 后 `llm_configured=true` 且 `llm_model` 为你配置的模型名。

> 注意：单变量的 `PUT /env-vars/<KEY>` 只改那一个变量。整组的 `PUT /env-vars`
> 是**全量替换**，会删掉没带上的变量，别用它来改单项。

---

## 7. 遗留外部依赖（无法在本仓库内闭环）

| 事项 | 阻塞原因 | 入口 |
|---|---|---|
| ~~Coston2 真实部署~~ ✅ 已完成（2026-07-19，见 §2.5） | — | §2.5 |
| 应用托管 | 需用户的 Vercel/Render 账号（免费） | §6 |
| GCP Confidential Space vTPM attestation | 需 GCP TEE 环境与项目配置 | §3 |
| ~~真实 LLM 调用~~ ✅ 已完成（2026-09-30，DeepSeek `deepseek-flash`） | — | §4 |
| WalletConnect project id | 需 WalletConnect Cloud 账号 | §1.3 |
