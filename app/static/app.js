"use strict";
const $ = s => document.querySelector(s);
const DEFAULTS = { equity: "", risk: "", theme: "auto", action: "quick", anim: true, ladder: true, symbols: [] };
const BASE_CHIPS = [["BTC long 20x @ 98,400", "BTC long, 20x, entry 98,400, how does it look?"], ["gold short 5x", "gold short 5x"],
  ["MSFT long, sized", "MSFT long @ 513.94 stop 487.30 account 10k risk 1%"], ["how does BCH look?", "how does BCH look?"]];
let settings = Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem("ta_settings") || "{}"));
let current = null;   // {id, es, t0}

function esc(s){ return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function inline(s){
  return esc(s).replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>")
    .replace(/\bFAIL\b/g,'<span class="fail">FAIL</span>').replace(/\bWARN\b/g,'<span class="warn">WARN</span>')
    .replace(/(Grade:?\s*)([ABC])(?![A-Za-z])/g,'$1<span class="badge $2">$2</span>');
}
function md(text){
  const L = text.replace(/\r/g,"").split("\n"); let h = "", i = 0;
  while (i < L.length){
    const l = L[i];
    if (l.startsWith("```")){ let c=[]; i++; while(i<L.length && !L[i].startsWith("```")) c.push(L[i++]); i++;
      h += "<pre>"+esc(c.join("\n"))+"</pre>"; continue; }
    if (/^\|.*\|\s*$/.test(l)){ let rows=[]; while(i<L.length && /^\|.*\|\s*$/.test(L[i])) rows.push(L[i++]);
      const cells = r => r.trim().slice(1,-1).split("|").map(c=>c.trim());
      const body = rows.filter(r => !/^\|[\s:|-]+\|\s*$/.test(r));
      h += "<table>" + body.map((r,k)=>`<tr style="animation-delay:${Math.min(k,20)*30}ms">`+cells(r).map(c=>k?`<td>${inline(c)}</td>`:`<th>${inline(c)}</th>`).join("")+"</tr>").join("") + "</table>";
      continue; }
    const m = l.match(/^(#{1,4})\s+(.*)/); if (m){ h += `<h${m[1].length+1}>${inline(m[2])}</h${m[1].length+1}>`; i++; continue; }
    if (/^\s*[-*]\s+/.test(l)){ let it=[]; while(i<L.length && /^\s*[-*]\s+/.test(L[i])) it.push(L[i++].replace(/^\s*[-*]\s+/,""));
      h += "<ul>"+it.map(x=>`<li>${inline(x)}</li>`).join("")+"</ul>"; continue; }
    if (l.trim()===""){ i++; continue; }
    let p=[]; while(i<L.length && L[i].trim()!=="" && !/^(\||#|```|\s*[-*]\s)/.test(L[i])) p.push(L[i++]);
    h += "<p>"+inline(p.join(" "))+"</p>";
  }
  return h;
}

// ---- price ladder: built from the "Milestone | Price" table in the report ----
function ladderRows(markdown){
  const L = markdown.split("\n"); const rows = [];
  const start = L.findIndex(l => /^\|\s*Milestone\s*\|\s*Price/i.test(l));
  if (start < 0) return rows;
  for (let i = start + 2; i < L.length && /^\|.*\|\s*$/.test(L[i]); i++){
    const c = L[i].trim().slice(1,-1).split("|").map(x=>x.trim());
    const price = parseFloat((c[1]||"").replace(/[^0-9.\-]/g,""));
    if (!isNaN(price)) rows.push({ name: c[0].replace(/\*/g,""), price, r: c[3] || "" });
  }
  return rows;
}
function drawLadder(markdown){
  const wrap = $("#ladderWrap"), rows = ladderRows(markdown).sort((a,b)=>b.price-a.price);
  if (!settings.ladder || rows.length < 2){ wrap.hidden = true; return; }
  const hi = rows[0].price, lo = rows[rows.length-1].price, span = (hi - lo) || 1;
  const H = Math.max(160, rows.length * 34), pad = 16, y = p => pad + (hi - p) / span * (H - 2 * pad);
  const colour = n => /stop|invalid|liq/i.test(n) ? "var(--fail)" : /target|T\d|runner/i.test(n) ? "var(--ok)" : "var(--accent)";
  let svg = `<svg viewBox="0 0 640 ${H}" role="img" aria-label="Price ladder"><line x1="150" y1="${pad}" x2="150" y2="${H-pad}" stroke="var(--line)" stroke-width="2"/>`;
  let lastY = -99;
  rows.forEach((r, k) => {
    let yy = y(r.price); if (yy - lastY < 16) yy = lastY + 16; lastY = yy;
    svg += `<g class="rung" style="animation-delay:${k*60}ms"><circle cx="150" cy="${yy}" r="6" fill="${colour(r.name)}"/>` +
      `<text x="140" y="${yy+4}" text-anchor="end">${esc(r.price.toLocaleString(undefined,{maximumFractionDigits:2}))}</text>` +
      `<text x="164" y="${yy+4}">${esc(r.name)}${r.r && r.r !== "-" ? " · " + esc(r.r) : ""}</text></g>`;
  });
  $("#ladder").innerHTML = svg + "</svg>"; wrap.hidden = false;
}

// ---- run lifecycle ----
function setBusy(b){
  ["#quick","#agent","#portfolio"].forEach(s => $(s).disabled = b);
  $("#cancel").hidden = !b;
}
function addStep(id, label){
  const ol = $("#steps");
  ol.querySelectorAll("li.active").forEach(li => { li.classList.remove("active"); li.classList.add("done"); });
  const li = document.createElement("li"); li.className = "active"; li.dataset.id = id;
  li.innerHTML = `<span class="dot"></span><span>${esc(label)}</span>`; ol.appendChild(li);
}
async function post(path, body){
  const r = await fetch(path, { method:"POST", headers:{ "Content-Type":"application/json", "X-Trading-Agent":"1" }, body: JSON.stringify(body) });
  return r.json();
}
async function run(kind){
  if (current) return;
  const text = kind === "portfolio" ? "portfolio" : $("#q").value.trim(); if (!text) return;
  $("#steps").innerHTML = ""; $("#progress").hidden = false; $("#progress .bar").classList.remove("finished");
  $("#skeleton").hidden = false; $("#results").hidden = true; setBusy(true); $("#status").textContent = "Starting...";
  let job;
  try { job = await post("/api/run", { kind, text, equity: $("#equity").value || null, risk_pct: $("#risk").value || null }); }
  catch(e){ return finish({ ok:false, markdown:"**Request failed:** " + e }, kind); }
  if (!job.id) return finish({ ok:false, markdown:"**Error:** " + (job.error || "could not start") }, kind);
  const es = new EventSource(`/api/jobs/${job.id}/events`); current = { id: job.id, es, kind };
  es.onmessage = m => {
    const ev = JSON.parse(m.data);
    if (ev.type === "step") addStep(ev.id, ev.label);
    else if (ev.type === "tick") $("#status").textContent = `${kind === "agent" ? "Full agent running" : "Working"}... ${Math.round(ev.t)}s`;
    else if (ev.type === "done"){ es.close(); finish(ev.result, kind); }
    else if (ev.type === "cancelled"){ es.close(); finish(null, kind); }
  };
  es.onerror = () => { if (current && current.es === es){ es.close(); finish({ ok:false, markdown:"**Connection to the local server was lost.**" }, kind); } };
}
function finish(res, kind){
  current = null; setBusy(false); $("#skeleton").hidden = true;
  $("#steps").querySelectorAll("li").forEach(li => { li.classList.remove("active"); li.classList.add("done"); });
  $("#progress .bar").classList.add("finished");
  if (!res){ $("#status").textContent = "Cancelled"; $("#progress").hidden = true; return; }
  showResult(res);
  $("#status").textContent = (res.ok ? "Done" : "Finished with errors") + (res.seconds !== undefined ? ` in ${res.seconds}s` : "") +
    (res.cost_usd != null ? ` · cost $${Number(res.cost_usd).toFixed(2)}` : (kind === "quick" ? " · free" : ""));
  setTimeout(() => { $("#progress").hidden = true; }, 900);
}
function showResult(res){
  const mdText = res.markdown || "**Error**";
  $("#out").innerHTML = md(mdText); drawLadder(mdText);
  const r = $("#results"); r.hidden = false; r.querySelectorAll(".panel").forEach(p => { p.style.animation = "none"; void p.offsetWidth; p.style.animation = ""; });
}
async function cancel(){ if (current) await post(`/api/jobs/${current.id}/cancel`, { text:"cancel" }); }

// ---- settings, history, chips ----
function applySettings(){
  const root = document.documentElement;
  root.dataset.theme = settings.theme; root.dataset.anim = settings.anim ? "on" : "off";
  if (!$("#equity").value) $("#equity").value = settings.equity || "";
  if (!$("#risk").value) $("#risk").value = settings.risk || "";
  $("#quick").className = settings.action === "quick" ? "" : "secondary";
  $("#agent").className = settings.action === "agent" ? "" : "secondary";
  const chips = $("#chips"); chips.innerHTML = "";
  BASE_CHIPS.concat(settings.symbols.map(s => [s, `how does ${s} look?`])).forEach(([label, ex]) => {
    const c = document.createElement("span"); c.className = "chip"; c.textContent = label;
    c.onclick = () => { $("#q").value = ex; $("#q").focus(); }; chips.appendChild(c);
  });
}
function openDrawer(id){ document.querySelectorAll(".drawer").forEach(d => { d.classList.toggle("open", d.id === id); d.setAttribute("aria-hidden", d.id !== id); }); }
function closeDrawers(){ openDrawer(""); }
async function loadConfig(){
  try {
    const c = await (await fetch("/api/config")).json();
    $("#agent").disabled = !c.claude_cli; if (!c.claude_cli) $("#agent").title = "Claude Code CLI not found on this machine";
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
  $("#equity").value = settings.equity; $("#risk").value = settings.risk; applySettings(); closeDrawers();
}
async function openHistory(){
  const list = $("#histList"); list.innerHTML = "<li>loading...</li>"; openDrawer("history");
  const j = await (await fetch("/api/history")).json();
  list.innerHTML = j.items.length ? "" : "<li>No runs yet.</li>";
  j.items.forEach(it => {
    const li = document.createElement("li");
    li.innerHTML = `<b>${esc(it.kind)}</b> ${it.ok ? "" : '<span class="fail">failed</span>'}<small>${esc(String(it.text).slice(0, 70))} · ${esc(it.utc)}</small>`;
    li.onclick = async () => { const d = await (await fetch("/api/history/" + it.id)).json(); showResult(d); closeDrawers(); };
    list.appendChild(li);
  });
}

$("#quick").onclick = () => run("quick"); $("#agent").onclick = () => run("agent"); $("#portfolio").onclick = () => run("portfolio");
$("#cancel").onclick = cancel; $("#settingsBtn").onclick = openSettings; $("#historyBtn").onclick = openHistory;
$("#saveSettings").onclick = saveSettings; document.querySelectorAll("[data-close]").forEach(b => b.onclick = closeDrawers);
$("#q").addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) run(settings.action); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawers(); });
applySettings(); loadConfig();
