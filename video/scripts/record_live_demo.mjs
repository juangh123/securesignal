/**
 * Record the LIVE SecureSignal demo against the deployed app.
 *
 * Playwright launches Chrome with an injected EIP-1193 provider backed by the
 * deployer private key (contracts/.env). Signing/broadcast calls are handled
 * in Node via Playwright route interception (bypasses browser CORS/PNA), so
 * the real UI flow runs end-to-end: connect -> Run Confidential Analysis ->
 * on-chain tx -> TEE /analyze -> decrypted result + attestation.
 *
 * Output: video/raw/live_demo.webm + live_demo_events.json (timestamps).
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire('F:/AI WORK/Flare Confidential Compute/frontend/package.json');
const { privateKeyToAccount } = require('viem/accounts');
import { chromium } from 'file:///C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';

const __dir = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dir, '..', '..');
const RAW = join(ROOT, 'video', 'raw');
const RPC = 'https://coston2-api.flare.network/ext/C/rpc';
const APP = 'https://securesignal.vercel.app';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const RELAY_BASE = 'http://127.0.0.1:8787';

mkdirSync(RAW, { recursive: true });

const envText = readFileSync(join(ROOT, 'contracts', '.env'), 'utf-8');
const keyLine = envText.split('\n').find((l) => l.startsWith('PRIVATE_KEY='));
if (!keyLine) throw new Error('PRIVATE_KEY not found in contracts/.env');
const account = privateKeyToAccount(keyLine.split('=')[1].trim());
console.log('wallet:', account.address);

async function rpc(method, params) {
  const res = await fetch(RPC, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: Math.floor(Math.random() * 1e6), method, params: params ?? [] }),
  });
  const j = await res.json();
  if (j.error) throw new Error(j.error.message);
  return j.result;
}

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 1,
  recordVideo: { dir: RAW, size: { width: 1920, height: 1080 } },
});

// Route interception: page -> 127.0.0.1:8787 handled here in Node (no CORS/PNA).
const cors = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET,POST,OPTIONS',
  'access-control-allow-headers': 'content-type',
};
await context.route(RELAY_BASE + '/**', async (route) => {
  const req = route.request();
  if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
  let body = {};
  try { body = JSON.parse(req.postData() || '{}'); } catch { /* ignore */ }
  const path = new URL(req.url()).pathname;
  try {
    if (path === '/send') {
      const nonce = await rpc('eth_getTransactionCount', [account.address, 'pending']);
      const gasPrice = await rpc('eth_gasPrice');
      const estimate = await rpc('eth_estimateGas', [{ from: account.address, to: body.to, data: body.data ?? '0x', value: body.value ?? '0x0' }]);
      const sig = await account.signTransaction({
        chainId: 114,
        to: body.to,
        data: body.data ?? '0x',
        value: body.value ? BigInt(body.value) : 0n,
        gas: BigInt(estimate),
        gasPrice: BigInt(gasPrice),
        nonce: Number(nonce),
      });
      const hash = await rpc('eth_sendRawTransaction', [sig]);
      console.log('[tx] sent:', hash);
      return route.fulfill({ status: 200, headers: { ...cors, 'content-type': 'application/json' }, body: JSON.stringify({ hash }) });
    }
    if (path === '/sign') {
      const sig = await account.signMessage({ message: { raw: body.data } });
      return route.fulfill({ status: 200, headers: { ...cors, 'content-type': 'application/json' }, body: JSON.stringify({ sig }) });
    }
    if (path === '/sign-typed') {
      const sig = await account.signTypedData(body.typed ?? body);
      return route.fulfill({ status: 200, headers: { ...cors, 'content-type': 'application/json' }, body: JSON.stringify({ sig }) });
    }
    return route.fulfill({ status: 404, headers: cors, body: JSON.stringify({ error: 'not found' }) });
  } catch (e) {
    console.error('[route] error:', e.message);
    return route.fulfill({ status: 500, headers: { ...cors, 'content-type': 'application/json' }, body: JSON.stringify({ error: e.message }) });
  }
});

await context.addInitScript(
  ({ address, relay }) => {
    const RPC = 'https://coston2-api.flare.network/ext/C/rpc';
    async function rpc(method, params) {
      const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params: params ?? [] }) });
      const j = await res.json();
      if (j.error) throw new Error(j.error.message);
      return j.result;
    }
    const provider = {
      isMetaMask: true,
      request: async ({ method, params }) => {
        switch (method) {
          case 'eth_requestAccounts':
          case 'eth_accounts':
            return [address];
          case 'eth_chainId':
            return '0x72';
          case 'net_version':
            return '114';
          case 'wallet_switchEthereumChain':
          case 'wallet_addEthereumChain':
            return null;
          case 'wallet_watchAsset':
            return true;
          case 'eth_sendTransaction': {
            const res = await fetch(relay + '/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params[0]) });
            const j = await res.json();
            if (!res.ok) throw new Error(j.error || 'relay send failed');
            return j.hash;
          }
          case 'personal_sign': {
            const res = await fetch(relay + '/sign', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data: params[0] }) });
            const j = await res.json();
            if (!res.ok) throw new Error(j.error || 'relay sign failed');
            return j.sig;
          }
          case 'eth_signTypedData_v4': {
            const typed = typeof params[1] === 'string' ? JSON.parse(params[1]) : params[1];
            const res = await fetch(relay + '/sign-typed', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ typed }) });
            const j = await res.json();
            if (!res.ok) throw new Error(j.error || 'relay sign-typed failed');
            return j.sig;
          }
          default:
            return rpc(method, params);
        }
      },
      on() {}, off() {}, removeListener() {}, removeAllListeners() {},
    };
    Object.defineProperty(window, 'ethereum', { value: provider, configurable: true, writable: true });
    window.dispatchEvent(new Event('ethereum#initialized'));
    const info = {
      uuid: '8a8e5b4a-6c31-4b8f-9f2d-5a1b2c3d4e5f',
      name: 'MetaMask',
      icon: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><rect width="24" height="24" rx="4" fill="%23f6851b"/></svg>',
      rdns: 'io.metamask',
    };
    const announce = () => window.dispatchEvent(new CustomEvent('eip6963:announceProvider', { detail: Object.freeze({ info, provider }) }));
    window.addEventListener('eip6963:requestProvider', announce);
    announce();
  },
  { address: account.address, relay: RELAY_BASE }
);

const page = await context.newPage();
page.on('console', (m) => { if (m.type() === 'error') console.log('[browser-error]', m.text().slice(0, 300)); });
page.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 300)));
const t0 = Date.now();
const log = (name) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1)}s] ${name}`);
const events = [];

await page.goto(APP, { waitUntil: 'domcontentloaded', timeout: 60000 });
log('page loaded');

// connect wallet (EIP-6963 injected provider)
await page.waitForSelector('w3m-button', { timeout: 60000 });
await page.click('w3m-button');
log('clicked connect');
await page.waitForSelector('w3m-modal', { timeout: 20000 });
await page.waitForTimeout(2500);
const rows = page.locator('wui-list-wallet');
let clicked = false;
for (let i = 0; i < await rows.count(); i++) {
  const name = ((await rows.nth(i).getAttribute('name')) || '').toLowerCase();
  if (name.includes('metamask')) {
    await rows.nth(i).evaluate((el) => el.click());
    clicked = true;
    log('clicked MetaMask connector');
    break;
  }
}
if (!clicked) throw new Error('MetaMask row not found in wallet modal');

await page.waitForSelector('textarea', { timeout: 60000 });
log('wallet connected, form visible');
events.push(['connected', (Date.now() - t0) / 1000]);

await page.fill('textarea', '0.5 BTC, 2 ETH, 10000 FLR');
await page.waitForTimeout(800);
log('holdings set');

const runBtn = page.getByRole('button', { name: /Encrypt & analyze in TEE/ });
await runBtn.waitFor({ state: 'visible', timeout: 15000 });
await runBtn.click();
log('analysis started');
events.push(['run', (Date.now() - t0) / 1000]);

// wait for result, polling the app state
const resultDeadline = Date.now() + 150000;
let resultVisible = false;
let lastState = '';
while (Date.now() < resultDeadline) {
  const bodyText = await page.evaluate(() => document.body.innerText).catch(() => '');
  const hasResult = bodyText.includes('TEE Analysis Result');
  const statusMatch = bodyText.match(/(\d\/7[^\n]{0,80})/);
  const errMatch = bodyText.match(/流程中断([\s\S]{0,200})/);
  const state = (hasResult ? 'RESULT' : '') + (statusMatch ? ' status=' + statusMatch[1].trim() : '') + (errMatch ? ' ERR=' + errMatch[1].trim().replace(/\n+/g, ' ').slice(0, 160) : '');
  if (state !== lastState) { console.log('[poll]', state); lastState = state; }
  if (hasResult) { resultVisible = true; break; }
  if (errMatch) break;
  await page.waitForTimeout(3000);
}
if (resultVisible) {
  log('result visible');
  events.push(['result', (Date.now() - t0) / 1000]);
  await page.waitForTimeout(6000);
} else {
  console.log('WARN: result not visible; final state logged above');
  events.push(['result_missing', (Date.now() - t0) / 1000]);
}

// smooth scroll through result down to attestation (slow, long)
events.push(['scroll_start', (Date.now() - t0) / 1000]);
await page.evaluate(async () => {
  const total = Math.max(0, document.body.scrollHeight - window.innerHeight);
  const steps = 90;
  for (let i = 0; i <= steps; i++) {
    window.scrollTo(0, (total * i) / steps);
    await new Promise((r) => setTimeout(r, 200));
  }
});
log('scrolled to bottom');
await page.waitForTimeout(12000);
events.push(['end', (Date.now() - t0) / 1000]);

const video = page.video();
await context.close();
await video.saveAs(join(RAW, 'live_demo.webm'));
console.log('SAVED video/raw/live_demo.webm');
writeFileSync(join(RAW, 'live_demo_events.json'), JSON.stringify(events, null, 2));
await browser.close();
