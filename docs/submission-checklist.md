# SecureSignal 黑客松提交清单

> Flare Summer Signal Hackathon — Bounty 2: Confidential Compute Apps
> 状态日期：2026-10-21（Flare 提交范围与发布验收复核）。Flare Summer Signal 已于
> 2026-08-14 19:59 UTC+8 截止，提交 BUIDL 为
> [`47690`](https://dorahacks.io/build/47690)。本清单只适用于 Flare 赛事；仓库中的
> BLI/Legal Hack 材料仅作历史记录，不参与本次提交说明。✅ = 已完成并有验证证据。

## 一、代码与功能（✅ 全部完成）

| 项 | 状态 | 验证证据 |
|---|---|---|
| ECIES 端到端加密（eciesjs ↔ eciespy） | ✅ | 双向交叉解密测试向量通过（`frontend/e2e/`） |
| 合约 attestation 验签（ecrecover） | ✅ | `npx hardhat test` 33/33（含伪造签名、高 s/v 越界、跨部署域分离负例） |
| TEE 密钥两步轮换（`proposeTeeKey`/`commitTeeKeyRotation`）onlyOwner + 时间锁 | ✅ | 负例与时间锁测试覆盖（首次登记免等待） |
| TEE 服务单元测试 + 覆盖率门槛 | ✅ | `pytest` 206 passed / 1 skipped；核心运行时覆盖率 87.64%，CI 门槛 80%（`.coveragerc`） |
| 公开端点加固 | ✅ | 链上任务门禁 fail-closed、single-flight + 结果缓存、每 IP/全局限速（429）、请求体上限、分析总预算；见 README「公开端点防滥用」 |
| CI 供应链与一致性 | ✅ | actions 按 commit SHA 固定；ABI/地址副本 `cmp`、依赖锁覆盖、`bash -n`、live-health、e2e-local 全流程作业 |
| 结果 relayer 上链（submitResult） | ✅ | 端到端断言链上 status=Verified |
| LLM 分析引擎（OpenAI 兼容，含规则回退） | ✅ | mock LLM 两组用例（合法/畸形 JSON）通过 |
| FTSO 真实读价（直读 Coston2 官方 FtsoV2） | ✅ | 联机实测 BTC/ETH/FLR，时间戳秒级新鲜 |
| 前端完整流程 + 结果展示 | ✅ | tsc / lint / build 全过 |
| 本地端到端集成 | ✅ | 25/25 断言（`tests/run_e2e_all.sh`，CI `e2e-local` 作业：起链 → 部署 → 登记 → 加密 → `/analyze` → 解密 → ecrecover → 链上 Verified） |
| docker-compose 一键起（含 deploy 初始化） | ✅ | YAML 结构校验通过（本机无 docker，未实际启动） |
| 可复现 Docker 构建（digest + 哈希锁定） | ✅ | `tee-service/Dockerfile` + `requirements-lock.txt` |
| 文档（架构 / 部署手册 / 双语 README） | ✅ | docs/ 三份 + README.md / README.en.md |
| 版本管理 | ✅ | git init，首次提交 2c6a6ec |

## 二、提交事项（1–7 均已完成）

| # | 事项 | 状态 | 验证证据 / 指引 |
|---|---|---|---|
| 1 | Coston2 测试网部署合约 | ✅ | AnalysisRegistry `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482`、FtsoV2Reader `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a`；生产冒烟 12/12（`frontend/e2e/e2e-coston2.mjs`） |
| 2 | tee-service 部署到可公网访问的环境 | ✅ | 真实硬件路径：https://d1tubqcwiwwev5.cloudfront.net（CloudFront → AWS Nitro Enclave，`/health` 返回 `aws-nitro-enclaves`）；dev-simulated Demo：https://securesignal-tee.onrender.com |
| 3 | 前端部署（Vercel） | ✅ | https://securesignal.vercel.app（英文 UI，已实测） |
| 4 | 更新 README Live Demo 区块 | ✅ | README.md / README.en.md 已回填真实链接、合约地址、TEE 公钥与视频链接 |
| 5 | 录制演示视频 | ✅ | `video/dist/SecureSignal_demo_1080p_v3.mp4`（2:19，1080p，英文配音+字幕，含真实 Coston2 交易） |
| 6 | （可选）真实 LLM key | ✅ | 2026-09-30 已在 Render 配置 DeepSeek `deepseek-flash`（key 走 Dashboard/API，不入库）；Render Demo 的 `/health` 返回 `llm_configured=true`，`/analyze` 返回 `analysis_mode="llm"`。真实 enclave 路径不配置 LLM key，走规则引擎 |
| 7 | （可选，加分项）真实硬件 attestation | ✅ | AWS Nitro Enclaves 非 debug 部署（2.7.0 重建，PCR 条件化 KMS key release）；NSM COSE 签名、证书链、nonce、user_data、ECIES key、PCR0 全部验证通过；task 22 链上 `Verified`（`deliverables/aws-nitro-attestation-22.json` + 独立验证） |

## 三、评审亮点（提交描述可用）

1. **信任链完整闭环**：客户端 ECIES 加密 → enclave 内解密分析 → 结果哈希 + TEE 签名上链 → 任何人都可对链验证结果出自登记的 TEE 密钥。
2. **安全不是贴纸**：TEE 密钥轮换已改为 owner-only 的两步流程并在公共网络强制延迟生效（`proposeTeeKey` → 等 `teeRotationDelay` → `commitTeeKeyRotation`，可 `cancelTeeKeyRotation` 撤销），轮换在生效前即可被链上监控发现；合约端 ecrecover 验签拒绝伪造 attestation，含负例测试。
3. **FTSO 真实消费**：直读 Coston2 官方 FtsoV2 合约（经 FlareContractRegistry 解析），失败显式报错、绝不静默返回假价；离线模式显式标注。
4. **真实硬件证明 + 诚实分级**：生产路径运行在 AWS Nitro Enclave，返回可验证的 NSM attestation document；公共 Render demo 仍明确标注 `dev-simulated`，两者不混淆。
5. **可复现构建**：基础镜像 digest 锁定 + 73 个 pip 依赖 sha256 哈希锁定（已按未使用依赖审计移除 numpy/pandas 及其孤儿传递依赖；顶层 pin 全部精确，重生成锁是确定性的）；链上 `expectedImageDigest` 已更新为 `keccak256(AWS Nitro PCR0)`（`rotateTeeKey` tx `0xe353838836c44053aa3372110b56db307ac48016bbe9637e66f90996c7db4cde`）。当前线上 v1 合约只做 EIP-191 验签（v2 源码已升级为 EIP-712 域绑定，待部署），NSM 文档验证在链下完成。

## 四、已知限制（评审问答预案）

- **Q: attestation 是真的硬件证明吗？** A: AWS Nitro Enclaves 路径是真实硬件证明：我们固定 AWS Nitro root、验证 COSE/ES384 签名链，并核对 nonce、`task_id + result_hash`、ECIES 公钥和 PCR0。公共 Render demo 仍可能使用明确标注的 `dev-simulated` 模式。
- **Q: LLM 调用在 TEE 内吗？** A: LLM API 调用由 enclave 内进程发起；TEE→LLM provider 链路的信任模型与缓解选项见 deployment.md §4。
- **Q: 为什么本地演示用 fixture 价？** A: 本地 hardhat 链无 FTSO；对 Coston2 RPC 的在线模式已联机实测（价格与时间戳见 README），部署后默认走真实喂价。
