# DoraHacks Submission Form — Copy-Paste Sheet (SecureSignal)

Form: "Create a new BUIDL and submit" (Flare Summer Signal · Bounty 2: Confidential Compute Apps)

---

## 1. BUIDL (project) name *
`SecureSignal`

## 2. BUIDL logo *
File: `deliverables/SecureSignal_logo_480.png` (480×480 PNG, ~12 KB — meets the <2 MB / 480×480 guideline)

## 3. Vision *
> Describe the problem which this project solves

Paste this:

Getting personalized crypto portfolio advice today means handing your full holdings and strategy to a centralized service you cannot audit — exposing users to front-running, privacy breaches, and targeted attacks. SecureSignal runs the analysis engine inside a TEE on Flare Confidential Compute: holdings are encrypted in the browser with a one-time ECIES session key and decrypted only inside the enclave, then every result is signed and anchored on-chain so anyone can verify it came from the registered TEE. Not even we can see your data.

## 4. Category *
`Crypto / Web3`

## 5. GitHub/Gitlab/Bitbucket (optional)
`https://github.com/juangh123/securesignal`

## 6. Project website (optional)
`https://securesignal.vercel.app`

## 7. Demo video (optional)
Recommended: upload `video/dist/SecureSignal_demo_1080p_v3.mp4` (2:19) to YouTube and paste the YouTube link (the form embeds YouTube players).

Fallback direct link (works, but not embedded):
`https://github.com/juangh123/securesignal/raw/main/video/dist/SecureSignal_demo_1080p_v3.mp4`

## 8. Social links (at least one link) *
**Required by the form — no social URL is configured yet.** Paste at least one public link, e.g.:
- A team member's X/Twitter, Farcaster, Telegram, Substack, or GitHub profile that is NOT the project repo (or use the GitHub organization/account profile if acceptable): e.g. `https://github.com/juangh123`

---

## Other useful links for the submission text
- TEE backend: `https://securesignal-tee.onrender.com` (`/public-key`)
- Contracts (Coston2, chainId 114): AnalysisRegistry `0xe27DA7d476DF203D05afA3430fAa5Aefa14CE482` · FtsoV2Reader `0xDf0858eE9250f859Edd364C9bA1d27FA70A91F5a`
- Registered TEE address: `0xEe4975C290FBF46757A1D90F02c3CF555163556E`
- Example on-chain result (Coston2 tx): `0xe2d4321b7d49aaf5bd1bc9995c6cf0f12a936b3ae424b6460ace3bad60d457b3`
- Full narrative: `SUBMISSION.md`
