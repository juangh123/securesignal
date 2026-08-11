import { chromium } from "file:///C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const SCENES = {
  seg1_title: 8.0,
  seg2_problem: 32.0,
  seg3_arch: 31.0,
  seg4_demo: 32.5,
  seg5_result: 22.5,
  seg6_outro: 15.0,
};

const cwd = process.cwd().replaceAll("\\", "/");

for (const [name, dur] of Object.entries(SCENES)) {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: "video/raw", size: { width: 1920, height: 1080 } },
  });
  const page = await context.newPage();
  await page.goto(`file:///${cwd}/video/scenes/${name}.html`);
  await page.waitForTimeout(Math.round((dur + 2.5) * 1000));
  await context.close();
  await page.video().saveAs(`video/raw/${name}.webm`);
  await browser.close();
  console.log(name, "done");
}
console.log("ALL RECORDED");
