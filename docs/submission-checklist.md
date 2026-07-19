# SecureSignal 黑客松提交清单

> Flare Summer Signal Hackathon — Bounty 2: Confidential Compute Apps
> 状态日期：2026-07-19。✅ = 已完成并有验证证据；⏳ = 等待外部凭据/人工操作。

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

## 二、提交前必须完成的剩余项（⏳ 需要项目所有者操作）

| # | 事项 | 需要什么 | 指引 |
|---|---|---|---|
| 1 | Coston2 测试网部署合约 | 部署者私钥 + faucet 测试币 | `docs/deployment.md` §2（faucet → deploy.ts --network coston2 → setup-tee → 回填地址） |
| 2 |  tee-service 部署到可公网访问的环境 | 服务器 / 容器平台；生产 env（见 §1 表） | `docs/deployment.md` §1、§2.5 |
| 3 | 前端部署（Vercel 等） | WalletConnect projectId（已有）+ 平台账号 | `frontend/.env.local` 两变量配到平台 |
| 4 | 更新 README Live Demo 区块 | 完成 1–3 后填入真实地址与链接 | README.md / README.en.md `Live Demo` 节 |
| 5 | 录制 4 分钟演示视频 | 部署完成后的可运行环境 | 建议脚本：问题引入(30s) → 架构(45s) → 现场演示加密分析全流程(90s) → 链上验证(45s) → 亮点总结(30s) |
| 6 | （可选）真实 LLM key | OpenAI 兼容 API key | 设 `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL` 三个 env 即启用 |
| 7 | （可选，加分项）GCP Confidential Space 真实 vTPM | GCP 账号 + TEE 环境 | `docs/deployment.md` §3 改造路线 |

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
