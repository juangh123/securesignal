# SecureSignal 黑客松提交清单

> Flare Summer Signal Hackathon — Bounty 2: Confidential Compute Apps
> 状态日期：2026-08-13（提交前终检）。提交截止：2026-08-14 19:59（DoraHacks）。✅ = 已完成并有验证证据；⏳ = 等待外部凭据/人工操作。

## 一、代码与功能（✅ 全部完成）

| 项 | 状态 | 验证证据 |
|---|---|---|
| ECIES 端到端加密（eciesjs ↔ eciespy） | ✅ | 双向交叉解密测试向量通过（`frontend/e2e/`） |
| 合约 attestation 验签（ecrecover） | ✅ | `npx hardhat test` 11/11（含伪造签名负例） |
| `rotateTeeKey` onlyOwner 访问控制 | ✅ | 负例测试覆盖 |
| 结果 relayer 上链（submitResult） | ✅ | 端到端断言链上 status=Verified |
| LLM 分析引擎（OpenAI 兼容，含规则回退） | ✅ | mock LLM 两组用例（合法/畸形 JSON）通过 |
| FTSO 真实读价（直读 Coston2 官方 FtsoV2） | ✅ | 联机实测 BTC/ETH/FLR，时间戳秒级新鲜 |
| 前端完整流程 + 结果展示 | ✅ | tsc / lint / build 全过 |
| 本地端到端集成 | ✅ | 23/23 断言（`frontend/e2e/e2e-local-run.log`） |
| docker-compose 一键起（含 deploy 初始化） | ✅ | YAML 结构校验通过（本机无 docker，未实际启动） |
| 可复现 Docker 构建（digest + 哈希锁定） | ✅ | `tee-service/Dockerfile` + `requirements-lock.txt` |
| 文档（架构 / 部署手册 / 双语 README） | ✅ | docs/ 三份 + README.md / README.en.md |
| 版本管理 | ✅ | git init，首次提交 2c6a6ec |

## 二、提交前必办事项（状态：1–5 已完成）

| # | 事项 | 状态 | 验证证据 / 指引 |
|---|---|---|---|
| 1 | Coston2 测试网部署合约 | ✅ | AnalysisRegistry `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482`、FtsoV2Reader `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a`；生产冒烟 12/12（`frontend/e2e/e2e-coston2.mjs`） |
| 2 | tee-service 部署到可公网访问的环境 | ✅ | https://securesignal-tee.onrender.com（`/public-key` 200，已实测） |
| 3 | 前端部署（Vercel） | ✅ | https://securesignal.vercel.app（英文 UI，已实测） |
| 4 | 更新 README Live Demo 区块 | ✅ | README.md / README.en.md 已回填真实链接、合约地址、TEE 公钥与视频链接 |
| 5 | 录制演示视频 | ✅ | `video/dist/SecureSignal_demo_1080p_v3.mp4`（2:19，1080p，英文配音+字幕，含真实 Coston2 交易） |
| 6 | （可选）真实 LLM key | ◻ | OpenAI 兼容 key → 设 `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL`；当前线上未配置，走确定性规则引擎回退（已英文化） |
| 7 | （可选，加分项）GCP Confidential Space 真实 vTPM | ◻ | `docs/deployment.md` §3 改造路线 |

## 三、评审亮点（提交描述可用）

1. **信任链完整闭环**：客户端 ECIES 加密 → enclave 内解密分析 → 结果哈希 + TEE 签名上链 → 任何人都可对链验证结果出自登记的 TEE 密钥。
2. **安全不是贴纸**：`rotateTeeKey` 无访问控制的原漏洞已修复（onlyOwner）；合约端 ecrecover 验签拒绝伪造 attestation，含负例测试。
3. **FTSO 真实消费**：直读 Coston2 官方 FtsoV2 合约（经 FlareContractRegistry 解析），失败显式报错、绝不静默返回假价；离线模式显式标注。
4. **诚实的工程标注**：dev-simulated 部分（attestation token、本地 enclave、fixture 价）在代码、API 响应、UI 徽标、README 四处一致标注。
5. **可复现构建**：基础镜像 digest 锁定 + 77 个 pip 依赖 sha256 哈希锁定，支撑"镜像 digest 上链验证"叙事。

## 四、已知限制（评审问答预案）

- **Q: attestation 是真的 vTPM 吗？** A: 当前是 dev-simulated（结构化 JSON + 真实 secp256k1 签名 + 链上 ecrecover），改造路线已写就（vtpm.py TODO + deployment.md §3），合约层已预留生产接入点。
- **Q: LLM 调用在 TEE 内吗？** A: LLM API 调用由 enclave 内进程发起；TEE→LLM provider 链路的信任模型与缓解选项见 deployment.md §4。
- **Q: 为什么本地演示用 fixture 价？** A: 本地 hardhat 链无 FTSO；对 Coston2 RPC 的在线模式已联机实测（价格与时间戳见 README），部署后默认走真实喂价。
