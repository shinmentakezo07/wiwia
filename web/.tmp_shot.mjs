// Headless screenshot helper over raw CDP (no playwright install needed).
// Reads the master key from ../.env without ever printing it.
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";

const env = readFileSync(new URL("../.env", import.meta.url), "utf8");
const key = (env.match(/^WIWI_MASTER_KEY=(.*)$/m)?.[1] ?? "").trim().replace(/^["']|["']$/g, "");
const BASE = process.env.BASE ?? "http://localhost:4000";
const pages = (process.argv[2] ?? "/console,/console/usage").split(",");
const W = Number(process.env.W ?? 1440);
mkdirSync("/tmp/shots", { recursive: true });

const chrome = spawn("/usr/bin/google-chrome", [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
  "--remote-debugging-port=9333", `--window-size=${W},1000`, "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try {
    const list = await (await fetch("http://127.0.0.1:9333/json")).json();
    target = list.find((t) => t.type === "page");
  } catch {}
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const pending = new Map();
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
const send = (method, params = {}) =>
  new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });

await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: W, height: 1000, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: BASE + "/login" });
await sleep(1500);
await send("Runtime.evaluate", { expression: `localStorage.setItem("wiwi.master_key", ${JSON.stringify(key)})` });

for (const p of pages) {
  await send("Page.navigate", { url: BASE + p });
  await sleep(4500);
  const { result } = await send("Runtime.evaluate", {
    expression: "JSON.stringify({h: document.documentElement.scrollHeight, url: location.pathname})",
    returnByValue: true,
  });
  const info = JSON.parse(result.result.value);
  const h = Math.min(info.h, 6000);
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: h, deviceScaleFactor: 1, mobile: false });
  await sleep(800);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  const name = `/tmp/shots/${p.replace(/\W+/g, "_") || "root"}_${W}.png`;
  writeFileSync(name, Buffer.from(shot.result.data, "base64"));
  console.log(name, info.url, h);
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: 1000, deviceScaleFactor: 1, mobile: false });
}
ws.close();
chrome.kill();
