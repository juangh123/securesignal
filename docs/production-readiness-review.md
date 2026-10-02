# SecureSignal 生产准备度评审 (PRR) 与优化建议

> **历史说明（评审日期 2026-07-31；状态更新 2026-10-02）**：本评审记录中的两个
> Blocker 均已在代码与部署中解决——生产路径现运行于非 debug 的 AWS Nitro Enclave，
> NSM attestation 已对 AWS 根证书验签并验证 nonce/user_data/ECIES key/PCR0
> （见 `deploy/aws/README.md`、`deliverables/aws-nitro-attestation-18.json`）；
> `TEE_PRIVATE_KEY` 在 `ENV=prod` 缺失时已改为 fail-closed。以下矩阵与建议保留为
> 当时的评审记录，其中 TEE/密钥两条不再代表当前状态。

## 项目状态概述

基于项目文档、架构限制以及当前的代码实现，本项目已经准备好作为验证概念（Proof of Concept）或者黑客松版本运行。然而，它在正式投入生产环境处理敏感数据和提供真实的机密计算承诺方面，仍存在实质性风险，必须解决才能达到**生产就绪 (Production Ready)** 的标准。

项目性质属于**外部可访问系统**，且与**真实的加密资产和市场喂价相关联（高客户关键度与数据敏感性）**。虽然目前没有自动管理客户资产，但提供的安全承诺：“甚至我们也无法看到您的数据”，意味着数据生命周期的安全与透明度必须极高。

## A. 生产准备度矩阵 (PRR)

| 领域 | 状态 | 详情与评价 |
| :--- | :---: | :--- |
| **功能实现 (Code Completeness)** | ✅ *Pass* | ECIES 加解密链路前后端对齐；合约 `rotateTeeKey` 访问控制 (`onlyOwner`) 规范且经验证测试通过；`ecrecover` 机制正确应用于 Attestation 验签。 |
| **LLM 与 FTSO 引擎 (Engine)** | ✅ *Pass* | LLM与FTSO容错机制清晰，“绝不静默伪造假价或伪造LLM响应”。在异常时能以明确的 `rule-fallback` 退回，状态标注诚实。 |
| **机密计算 (TEE Attestation)** | ✅ *Pass (2026-10-02)* | 生产路径运行于非 debug 的 AWS Nitro Enclave；NSM COSE/CBOR 文档已对固定 AWS 根证书验签，证书链、ES384 签名、nonce、`task_id + result_hash` user_data、ECIES 公钥与 PCR0 全部通过。Coston2 task 18 链上 `Verified`，证据见 `deliverables/aws-nitro-attestation-18.json` 与独立 verifier 输出。公共 Render 端点仍明确标注 `dev-simulated`，不再把演示路径当作硬件证明。 |
| **私钥生命周期 (Key Lifecycle)** | ✅ *Pass (2026-10-02)* | `crypto/keys.py` 在 `ENV=prod` 且缺少/空白 `TEE_PRIVATE_KEY` 时直接抛错（fail-closed），临时密钥仅允许在 dev 模式生成；`crypto/test_keys.py` 覆盖 5 个密钥生命周期用例。KMS key release 仍是下一步强化项，见 Follow-ups。 |
| **LLM 数据边界泄露** | ⚠️ *Follow-up（已披露）* | 前端 TrustNotice 会依据实时 `/health` 显示：配置 LLM 时，holdings/symbols 等字段会作为 prompt 文本发给通用模型供应商，保密范围仅覆盖 browser → TEE 传输。剩余风险是无法在不更换机密推理供应商的前提下消除，属于明确的用户知情选择。 |
| **依赖安全 (Supply Chain)** | ✅ *Pass* | Docker 镜像 digest 的锁定以及 `requirements-lock.txt` 下的所有 Python 依赖进行了 SHA256 哈希硬编码，这有效防范了针对 `pip` 的水坑攻击。 |

## B. 原阻塞项关闭记录 (Closed Blockers)

以下两项是 2026-07-31 评审提出的发布阻塞。它们已于 2026-10-02 关闭，保留原始风险描述以便追溯：

| 领域 | 原阻塞性风险 (Risk/Gap) | 关闭方式与证据 (Closure) | 状态 |
| :---: | :--- | :--- | :--- |
| **安全/TEE** | **缺少真实的远程出具证明（Remote Attestation）机制**<br>没有接入真实的 TEE，任何人都无法辨别服务器是处于自建 VPC 还是确是存在于不可篡改的 Confidential Space (Enclave) 里。 | AWS Nitro Enclaves 非 debug 部署；`verify_aws_nitro_attestation.py` 固定 AWS Nitro root，校验 COSE_Sign1 ES384、证书链、nonce、user_data、ECIES 公钥与 PCR0；Coston2 task 18 链上 `Verified`（`requestAnalysis` + `ResultSubmitted` 均有交易哈希）。 | ✅ 2026-10-02 |
| **安全/密钥** | **生产缺失 `TEE_PRIVATE_KEY` 自动降级（Fail-open 风险）**<br>如果服务器未能拉取到合规的环境变量作为私钥，服务会擅自在内存搓一个随机私钥导致不可用的启动。 | `tee-service/crypto/keys.py` 在 `ENV=prod` 且 key 缺失/空白时抛 `RuntimeError`；仅 dev 生成临时密钥。新增 `tee-service/crypto/test_keys.py`（prod fail-closed、空白 key、dev ephemeral、非法长度、合法 key 缓存共 5 例）。 | ✅ 2026-10-02 |

## C. 后续跟进与优化建议 (Follow-ups & Recommendations)

如果不影响黑客松 Demo 发布，以下可作为后续版本的常规优化或设计改进的 Follow-up。

1. **链上验证真正的 TEE 证明**：
   当前 `AnalysisRegistry` 只校验 EIP-191 TEE 签名；NSM/COSE 验证由链下 verifier 完成，owner 登记 TEE key 的信任假设仍然存在。长期方案是在合约或 Flare attestation 中间层验证 NSM/JWT 证明（含 PCR 白名单），让链上状态不依赖 owner 的人工审查。
2. **私钥无磁盘化（Keyless Architecture）**：
   当前父实例通过 vsock 转发 runtime secret bundle，父实例可以拒绝服务，但仍在其信任边界之外。下一步应把 `TEE_PRIVATE_KEY` 改为 KMS 封存密钥，并用 `kms:RecipientAttestation:PCR0`（配合 PCR3/PCR8）限制 `kms:Decrypt`，让密钥只在通过证明的 enclave 内解封。
3. **LLM 模型信任边界 (Disclosure)**：
   前端已实时披露通用 LLM 会接收 prompt 文本、保密范围只到 TEE 边界。若要在产品层面消除该越界，需要替换为机密推理 API 或允许用户选择纯规则引擎模式。
4. **前端重载提示（Rehydration Issue）**：
   在异常时如果发生 `task_id` 已经上报并消耗的重试，或者 `client_pubkey` 因为前端会话被异常覆盖而发生了变更，可能会导致请求处于悬挂状态。建议引入对特定分析 `session_id` 和公钥周期的 localStorage 级缓存机制保持短期的弹性。
