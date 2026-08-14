import { defaultWagmiConfig } from '@web3modal/wagmi/react/config';
import { cookieStorage, createStorage } from 'wagmi';
import { flare, flareTestnet, hardhat } from 'wagmi/chains';

// Get projectId from https://cloud.walletconnect.com
// No hardcoded fallback: set NEXT_PUBLIC_PROJECT_ID in .env.local (see .env.example).
export const projectId = process.env.NEXT_PUBLIC_PROJECT_ID;

if (!projectId) throw new Error('NEXT_PUBLIC_PROJECT_ID is not defined. Set it in .env.local (see .env.example).');

const metadata = {
  name: 'SecureSignal',
  description: 'Privacy-preserving AI portfolio advisor on Flare',
  url: 'https://securesignal.vercel.app',
  icons: ['https://raw.githubusercontent.com/juangh123/securesignal/main/deliverables/SecureSignal_logo_480.png']
}

// Create wagmiConfig
const chains = [flare, flareTestnet, hardhat] as const
export const config = defaultWagmiConfig({
  chains,
  projectId,
  metadata,
  ssr: true,
  storage: createStorage({
    storage: cookieStorage
  }),
})
