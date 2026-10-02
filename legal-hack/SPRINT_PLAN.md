# Legal Hack 2026 冲刺计划 — SecureSignal → 法律科技赛道

> 生成日期：2026-09-05 · 赛事信息复核：2026-10-02 · 目标赛事：BLI Legal Tech Hackathon 2（DoraHacks）

---

## 1. 赛事情报（已核实）

| 项目 | 内容 |
|---|---|
| 名称 | BLI Legal Tech Hackathon 2（Blockchain Legal Institute 主办） |
| 奖池 | 页面显示 $20,000 USD；正文同时写明 “$10,000+ USD value in bounties” 且奖池仍在扩充，最终金额可能变动 |
| 截止 | **2026-11-01 09:01**（页面在 Asia/Shanghai 显示的时间） |
| 形式 | 线上 · Virtual |
| 标签 | Blockchain · Crypto · **Legal** · Finance · **Compliance** · AI · RWA · **RegTech** |
| 平台技术 | Bitcoin · Ethereum（EVM 兼容链均可，Flare 满足） |
| 规模 | 当前 1 个 Track（All BUIDLs）· 2 个 Bounty · 1 个已提交 BUIDL（竞争窗口极好） |
| 官网 | bli.tools/hackathon（口号：Law. Finance. Compliance.；赏金由律所/区块链公司提供，单个 $1k–$25k） |
| 额外激励 | 顶尖区块链律所的赏金/支持/推广、天使投资人路演机会、国际媒体报道、加速器网络 |

**页面状态（2026-10-02 复核）**：赛事页可公开读取到 Track 与 Bounty Tab；提交时选择唯一的
`All BUIDLs` Track，并按最终 BUIDL 叙事选择匹配的 Bounty。Bounty 详细评审要求仍需登录后
在提交页确认。

## 赛事与 BUIDL 对应关系（防止串台）

| 赛事 | 当前 BUIDL | 状态 |
|---|---|---|
| BLI Legal Tech Hackathon 2 | ZK-CID — Zero-Knowledge Compliance Identity & Decentralized Workflow Orchestration (`/build/48603`) | **Under Review**，这是当前 BLI 的正式参赛 BUIDL |
| Flare Summer Signal Hackathon | SecureSignal — Privacy-Preserving AI Portfolio Advisor on Flare (`/build/47690`) | Flare 赛事 BUIDL；**不在 BLI Builds 列表中** |
| Arc 相关赛事 | ArcProof (`/build/49114`) | 独立项目；**不在 BLI Builds 列表中** |

> 2026-10-02 复核截图：`deliverables/dorahacks-bli-builds-2026-10-02.png`。
> 如果把 AWS Nitro / Confidential Legal AI 作为 BLI 参赛方向，需要明确决定是让
> SecureSignal 替换 ZK-CID，还是新建 LexClave BUIDL；不能直接沿用两个互不相干的赛事文案。

---

## 2. 核心判断：现有项目的赛道契合度

SecureSignal 已有并验证过的资产（上一赛事 Flare Summer Signal 完成）：

- 浏览器端 ECIES 加密 ↔ TEE 内解密（eciesjs ↔ eciespy，线格式逐字节兼容）
- `AnalysisRegistry` 合约：TEE 密钥登记、attestation `ecrecover` 验签、结果哈希上链（含负例测试）
- Coston2 已部署合约 + 12/12 生产冒烟测试 + FTSO 真实读价
- 真实 AWS Nitro Enclaves 硬件 attestation：非 debug enclave、NSM COSE/CBOR、
  AWS root 验签、nonce/user_data/ECIES key/PCR0 全验证，task 18 链上 `Verified`
- Next.js 前端全流程 + LLM 分析引擎（OpenAI 兼容，规则引擎回退）
- Vercel + Render 在线 Demo + 演示视频管线

**这套栈的法律叙事几乎是量身定做的**：律师-客户特权（attorney-client privilege）、
证据链（chain of custody）、可审计性 —— TEE + 链上 attestation 恰好证明
"敏感法律数据只在经验证的、公开代码的 enclave 里被处理过，处理方自己也看不到原文"。
这是任何普通法律 AI SaaS 给不出的密码学保证。

---

## 3. 推荐转向方案：LexClave（暂定名）

**Confidential Legal AI —— 带链上处理证明的保密法律文档分析**

| 维度 | SecureSignal（现状） | LexClave（转向后） |
|---|---|---|
| 用户 | 加密资产投资者 | 律所 / 法务 / 合规团队 |
| 输入 | 加密持仓 JSON | 合同/尽调文档（客户端加密上传） |
| TEE 内处理 | 组合风险分析（LLM） | 条款抽取、风险标记、合规清单（LLM） |
| 链上证明 | 分析结果哈希 + attestation | 文档哈希 + 处理记录 + attestation（证据链） |
| 差异化 | "Not even we can see your data" | 同一句，但对律师是执业刚需而非锦上添花 |

代码复用预估：合约层 ~90%（加一个文档哈希登记函数），TEE 服务 ~70%
（换分析 prompt 和输入 schema），前端 ~60%（上传文档 UI 替换持仓表单）。

### 备选方向（如赏金明细指向别处）
- **B. RWA 合规证明**：隐私保护的 KYC/合格投资人证明 + 链上可验证状态
- **C. 合规审计锚定**：把现有组合分析保留，叙事改为"DAO/基金的隐私保护合规报告"

---

## 4. 冲刺时间线（2026-09-05 → 11-01，共 8 周）

| 阶段 | 日期 | 目标 |
|---|---|---|
| W1 情报+定方向 | 09/05–09/11 | 登录 DoraHacks 确认 Track/Bounty 明细；定最终方向；写新 README 骨架 |
| W2 合约+数据模型 | 09/12–09/18 | `DocumentRegistry` 合约改造、测试全绿、Coston2 重新部署 |
| W3 TEE 分析引擎 | 09/19–09/25 | 法律文档分析 pipeline（条款抽取/风险标记）、LLM prompt 工程、规则回退 |
| W4 前端 | 09/26–10/02 | 文档上传/加密 UI、结果展示、链上验证页（证据链时间线） |
| W5 端到端+部署 | 10/03–10/09 | e2e 测试、Vercel/Render 上线、冒烟测试 |
| W6 打磨 | 10/10–10/16 | 真实样例文档演示数据、法律叙事打磨、找法律人士试用反馈 |
| W7 提交材料 | 10/17–10/23 | 演示视频（沿用现有视频管线）、SUBMISSION.md、BUIDL 页面 |
| W8 缓冲+提交 | 10/24–10/31 | 缓冲一周修 bug；**10/30 前提交**，不留到截止当天 |

---

## 5. 立即行动清单

- [x] 复核 DoraHacks 赛事页：截止 `2026-11-01 01:01`、1 个 Track、2 个 Bounty
- [ ] 登录 DoraHacks，记录 2 个 Bounty 的具体评审要求（决定最终叙事）
- [ ] 确认方向：LexClave 法律文档 AI vs 备选 B/C
- [ ] 决定是否复用 GitHub 仓库（juangh123/securesignal）开新分支，还是新仓库
- [x] 检查上一赛事部署：AWS Nitro Enclaves 生产路径已部署并完成 Coston2 12/12 验证

---

*本文件随冲刺推进持续更新。*
