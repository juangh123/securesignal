import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

import { headers } from 'next/headers'
import { cookieToInitialState } from 'wagmi'
import { config } from '@/config'
import Web3ModalProvider from '@/context'

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "SecureSignal - Flare Confidential Compute",
  description: "Privacy-preserving AI portfolio advisor on Flare",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const headersList = await headers()
  const initialState = cookieToInitialState(config, headersList.get('cookie'))

  return (
    <html lang="en">
      <body className={inter.className}>
        <Web3ModalProvider initialState={initialState}>
          {children}
        </Web3ModalProvider>
      </body>
    </html>
  );
}