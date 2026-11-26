// GPU capture of the dev site (Chrome for Testing, Metal, real frame rate).
//   node tools/capture.mjs shot  <hash> <out.png> [w] [h] [actions-json]
//   node tools/capture.mjs video <hash> <out.gif|out-dir> [w] [h] [actions-json] [seconds] [fps]
// hash: "burst", "orbit", "tunnel" (the page is $CAPTURE_BASE or http://127.0.0.1:5199/, plus #<hash>)
// actions (in order): {"wait":ms} {"click":[x,y]} {"move":[x,y]} {"key":"Escape"} {"wheel":[x,y,dy]}
//   {"drag":[[x1,y1],[x2,y2],steps?]} {"eval":"spine.open(12)"} {"shot":"path.png"} {"record":true}
// "video" records frames (screencast) while the actions run (from the first {"record":true},
// else from the start) and writes an animated GIF with Python/PIL. Prints console errors and FPS.
// A lock file keeps captures one at a time (shared GPU, shared Mac with production).
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const [, , kind, hash, out, w = "1440", h = "900", actionsJson = "[]", secs = "6", fps = "24"] = process.argv;
const exe = "/Users/armaandave/Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing";
const lock = "/tmp/claude-501/spine-world.capture.lock"; // one GPU capture at a time, across every checkout
for (let i = 0; ; i++) {
  try { fs.mkdirSync(lock); break; } catch { if (i > 600) { fs.rmSync(lock, { recursive: true, force: true }); } await new Promise((r) => setTimeout(r, 500)); }
}
const release = () => fs.rmSync(lock, { recursive: true, force: true });
process.on("exit", release);
const browser = await chromium.launch({ executablePath: exe, headless: true, args: ["--enable-gpu", "--ignore-gpu-blocklist", "--use-angle=metal", "--disable-background-timer-throttling", "--disable-renderer-backgrounding"] });
try {
  const page = await browser.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 2 });
  page.on("console", (m) => { if (["error", "warning"].includes(m.type())) console.log(`console.${m.type()}: ${m.text().slice(0, 300)}`); });
  page.on("pageerror", (e) => console.log(`pageerror: ${e.message}`));
  await page.goto(`${process.env.CAPTURE_BASE || "http://127.0.0.1:5199/"}#${hash}`, { waitUntil: "load", timeout: 90000 });
  await page.waitForFunction(() => window.spine, null, { timeout: 90000 }).catch(() => console.log("warning: window.spine never appeared"));
  await page.waitForTimeout(1500);
  const frames = [];
  let cdp = null, recording = false;
  const startRec = async () => {
    if (recording) return; recording = true;
    cdp = await page.context().newCDPSession(page);
    cdp.on("Page.screencastFrame", async (f) => { frames.push(Buffer.from(f.data, "base64")); try { await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }); } catch {} });
    await cdp.send("Page.startScreencast", { format: "jpeg", quality: 82, maxWidth: +w, maxHeight: +h, everyNthFrame: 1 });
  };
  const actions = JSON.parse(actionsJson);
  if (kind === "video" && !actions.some((a) => a.record)) await startRec();
  const t0 = Date.now();
  for (const a of actions) {
    if (a.record) await startRec();
    if (a.wait) await page.waitForTimeout(a.wait);
    if (a.click) await page.mouse.click(a.click[0], a.click[1]);
    if (a.move) await page.mouse.move(a.move[0], a.move[1], { steps: 20 });
    if (a.key) await page.keyboard.press(a.key);
    if (a.wheel) { await page.mouse.move(a.wheel[0], a.wheel[1]); for (let k = 0; k < 8; k++) { await page.mouse.wheel(0, a.wheel[2] / 8); await page.waitForTimeout(30); } }
    if (a.drag) { await page.mouse.move(...a.drag[0]); await page.mouse.down(); await page.mouse.move(...a.drag[1], { steps: a.drag[2] || 30 }); await page.mouse.up(); }
    if (a.eval) console.log("eval:", JSON.stringify(await page.evaluate(a.eval)));
    if (a.shot) await page.screenshot({ path: a.shot });
  }
  const fpsMeasured = await page.evaluate(() => new Promise((r) => { let n = 0; const s = performance.now(); const f = () => { n++; if (performance.now() - s < 1000) requestAnimationFrame(f); else r(n); }; requestAnimationFrame(f); }));
  console.log("fps:", fpsMeasured);
  if (kind === "video") {
    const left = +secs * 1000 - (Date.now() - t0);
    if (left > 0) await page.waitForTimeout(left);
    await cdp?.send("Page.stopScreencast").catch(() => {});
    const dir = out.endsWith(".gif") ? fs.mkdtempSync("/tmp/claude-501/cap-") : out;
    fs.mkdirSync(dir, { recursive: true });
    frames.forEach((b, i) => fs.writeFileSync(path.join(dir, `f${String(i).padStart(4, "0")}.jpg`), b));
    console.log("frames:", frames.length);
    if (out.endsWith(".gif")) {
      execFileSync("uv", ["run", "--no-project", "--with", "pillow", "python", "-c", `
import glob,sys
from PIL import Image
fs=sorted(glob.glob(sys.argv[1]+'/f*.jpg'))
step=max(1,round(len(fs)/(${+secs}*${+fps})))
fs=fs[::step]
ims=[Image.open(f).convert('RGB').resize((${Math.round(+w * 0.6)},${Math.round(+h * 0.6)}), Image.LANCZOS) for f in fs]
ims=[i.quantize(colors=200, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for i in ims]
ims[0].save(sys.argv[2], save_all=True, append_images=ims[1:], duration=int(1000/${+fps}), loop=0, optimize=True)
print('gif', len(ims), 'frames')
`, dir, out], { stdio: "inherit" });
      fs.rmSync(dir, { recursive: true, force: true });
    }
  } else {
    await page.screenshot({ path: out });
  }
  console.log("saved", out);
} finally { await browser.close(); release(); }
