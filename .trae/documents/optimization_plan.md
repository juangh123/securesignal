# SecureSignal Optimization Implementation Plan

> Status: APPROVED
> Source: User request based on prior repository analysis
> Mode: --quick
> Iterations: 1 / 3
> Author: Planner
> Last updated: 2026-07-27

## Requirements summary
根据早期的状态分析（Brooks Law / Health Check），SecureSignal 项目当前的碎片化测试和高认知负荷的跨语言加密解密模块是主要的维护瓶颈。本计划旨在针对这两个痛点提供明确的“下一步优化方案”：整合 E2E 测试并实现各独立模块的一键执行；优化加密模块边界封装以降低认知负荷，实现强类型契约。

## Acceptance criteria
- **AC-1 (Test Aggregation)**: 必须有一个集成测试聚合入口（例如 scripts/test-all.sh 或 .github/workflows/test.yml 抽象化的本地版本），通过单命令依次执行合约、TEE 与前端端到端测试，如果其中一个环节失败，聚合脚本返回非零退出代码。
- **AC-2 (Crypto Encapsulation)**: 完善 	ee-service/crypto/keys.py 与 rontend/src/utils/crypto.ts，要求两者间在数据传输前，在各自层完成严格结构化封装；且补充跨语言数据结构接口文档规范(例如在 docs/crypto-interface.md)。
- **AC-3 (Non-destructive)**: 优化过程不能破坏现有 CI 与已有的 rontend/e2e/e2e-local.mjs 测试闭环。

## Quick mode rationale
改动面极小且高度集中于工程质量/开发体验（DX）改善（脚本补充与局部重构），不改变系统基础架构与领域逻辑；方案没有高风险（无生产破坏性），因此适合一次即可闭环的 Quick Mode。

## RALPLAN-DR

### Principles
- **最小变动 (Minimal Footprint)**: 不修改已验证成功的内部加密算法，仅仅在外围封装出清晰的 API 边界。
- **Fail-Fast**: 集成脚本在任意环节（Contract -> TEE -> Frontend）出现故障时必须立刻报错阻止后续运行。
- **清晰所有权**: 强约束跨层通信（特别是 ecies 的加密体结构与类型强制）的单源契约（Single Source of Truth），降低开发人员在此接口摸索的成本。

### Decision drivers
- **开发及调试体验**: 当前每次测试或调试均需要多开窗口分别启动各种子项目，聚合能显著提升效率。
- **认知负荷 (Cognitive Load)**: 跨 Python 和 TS/JS 系统的 Cryptography 实现极易因为类型 (0x-prefixed hex string vs Buffer/Base64) 不一致而出错。

### Viable options
**Option A: 强类型约束 + Shell 集成测试** (Favored)
- **实现思路**:  编写统一的 Makefile 或者一个顶层 	est-all.ts/test-all.sh。针对加密/解密跨层，整理并补充清晰的类型签名注释与接口定义。新增 docs/crypto-interface.md 作为契约真理源。
- **Pros**: 无需引入额外的沉重工具。最小成本地解决了碎片化与边界不清晰的问题。
- **Cons**: 跨平台的本地 Shell 脚本维护在 Windows 环境下有时会有兼容性（目前看项目已有 start.bat 与 start.sh，可接受）。

**Option B: 引入完整的 Monorepo 测试框架 (Nx/Turborepo)**
- **实现思路**:  使用 Nx / Turborepo 将此应用迁移至 Monorepo 体系以规范化图管和测试。
- **Pros**: 大规模项目下的图管理与缓存。
- **Cons**: Over-engineering，改动极大（package.json 引发连锁重构），甚至容易违背目前的组织良好的模块边界。违反了“外科手术式改动”的 Principle。

## Implementation steps
1. **聚合脚本创建**:
   在项目根目录创建 	ests/run_e2e_all.sh (对于 Windows，可同步创建 un_e2e_all.bat)。
   需要依次触发:
     - cd contracts && npx hardhat test
     - cd tee-service && pytest
     - 初始化本地 TEE 和 hardhat 节点 (需在后台常驻)
     - cd frontend/e2e && node e2e-local.mjs
     - (注意需要在 exit 时 hook 停止后台服务)

2. **加密契约文档化**:
   - 创建 docs/crypto-interface.md
   - 文档细化传输载荷 Payload 对象结构: { client_pubkey: string, holdings: Record<string, number>, risk_profile: string }
   - 细化加密后在网络链路的形式：Base64( 65B ephem_pubkey || 16B nonce || 16B tag || cipher )

3. **加密模块重构（契约硬化）**:
   - 	ee-service/crypto/keys.py:  补充类型提示(Type hints) 和 Docstring（针对 Payload 解析与 dict 类型定义），如果 Python 版本支持，可补充 Pydantic 定义（例如: class TeePayload(BaseModel): client_pubkey: str...）。
   - rontend/src/utils/crypto.ts: 从现在的 unknown 宽泛接口收窄，定义确切的 export interface TeePayload { ... } 并在 encryptForTee(teePubHex: string, payloadObj: TeePayload) 实施约束。

## Workspace setup
- 实施前必须运行 git status --short 和 git branch --show-current。
- 如果 working tree 干净 (目前见到 M 和 ?? 状态的 dirty)，优先推荐创建 worktree 保护代码，且不能将现有的 M 状态（	ee-service/attestation/vtpm.py 等）污染到提交中。建议：git worktree add -b codex/optimization-dx ../Flare-Confidential-Compute-optimization-dx

## Risks & mitigations
| Risk | Mitigation |
|---|---|
| 集成脚本在不同操作系统（Win vs Unix）中的进程控制（杀死后台进程）难以统一 | 提供 Docker-compose 依赖或提供基于 Node.js child_process 的跨平台集成测脚本（例如 	est-all.mjs） |
| keys.py 补充 Pydantic 定义可能引入意外重构 | 最小化代码，仅引入 TypedDict 以限制运行时开销和依赖 |

## Verification steps
- 运行最终定稿的 	ests/run_e2e_all.sh 或 	est-all.mjs 必须在一个控制台中自行拉起合约节点、TEE 服务并执行完所有的测试，最后打印 SUMMARY: ALL PASSED 并携带 exit code 0 退回终端。
- 验证 rontend/src/utils/crypto.ts: IDE 能够正确对 payload 提供代码补全（TypeScript 强类型检测生效）。
