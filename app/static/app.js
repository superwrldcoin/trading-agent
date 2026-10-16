"use strict";
const $ = s => document.querySelector(s);
const esc = Report.esc;
const DEFAULTS = { equity: "", risk: "", theme: "auto", action: "quick", anim: true, ladder: true, symbols: [] };
const BASE_CHIPS = [["BTC long 20x @ 98,400", "BTC long, 20x, entry 98,400, how does it look?"], ["gold short 5x", "gold short 5x"],
  ["MSFT long, sized", "MSFT long @ 513.94 stop 487.30 account 10k risk 1%"], ["how does BCH look?", "how does BCH look?"]];
let settings = Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem("ta_settings") || "{}"));
let current = null;   // {id, es, kind}
let lastMarkdown = "";

// ---- run lifecycle ----
function setBusy(b){
  ["#quick", "#agent", "#portfolio"].forEach(s => $(s).disabled = b);
  $("#cancel").hidden = !b; document.body.classList.toggle("busy", b);
}
function addStep(id, label){
  const ol = $("#steps");
  ol.querySelectorAll("li.active").forEach(li => { li.classList.remove("active"); li.classList.add("done"); });
  const li = document.createElement("li"); li.className = "active"; li.dataset.id = id;
  li.innerHTML = `<span class="dot"></span><span>${esc(label)}</span>`; ol.appendChild(li);
}
async function post(path, body){
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Trading-Agent": "1" }, body: JSON.stringify(body) });
  return r.json();
}
async function run(kind){
  if (current) return;
  const text = kind === "portfolio" ? "portfolio" : $("#q").value.trim();
  if (!text){ const q = $("#q"); q.classList.remove("nudge"); void q.offsetWidth; q.classList.add("nudge"); q.focus(); return; }
  $("#steps").innerHTML = ""; $("#progress").hidden = false; $("#progress .bar").classList.remove("finished");
  $("#skeleton").hidden = !$("#results").hidden; $("#results").classList.add("stale"); setBusy(true); $("#status").textContent = "Starting...";
  let job;
  try { job = await post("/api/run", { kind, text, equity: $("#equity").value || null, risk_pct: $("#risk").value || null }); }
  catch(e){ return finish({ ok: false, markdown: "**Request failed:** " + e }, kind); }
  if (!job.id) return finish({ ok: false, markdown: "**Error:** " + (job.error || "could not start") }, kind);
  const es = new EventSource(`/api/jobs/${job.id}/events`); current = { id: job.id, es, kind };
  es.onmessage = m => {
    const ev = JSON.parse(m.data);
    if (ev.type === "step") addStep(ev.id, ev.label);
    else if (ev.type === "tick") $("#status").textContent = `${kind === "agent" ? "Full agent running" : "Working"}... ${Math.round(ev.t)}s`;
    else if (ev.type === "done"){ es.close(); finish(ev.result, kind); }
    else if (ev.type === "cancelled"){ es.close(); finish(null, kind); }
  };
  es.onerror = () => { if (current && current.es === es){ es.close(); finish({ ok: false, markdown: "**Connection to the local server was lost.**" }, kind); } };
}
function finish(res, kind){
  current = null; setBusy(false); $("#skeleton").hidden = true; $("#results").classList.remove("stale");
  $("#steps").querySelectorAll("li").forEach(li => { li.classList.remove("active"); li.classList.add("done"); });
  $("#progress .bar").classList.add("finished");
  if (!res){ $("#status").textContent = "Cancelled"; $("#progress").hidden = true; return; }
  showResult(res);
  $("#status").textContent = (res.ok ? "Done" : "Finished with errors") + (res.seconds !== undefined ? ` in ${res.seconds}s` : "") +
    (res.cost_usd != null ? ` · cost $${Number(res.cost_usd).toFixed(2)}` : (kind === "quick" ? " · free" : ""));
  setTimeout(() => { $("#progress").hidden = true; }, 900);
}
function showResult(res){
  lastMarkdown = res.markdown || "**Error**";
  const r = $("#results"); r.hidden = false; r.classList.remove("enter"); void r.offsetWidth; r.classList.add("enter");
  Report.render($("#out"), lastMarkdown, { onCopy: copyReport });
  r.scrollIntoView({ behavior: animOn() ? "smooth" : "auto", block: "start" });
}
async function cancel(){ if (current) await post(`/api/jobs/${current.id}/cancel`, { text: "cancel" }); }
async function copyReport(){
  try { await navigator.clipboard.writeText(lastMarkdown); toast("Report copied"); }
  catch(e){ toast("Couldn't copy: the browser blocked clipboard access"); }
}
let toastTimer = 0;
function toast(msg){
  const t = $("#toast"); t.textContent = msg; t.classList.remove("show"); void t.offsetWidth; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2400);
}
const animOn = () => settings.anim && !matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---- settings, history, chips ----
function applySettings(){
  const root = document.documentElement;
  root.dataset.theme = settings.theme; root.dataset.anim = settings.anim ? "on" : "off"; root.dataset.ladder = settings.ladder ? "on" : "off";
  if (!$("#equity").value) $("#equity").value = settings.equity || "";
  if (!$("#risk").value) $("#risk").value = settings.risk || "";
  $("#quick").className = settings.action === "quick" ? "" : "secondary";
  $("#agent").className = settings.action === "agent" ? "" : "secondary";
  const chips = $("#chips"); chips.innerHTML = "";
  BASE_CHIPS.concat(settings.symbols.map(s => [s, `how does ${s} look?`])).forEach(([label, ex], k) => {
    const c = document.createElement("button"); c.className = "chip"; c.type = "button"; c.textContent = label; c.style.setProperty("--i", k);
    c.onclick = () => { $("#q").value = ex; $("#q").focus(); }; chips.appendChild(c);
  });
}
function openDrawer(id){
  document.querySelectorAll(".drawer").forEach(d => { d.classList.toggle("open", d.id === id); d.setAttribute("aria-hidden", d.id !== id); });
  $("#scrim").hidden = !id; document.body.classList.toggle("drawer-open", !!id);
  document.querySelectorAll(".appbar .seg-btn").forEach(b => b.classList.toggle("active",
    (id === "settings" && b.id === "settingsBtn") || (id === "history" && b.id === "historyBtn") || (!id && b.id === "analyzeBtn")));
  if (id){ const f = document.getElementById(id).querySelector("input, select, button"); if (f) setTimeout(() => f.focus(), 50); }
}
function closeDrawers(){ openDrawer(""); }
async function loadConfig(){
  try {
    const c = await (await fetch("/api/config")).json();
    $("#agent").disabled = !c.claude_cli; if (!c.claude_cli) $("#agent").title = "Claude Code CLI not found on this machine";
    const pill = $("#cliPill"); pill.hidden = false; pill.className = "cli-pill " + (c.claude_cli ? "on" : "off");
    pill.textContent = c.claude_cli ? "Full agent ready" : "Quick check only"; pill.title = c.claude_cli ? "Claude Code CLI found" : "Claude Code CLI not found";
    const sel = $("#s_symbols"); sel.innerHTML = c.watchlist.map(s => `<option ${settings.symbols.includes(s) ? "selected" : ""}>${esc(s)}</option>`).join("");
    $("#rulesBox").textContent = c.rules ? JSON.stringify(c.rules, null, 1) : "Rules: none set yet.";
  } catch(e){ $("#rulesBox").textContent = "unavailable"; }
}
function openSettings(){
  $("#s_equity").value = settings.equity; $("#s_risk").value = settings.risk; $("#s_theme").value = settings.theme;
  $("#s_action").value = settings.action; $("#s_anim").checked = settings.anim; $("#s_ladder").checked = settings.ladder; openDrawer("settings");
}
function saveSettings(){
  settings = { equity: $("#s_equity").value, risk: $("#s_risk").value, theme: $("#s_theme").value, action: $("#s_action").value,
    anim: $("#s_anim").checked, ladder: $("#s_ladder").checked, symbols: [...$("#s_symbols").selectedOptions].map(o => o.value) };
  localStorage.setItem("ta_settings", JSON.stringify(settings));
  $("#equity").value = settings.equity; $("#risk").value = settings.risk; applySettings(); closeDrawers(); toast("Settings saved");
  if (lastMarkdown && !$("#results").hidden) Report.render($("#out"), lastMarkdown, { onCopy: copyReport });
}
function ago(utc){
  const t = Date.parse(String(utc).replace(" ", "T").replace(/ ?UTC$/, "") + (/Z|[+-]\d\d:?\d\d$/.test(utc) ? "" : "Z"));
  if (isNaN(t)) return String(utc);
  const s = (Date.now() - t) / 1000;
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
}
async function openHistory(){
  const list = $("#histList"); list.innerHTML = `<li class="empty">Loading...</li>`; openDrawer("history");
  let j; try { j = await (await fetch("/api/history")).json(); } catch(e){ list.innerHTML = `<li class="empty">History unavailable.</li>`; return; }
  list.innerHTML = j.items.length ? "" : `<li class="empty">No runs yet. Your analyses will show up here.</li>`;
  const icon = { quick: "⚡", agent: "✦", portfolio: "◔" };
  j.items.forEach((it, k) => {
    const li = document.createElement("li"); li.tabIndex = 0; li.style.setProperty("--i", Math.min(k, 15));
    li.innerHTML = `<span class="h-icon k-${esc(it.kind)}">${icon[it.kind] || "•"}</span><span class="h-body"><b>${esc(String(it.text).slice(0, 70))}</b>` +
      `<small>${esc(it.kind)} · ${esc(ago(it.utc))}${it.ok ? "" : ' · <span class="st st-fail">✕ failed</span>'}</small></span>`;
    const open = async () => { const d = await (await fetch("/api/history/" + it.id)).json(); closeDrawers(); showResult(d); };
    li.onclick = open; li.onkeydown = e => { if (e.key === "Enter") open(); };
    list.appendChild(li);
  });
}

$("#quick").onclick = () => run("quick"); $("#agent").onclick = () => run("agent"); $("#portfolio").onclick = () => run("portfolio");
$("#cancel").onclick = cancel; $("#settingsBtn").onclick = openSettings; $("#historyBtn").onclick = openHistory;
$("#analyzeBtn").onclick = () => { closeDrawers(); $("#q").focus(); window.scrollTo({ top: 0, behavior: animOn() ? "smooth" : "auto" }); };
$("#saveSettings").onclick = saveSettings; document.querySelectorAll("[data-close]").forEach(b => b.onclick = closeDrawers);
$("#scrim").onclick = closeDrawers;
$("#q").addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) run(settings.action); });
document.addEventListener("keydown", e => {
  if (e.key === "Escape") return closeDrawers();
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "/"){ e.preventDefault(); $("#analyzeBtn").click(); }
  else if (e.key === "h" || e.key === "H") openHistory();
  else if (e.key === ",") openSettings();
});
document.addEventListener("pointermove", e => {   // soft spotlight that follows the cursor on panels
  const p = e.target.closest && e.target.closest(".panel, .tile, .card");
  if (p){ const r = p.getBoundingClientRect(); p.style.setProperty("--mx", `${e.clientX - r.left}px`); p.style.setProperty("--my", `${e.clientY - r.top}px`); }
}, { passive: true });
applySettings(); loadConfig();
