# SecureSignal 生产准备度评审 (PRR) 与优化建议

## 项目状态概述

基于项目文档、架构限制以及当前的代码实现，本项目已经准备好作为验证概念（Proof of Concept）或者黑客松版本运行。然而，它在正式投入生产环境处理敏感数据和提供真实的机密计算承诺方面，仍存在实质性风险，必须解决才能达到**生产就绪 (Production Ready)** 的标准。

项目性质属于**外部可访问系统**，且与**真实的加密资产和市场喂价相关联（高客户关键度与数据敏感性）**。虽然目前没有自动管理客户资产，但提供的安全承诺：“甚至我们也无法看到您的数据”，意味着数据生命周期的安全与透明度必须极高。

## A. 生产准备度矩阵 (PRR)

| 领域 | 状态 | 详情与评价 |
| :--- | :---: | :--- |
| **功能实现 (Code Completeness)** | ✅ *Pass* | ECIES 加解密链路前后端对齐；合约 `rotateTeeKey` 访问控制 (`onlyOwner`) 规范且经验证测试通过；`ecrecover` 机制正确应用于 Attestation 验签。 |
| **LLM 与 FTSO 引擎 (Engine)** | ✅ *Pass* | LLM与FTSO容错机制清晰，“绝不静默伪造假价或伪造LLM响应”。在异常时能以明确的 `rule-fallback` 退回，状态标注诚实。 |
| **机密计算 (TEE Attestation)** | ❌ *Blocker* | 当前后端使用的仍然是使用 Python 本地生成的 dev-simulated ECDSA 签名认证，无法证明执行是在真实的 GCP Confidential Space 或 Flare vTPM 等可信执行环境(TEE)里发生的。 |
| **私钥生命周期 (Key Lifecycle)** | ❌ *Blocker* | TEE 的临时（Dev）私钥缺乏安全的启动防护：如果 `TEE_PRIVATE_KEY` 环境变量缺失，其采取的降级策略是静默生成一串临时私钥。如果在生产中出现这样的配置丢失，可能导致重启后旧的加密载荷解谜失败并引入脏状态。 |
| **LLM 数据边界泄露** | ⚠️ *Follow-up* | 目前 LLM 的信任边界有越界，虽通过 ECIES 保护了外部进到 TEE 的隧道，但 prompt 内的敏感市值数据还是出了 TEE 边界抵达了 LLM 供应商。需要在用户侧添加明确的条款或者进行二次模型机密化适配才能投入生产。 |
| **依赖安全 (Supply Chain)** | ✅ *Pass* | Docker 镜像 digest 的锁定以及 `requirements-lock.txt` 下的所有 Python 依赖进行了 SHA256 哈希硬编码，这有效防范了针对 `pip` 的水坑攻击。 |

## B. 阻塞性问题 (Blockers) - 发布前必须解决

为实现从“测试阶段”正式步入“具有机密计算背书的生产系统”的跨越，以下事项为 **不可逾越的发布阻塞**：

| 领域 | 阻塞性风险 (Risk/Gap) | 解决标准 (Remediation) | 目标截止 |
| :---: | :--- | :--- | :--- |
| **安全/TEE** | **缺少真实的远程出具证明（Remote Attestation）机制**<br>没有接入真实的 TEE，任何人都无法辨别服务器是处于自建 VPC 还是确是存在于不可篡改的 Confidential Space (Enclave) 里。 | 需要将服务打包至 GCP Confidential Space。在 `vtpm.py` 加入调用 Google metadata 服务器拉取 OIDC Attestation JWT 令牌的过程。并在部署流程里添加通过 `rotateTeeKey` 持载 `image_digest` 的管理逻辑（详情可参考 `docs/deployment.md` 第 3 节）。 | 生产发版前 |
| **安全/密钥** | **生产缺失 `TEE_PRIVATE_KEY` 自动降级（Fail-open 风险）**<br>如果服务器未能拉取到合规的环境变量作为私钥，服务会擅自在内存搓一个随机私钥导致不可用的启动。 | 必须修改 `tee-service/crypto/keys.py` 逻辑：当探测到运行处于生产模式 (`ENV=prod`) 但不存在合法固定的一致 `TEE_PRIVATE_KEY` 环境变量时，**禁止生成临时密钥兜底，应该强制报错崩溃退出（Fail-closed）**。 | 生产发版前 |

## C. 后续跟进与优化建议 (Follow-ups & Recommendations)

如果不影响黑客松 Demo 发布，以下可作为后续版本的常规优化或设计改进的 Follow-up。

1. **链上验证真正的 TEE 令牌**：
   目前的方案为管理人员审查 GCP JWT 后手动以所有者 `OnlyOwner` 调用进行登记（信任 Owner）。长期安全来看，应该在 `AnalysisRegistry.sol` 编写真正的包含 Google root RSA 根证书公钥来执行在线的签名链 JWT Token 校验或者使用 Flare 专门的 attest 合约中间层。
2. **私钥无磁盘化（Keyless Architecture）**：
   未来，可以利用 GCP 的机密空间，利用 KMS（KMS在发现请求来自于合法的包含制定 `image_digest` 的 JWT 后自动解封加密的数据）。不直接传递 `TEE_PRIVATE_KEY` 环境变量，将密钥受信任暴露口降到最低。
3. **明示 LLM 模型信任边界 (Disclosure)**：
   应当在页面的分析条款处非常醒目地揭示：“由于接入通用大模型机制（非机密推理API），提交数据的核心价值字段将被作为 Prompt 被透明发送至模型推理方，脱离 TEE 的保密范畴”。
4. **前端重载提示（Rehydration Issue）**：
   在异常时如果发生 `task_id` 已经上报并消耗的重试，或者 `client_pubkey` 因为前端会话被异常覆盖而发生了变更，可能会导致请求处于悬挂状态。建议引入对特定分析 `session_id` 和公钥周期的 localStorage 级缓存机制保持短期的弹性。
