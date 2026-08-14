# SecureSignal Demo Video - Storyboard

> **Production Log (2026-08-03):** Ai voice tracks, SRT subtitles, and animated scenes have been synthesized.
> **Exported Final Video:** `video/dist/SecureSignal_demo_1080p_v3.mp4` (2:19, 1080p30, H.264+AAC).
> Raw recordings and intermediate renders are intentionally gitignored; the final render remains tracked.

---


## 0. 录制前准备清单（务必逐项确认）

- [ ] MetaMask 已切换到 **Coston2 测试网**（Chain ID 114），账户内有少量 C2FLR
      （水龙头：https://faucet.flare.network/coston2 ）
- [ ] 浏览器打开 https://securesignal.vercel.app/ 能正常显示（深色新 UI）
- [ ] 浏览器窗口 1920x1080 或最大化；按 Ctrl+0 恢复 100% 缩放；隐藏书签栏（Ctrl+Shift+B）
- [ ] 关闭无关标签页；系统通知开启勿扰模式
- [ ] OBS：来源选「窗口采集」锁定浏览器窗口；输出 1080p30，码率 8000kbps，格式 mp4
- [ ] 录音：用耳机麦克风即可，环境安静；配音见第 2 节英文台词，语速适中
- [ ] 持仓输入框预填：`0.5 BTC, 2 ETH, 10000 FLR`

---

## 1. 分镜总览

| # | 时间码 | 画面 | 素材来源 | 配音 |
|---|--------|------|----------|------|
| 1 | 0:00-0:06 | 片头卡 | assets/title_card.png | 旁白 1 |
| 2 | 0:06-0:32 | 片头卡渐隐 → 切到应用首页（未连接钱包） | 录屏 A | 旁白 2 |
| 3 | 0:32-0:58 | 架构图（带高亮动画的顺序可分段放大） | assets/architecture.png | 旁白 3 |
| 4 | 0:58-2:02 | **实时演示**（连接钱包→加密→链上登记→TEE分析→解密→结果） | 录屏 B | 旁白 4 |
| 5 | 2:02-2:22 | 结果页 & Attestation 校验区缓慢滚动 | 录屏 C | 旁白 5 |
| 6 | 2:22-2:40 | 片尾卡（Demo / GitHub / 合约地址） | assets/outro_card.png | 旁白 6 |

---

## 2. 英文配音台词（逐段，供录音 + 字幕）

**旁白 1（0:00-0:06，约 15 词）**
> "This is SecureSignal — confidential portfolio intelligence, built on Flare Confidential Compute."

**旁白 2（0:06-0:32，约 60 词）**
> "Getting personalized crypto portfolio advice today means handing your full holdings to a centralized service you cannot audit — exposing you to front-running, privacy leaks, and targeted attacks. SecureSignal runs the analysis entirely inside a Trusted Execution Environment on Flare. Your data is encrypted in the browser, decrypted only inside the enclave, and every result carries a verifiable on-chain attestation. Not even we can see your data."

**旁白 3（0:32-0:58，约 60 词）**
> "Here is how it works. The frontend generates a one-time session key and encrypts your portfolio with the TEE's public key. A registry contract on the Coston2 testnet records the task and the TEE's attestation key. The TEE node decrypts the payload inside the enclave, runs the risk analysis, signs the result, and encrypts it back to your session key. Finally, the app verifies the signature and the on-chain result hash."

**旁白 4（0:58-2:02，约 75 词，配合操作节奏）**
> "Let's see it live. I connect my wallet on the Coston2 testnet, and enter a sample portfolio. When I click run, the app encrypts everything locally — nothing leaves the browser in plaintext. Now it registers the analysis task on-chain. The encrypted payload is sent to the TEE node, which performs the analysis inside the enclave. And here is the result — risk score, per-asset rebalancing advice, and a summary — decrypted locally, only visible to me."

**旁白 5（2:02-2:22，约 45 词）**
> "At the bottom, the app proves end-to-end integrity: the attestation's result hash matches the decrypted payload, signed by the registered TEE key, and anchored by the registry contract on Flare. Everything you see was built during this hackathon — the TEE engine, the Coston2 contracts, and this encrypted frontend."

**旁白 6（2:22-2:40，约 30 词）**
> "Next, we plan to pull FTSO price feeds directly inside the enclave, add zero-knowledge risk proofs for lending protocols, and launch on Flare mainnet. Try the live demo — links below. Thank you!"

---

## 3. 录屏分段操作指引（共 3 段，分开录、命名如下）

### 录屏 A — `raw/A_landing.mp4`（约 10 秒）
1. 刷新页面，保持未连接钱包状态
2. 鼠标自然移动一下，停留 8 秒即可（后期会从片头卡淡入到这里）

### 录屏 B — `raw/B_demo.mp4`（约 60-70 秒，核心段，建议多录两遍挑好的）
1. 点击 **Connect Wallet** → MetaMask 弹窗确认连接（弹窗也要录进去）
2. 确认输入框内容为 `0.5 BTC, 2 ETH, 10000 FLR`
3. 点击 **Run Confidential Analysis** 按钮
4. MetaMask 交易弹窗弹出 → 点击确认（这一步是"链上登记"，很关键）
5. 等待步骤条走过：校验密钥 → 链上登记 → TEE 分析 → 解密完成
6. 结果出现后停留 3 秒

> 提示：如果某一步报错，停下来重录这一段即可，后期只取成功的那一遍。

### 录屏 C — `raw/C_result.mp4`（约 20 秒）
1. 从结果顶部（风险评分、调仓建议）缓慢匀速向下滚动
2. 一直滚到 Attestation 校验区（"attestation.result_hash 与响应 result_hash 一致 ✓"）
3. 在 ✓ 处停留 3 秒

---

## 4. 配音录制方式（二选一）

- **方案甲（推荐）**：单独用手机/耳机麦克风按第 2 节台词分 6 段录音，
  命名 `raw/voice_1.mp3` … `raw/voice_6.mp3`（ wav/m4a 也行）。后期我来对齐。
- **方案乙**：录屏 B/C 时直接开口念旁白 4/5，旁白 1/2/3/6 再单独补录。

## 5. 素材交给我之后的剪辑流水线（我来执行）

1. 片段修剪对齐 → 拼接（片头卡 → A → 架构图 → B → C → 片尾卡）
2. 叠加英文硬字幕（`subs/voiceover.srt`，按你的录音重新对时）
3. 片头片尾淡入淡出、段落间 0.5s 交叉淡化
4. 响度统一（loudnorm 到 -16 LUFS），可选轻背景垫乐（音量 -28dB）
5. 导出 `dist/SecureSignal_demo_1080p_v3.mp4`（H.264 + AAC，≤ 100MB，可直接上传 YouTube）
