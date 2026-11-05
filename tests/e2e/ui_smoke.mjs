// UI smoke test: drives headless Chrome over the DevTools protocol (no npm deps; Node 22+ for global WebSocket).
//
//   node tests/e2e/ui_smoke.mjs [--base http://127.0.0.1:8799] [--out tools/output/e2e] [--chrome <path>]
//
// Start the app first with an isolated paper store, e.g.
//   TA_PAPER_PATH=tools/output/paper-smoke.json python app/server.py --no-browser --port 8799
// Checks navigation between Home / Analyze / Paper (clicks, keyboard, Back/Forward, drawers), the
// "Paper this setup" hand-off, and the daily challenge. Writes a screenshot per view. Exit 1 on any failure.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const BASE = arg("--base", "http://127.0.0.1:8799");
const OUT = arg("--out", "tools/output/e2e");
const CHROME = arg("--chrome", [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium"].find(existsSync));
const PORT = 9333 + Math.floor(Math.random() * 500);
const sleep = ms => new Promise(r => setTimeout(r, ms));

mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "ta-e2e-"))}`,
  "--window-size=1280,900", "about:blank"], { stdio: "ignore" });

let ws, seq = 0;
const pending = new Map();
function send(method, params = {}){
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((res, rej) => pending.set(id, { res, rej }));
}
async function connect(){
  for (let i = 0; i < 50; i++){
    try {
      const pages = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = pages.find(p => p.type === "page");
      if (page){
        ws = new WebSocket(page.webSocketDebuggerUrl);
        await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
        ws.onmessage = m => { const d = JSON.parse(m.data); const p = pending.get(d.id);
          if (p){ pending.delete(d.id); d.error ? p.rej(new Error(d.error.message)) : p.res(d.result); } };
        return;
      }
    } catch { /* chrome still starting */ }
    await sleep(200);
  }
  throw new Error("could not reach Chrome DevTools");
}
async function js(expr){
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`page error in ${expr}: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
  return r.result.value;
}
async function until(expr, ms = 8000){
  const t0 = Date.now();
  while (Date.now() - t0 < ms){ if (await js(expr)) return true; await sleep(100); }
  return false;
}
async function shot(name){
  const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(join(OUT, `${name}.png`), Buffer.from(r.data, "base64"));
}
const shown = sel => `(() => { const e = document.querySelector(${JSON.stringify(sel)}); return !!e && e.offsetParent !== null; })()`;
const state = `({ view: document.querySelector("main").dataset.view, hash: location.hash,
  home: ${shown("#homeView")}, analyze: ${shown("#inputPanel")}, paper: ${shown("#paperView")},
  active: [...document.querySelectorAll(".appbar .seg-btn.active")].map(b => b.id) })`;

let failed = 0;
function check(name, ok, detail = ""){
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : "  " + detail}`);
  if (!ok) failed++;
}
async function expectView(label, v, hashes = [`#${v}`]){
  const ok = await until(`(${state}).view === "${v}"`);
  const s = await js(state);
  const only = { home: s.home && !s.analyze && !s.paper, analyze: s.analyze && !s.home && !s.paper, paper: s.paper && !s.home && !s.analyze }[v];
  check(label, ok && only && hashes.includes(s.hash) && s.active.join() === `${v}Btn`, JSON.stringify(s));
}

try {
  await connect();
  await send("Page.enable"); await send("Runtime.enable");
  let ready = false;
  for (let i = 0; i < 15 && !ready; i++){        // the server may still be starting: retry until the app loads
    await send("Page.navigate", { url: `${BASE}/` });
    ready = await until(`document.readyState === "complete" && !!window.Paper && !!window.Home`, 2000);
  }
  if (!ready) throw new Error(`app did not load from ${BASE}`);
  await js(`localStorage.setItem("ta_notice", "1"), document.querySelector("#notice").hidden = true`);

  await expectView("lands on Home", "home", ["", "#home"]);   // a bare URL is Home
  // the player card waits on /api/paper, which re-marks open paper trades with live bars: allow for the network
  const homeReady = `!!document.querySelector("#hPlay") && document.querySelectorAll(".mode[data-mode]").length >= 3 && !!document.querySelector("#hPlayer .xp") && !!document.querySelector("#hDaily .quest")`;
  check("home has hero, modes, player card, daily challenge", await until(homeReady, 30000));
  await sleep(900); await shot("home");

  check("daily challenge names a symbol and a live countdown", await until(
    `/^\\d\\d:\\d\\d:\\d\\d$/.test(document.querySelector("#qClock")?.textContent || "") && !!document.querySelector(".q-sym")?.textContent`));
  await js(`document.querySelector('.h-jump a[href="#hModes"]').click()`); await sleep(300);
  const afterJump = await js(state);
  check("section strip scrolls without changing the view", afterJump.view === "home" && !afterJump.hash.includes("hModes"), JSON.stringify(afterJump));
  await js(`document.querySelector('.mode[data-mode="daily"]').click()`); await sleep(300);
  check("Daily mode tile stays on Home", (await js(state)).view === "home");
  const sym = await js(`document.querySelector(".q-sym").textContent`);
  if (await js(`!!document.querySelector("#qGo")`)){
    await js(`document.querySelector("#qGo").click()`); await expectView("'Take the challenge' → Paper", "paper");
    check("ticket switched to the challenge symbol with its banner", await until(
      `document.querySelector("#tSym").value === ${JSON.stringify(sym)} && !document.querySelector("#tChallenge").hidden`),
      JSON.stringify(await js(`({ sym: tSym.value, banner: tChallenge.hidden })`)));
    await js(`document.querySelector("#homeBtn").click()`); await expectView("back to Home", "home");
  }

  await js(`document.querySelector("#analyzeBtn").click()`); await expectView("Home → Analyze (nav click)", "analyze");
  await js(`document.querySelector("#paperBtn").click()`); await expectView("Analyze → Paper (nav click)", "paper");
  await sleep(900); await shot("paper");
  await js(`document.querySelector("#analyzeBtn").click()`); await expectView("Paper → Analyze (the old bug)", "analyze");
  await shot("analyze");
  await js(`document.querySelector("#paperBtn").click()`); await expectView("Analyze → Paper again", "paper");
  await js(`document.querySelector("#homeBtn").click()`); await expectView("Paper → Home", "home");
  await js(`document.querySelector("#hPlay").click()`); await expectView("Home Play button → Paper", "paper");

  await js(`history.back()`); await expectView("Back → Home", "home");
  await js(`history.forward()`); await expectView("Forward → Paper", "paper");

  await js(`document.activeElement.blur(); document.dispatchEvent(new KeyboardEvent("keydown", { key: "/" }))`);
  await expectView("'/' shortcut → Analyze", "analyze");
  await js(`document.activeElement.blur(); document.dispatchEvent(new KeyboardEvent("keydown", { key: "p" }))`);
  await expectView("'p' shortcut → Paper", "paper");

  await js(`document.querySelector("#settingsBtn").click()`);
  check("Settings drawer opens over Paper", await until(`document.querySelector("#settings").classList.contains("open")`));
  await js(`document.querySelector("#settings [data-close]").click()`);
  await until(`!document.querySelector("#settings").classList.contains("open")`);
  check("closing the drawer keeps Paper active", (await js(state)).active.join() === "paperBtn", JSON.stringify(await js(state)));

  // quick-check hand-off: show a result that carries a plan, then "Paper this setup"
  await js(`document.querySelector("#analyzeBtn").click()`); await expectView("back to Analyze for the hand-off", "analyze");
  await js(`showResult({ ok: true, markdown: "## BTC/USDT long quick check\\nsmoke", plan: { symbol: "BTC/USDT", side: "short", entry: null, stop: 999999, targets: [1000, 900], leverage: 2 } })`);
  check("'Paper this setup' bar appears", await until(shown("#planBtn")));
  await js(`document.querySelector("#planBtn").click()`); await expectView("'Paper this setup' → Paper", "paper");
  check("ticket pre-filled (side, stop, targets, leverage)", await until(
    `document.querySelector("#tStop").value === "999999" && document.querySelector("#tTargets").value === "1000, 900" && document.querySelector("#tLev").value === "2" && document.querySelector(".side.short").classList.contains("active")`),
    JSON.stringify(await js(`({ stop: tStop.value, t: tTargets.value, lev: tLev.value })`)));

  // house rules switch: off from the ticket, trade without a stop, survives a reload, back on from Home
  await js(`window.confirm = () => true`);
  await until(`document.querySelector("#tRules").textContent === "Rules on"`);
  await js(`document.querySelector("#tRules").click()`);
  check("rules switched off from the ticket", await until(
    `tRules.textContent === "Rules off" && tStop.placeholder === "optional" && tLev.max === "125" && document.documentElement.dataset.rules === "off"`),
    JSON.stringify(await js(`({ pill: tRules.textContent, ph: tStop.placeholder, max: tLev.max })`)));
  await js(`tStop.value = ""; tTargets.value = ""; document.querySelector('#tSizeMode [data-v="size"]').click();`);
  await js(`tSize.value = "0.001"; tLev.value = "60"; tSize.dispatchEvent(new Event("input")); tLev.dispatchEvent(new Event("input"))`);
  check("no-stop ticket previews with the rules-off note", await until(`!!document.querySelector(".rules-off-note") && !tSubmit.disabled`, 15000),
    await js(`tPreview.textContent.slice(0, 160)`));
  const before = await js(`document.querySelectorAll("#pOpen .pos").length`);
  await js(`tSubmit.click()`);
  check("freestyle trade opens without a stop", await until(
    `document.querySelectorAll("#pOpen .pos").length > ${before} && !!document.querySelector("#pOpen .free-tag")`, 15000));
  await shot("paper-rules-off");
  await send("Page.navigate", { url: `${BASE}/#paper` });
  await until(`document.readyState === "complete" && !!window.Paper`);
  check("rules-off survives a reload (stored in app.db)", await until(`tRules.textContent === "Rules off"`));
  await js(`window.confirm = () => true; document.querySelector("#homeBtn").click()`); await expectView("to Home with rules off", "home");
  check("Home shows rules off and the Freestyle track", await until(`!!document.querySelector("#hRules.off") && !!document.querySelector(".fs-track")`, 10000));
  await sleep(500); await shot("home-rules-off");
  await js(`document.querySelector("#hrToggle").click()`);
  check("rules back on from the Home card", await until(`!document.querySelector("#hRules.off") && tRules.textContent === "Rules on"`));

  await js(`location.hash = "#analyze"`); await expectView("deep link #analyze", "analyze");
  await send("Page.navigate", { url: `${BASE}/#paper` });
  await until(`document.readyState === "complete" && !!window.Paper`); await expectView("reload on #paper stays on Paper", "paper");
} catch (e){
  check("driver", false, e.stack || String(e));
} finally {
  try { ws && ws.close(); } catch {}
  chrome.kill();
}
console.log(failed ? `\n${failed} check(s) failed` : `\nall checks passed; screenshots in ${OUT}`);
process.exit(failed ? 1 : 0);
