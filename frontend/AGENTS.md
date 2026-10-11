# SecureSignal Frontend

Next.js 16 + wagmi/Web3Modal + viem.

- 加密协议：见项目根目录 `plan.md` 的「统一加密协议规范」。前端用 `eciesjs`（secp256k1 ECIES），实现在 `src/utils/crypto.ts`。
- 环境变量：`NEXT_PUBLIC_PROJECT_ID`（WalletConnect，无 fallback，必填）、`NEXT_PUBLIC_TEE_URL`（默认 `http://localhost:8000`）。本地配置放 `.env.local`，模板见 `.env.example`。
- 合约地址读 `src/config/contract-addresses.json`，ABI 读 `src/config/AnalysisRegistry.json`（由合约部署脚本刷新，勿手改）。
- 校验：`npx tsc --noEmit`、`npm run lint`、`npm run build`。

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
