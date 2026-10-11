'use client'

import React, { ReactNode, useEffect } from 'react'
import { config, projectId } from '@/config'
import { createWeb3Modal } from '@web3modal/wagmi/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { State, WagmiProvider } from 'wagmi'
import { flareTestnet } from 'wagmi/chains'

const queryClient = new QueryClient()
let web3ModalInitialized = false

export default function Web3ModalProvider({
  children,
  initialState
}: {
  children: ReactNode
  initialState?: State
}) {
  // WalletConnect Core touches indexedDB while initializing. Do it in a
  // client-only effect so server rendering never evaluates it.
  useEffect(() => {
    if (!projectId || web3ModalInitialized) return
    createWeb3Modal({
      wagmiConfig: config,
      projectId,
      enableAnalytics: false,
      // The app only writes to Coston2 (chainId 114); without this the modal
      // defaults to the first configured chain (Flare mainnet) and the app's
      // own "Switch to Coston2" button cannot move the modal's chain state.
      defaultChain: flareTestnet,
    })
    web3ModalInitialized = true
  }, [])

  return (
    <WagmiProvider config={config} initialState={initialState}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </WagmiProvider>
  )
}
