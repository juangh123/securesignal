# SecureSignal Frontend

Next.js 16 + wagmi/Web3Modal + viem DApp for the SecureSignal confidential portfolio advisor.

## Stack

- Next.js 16 App Router
- wagmi / Web3Modal for wallet connections
- viem for on-chain reads and contract transactions
- eciesjs for browser-side ECIES encryption

## Environment

Copy `.env.example` to `.env.local` and fill in the values:

```bash
cp .env.example .env.local
```

Required:

- `NEXT_PUBLIC_PROJECT_ID` — WalletConnect Cloud project id

Optional:

- `NEXT_PUBLIC_TEE_URL` — defaults to `http://localhost:8000`

## Run Locally

```bash
npm install
npm run dev
```

Open `http://localhost:3000`. The backend TEE service must be running on the
configured `NEXT_PUBLIC_TEE_URL`.

## Checks

```bash
npm run lint
npm run build
```

## Related Docs

- Root protocol/architecture: `../docs/architecture.md`
- Encryption protocol: `../docs/crypto-interface.md`
- Deployment guide: `../docs/deployment.md`
