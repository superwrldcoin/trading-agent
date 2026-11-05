"use strict";
// Paper desk: simulated trades at live feed prices (tools/paper.py). You open every trade; the agent only pre-fills.
// Uses $, esc, post, toast, animOn from app.js (loaded first).
const Paper = (() => {
  const REFRESH_MS = 15000;
  let cfg = { watchlist: [], themes: {}, contracts: {}, rules: null };
  let side = "long", ord = "market", sizeMode = "risk";
  let lastPx = null, lastQuote = null, timer = 0, previewTimer = 0, lastSnap = null, lastPreview = null, updatedAt = 0;
  const shown = {};          // last values drawn in the tiles (for count-up)
  const marks = {};          // last mark per trade id (for flash)

  const n = (x, nd) => x == null || !isFinite(x) ? "—" : Number(x).toLocaleString("en-US",
    { minimumFractionDigits: nd ?? (Math.abs(x) < 10 && x !== 0 ? 4 : 2), maximumFractionDigits: nd ?? (Math.abs(x) < 10 && x !== 0 ? 4 : 2) });
  const usd = (x, signed) => x == null ? "—" : (signed && x > 0 ? "+" : x < 0 ? "−" : "") + "$" + n(Math.abs(x), 2);
  const pct = (x, signed = true) => x == null ? "—" : (signed && x > 0 ? "+" : "") + n(x, 2) + "%";
  const rr = x => x == null ? "—" : (x > 0 ? "+" : "") + n(x, 2) + "R";
  const tone = x => x == null || Math.abs(x) < 1e-9 ? "" : x > 0 ? "up" : "down";
  const num = v => { const s = String(v ?? "").replace(/[$,\s]/g, ""); return s === "" ? null : Number(s); };
  const visible = () => $("main").dataset.view === "paper" && !document.hidden;

  // ---- view switching (called by showView in app.js; navigate with go("paper")) ----
  function setView(v){
    $("#paperView").hidden = v !== "paper";
    if (v === "paper"){ refresh(); quote(); schedule(); }
    else clearInterval(timer);
  }
  function schedule(){ clearInterval(timer); timer = setInterval(() => { if (visible()){ refresh(); quote(); } }, REFRESH_MS); }

  // ---- config ----
  async function init(){
    try { cfg = Object.assign(cfg, await (await fetch("/api/config")).json()); } catch(e){ /* desk still works with defaults */ }
    const sel = $("#tSym");
    sel.innerHTML = cfg.watchlist.map(s => `<option>${esc(s)}</option>`).join("");
    const saved = localStorage.getItem("ta_paper_sym");
    if (saved && cfg.watchlist.includes(saved)) sel.value = saved;
    onSymbol(false);
    // deep links: #paper opens the desk (the router in app.js); #paper?symbol=BTC/USDT&side=long&stop=..&targets=a,b[&entry=..&leverage=..] pre-fills a ticket
    const m = location.hash.match(/^#paper\?(.*)$/);
    if (m){
      const q = new URLSearchParams(m[1]);
      if (q.get("symbol") && q.get("side") && q.get("stop") && q.get("targets"))
        prefill({ symbol: q.get("symbol").toUpperCase(), side: q.get("side"), stop: num(q.get("stop")),
          targets: q.get("targets").split(",").map(num).filter(x => x != null), entry: num(q.get("entry")), leverage: num(q.get("leverage")) });
    }
  }
  // the home page's tickers open the desk on a symbol
  function openSymbol(sym){
    go("paper");
    if (cfg.watchlist.includes(sym) && $("#tSym").value !== sym){ $("#tSym").value = sym; onSymbol(); }
  }
  const theme = () => cfg.themes[$("#tSym").value];
  const levCap = () => ((cfg.rules || {}).max_leverage || {})[theme()];

  // ---- ticket ----
  function setSide(s){
    side = s;
    document.querySelectorAll(".side-toggle .side").forEach(b => { const on = b.dataset.side === s; b.classList.toggle("active", on); b.setAttribute("aria-checked", on); });
    const go = $("#tSubmit"); go.className = "go " + s; go.textContent = `Open paper ${s}`;
    previewSoon();
  }
  function segPick(id, v){
    document.querySelectorAll(`#${id} .seg-btn`).forEach(b => b.classList.toggle("active", b.dataset.v === v));
  }
  function setOrd(v){ ord = v; segPick("tOrd", v); $("#tLimitWrap").hidden = v !== "limit"; if (v === "limit" && !$("#tLimit").value && lastPx) $("#tLimit").value = lastPx; previewSoon(); }
  function setSizeMode(v){
    sizeMode = v; segPick("tSizeMode", v);
    $("#tSizeLbl").textContent = { risk: "Risk %", size: "Units", contracts: "Contracts" }[v];
    $("#tSize").value = v === "risk" ? (settings.risk || 1) : v === "contracts" ? 1 : "";
    previewSoon();
  }
  function onSymbol(clear = true){
    const sym = $("#tSym").value; localStorage.setItem("ta_paper_sym", sym);
    const c = cfg.contracts[sym];
    document.querySelector('#tSizeMode [data-v="contracts"]').hidden = !c;
    if (!c && sizeMode === "contracts") setSizeMode("risk");
    if (clear){ $("#tStop").value = ""; $("#tTargets").value = ""; $("#tLimit").value = ""; }
    lastPx = null; $("#tPx").textContent = "—"; $("#tChg").textContent = ""; levLabel(); quote(); previewSoon(); banner();
  }
  function levLabel(){
    const v = Number($("#tLev").value), cap = rulesOn ? levCap() : null, out = $("#tLevOut");
    out.textContent = `${v}x` + (cap ? ` · your cap ${cap}x` : rulesOn ? "" : " · rules off"); out.classList.toggle("over", !!cap && v > cap);
  }
  // house rules switch (app.js owns it): off = optional stop/targets, leverage up to the margin math, no checks
  const LEV_MAX = { on: 50, off: 125 };
  function onRules(on){
    const pill = $("#tRules"); pill.textContent = on ? "Rules on" : "Rules off"; pill.className = "rules-pill " + (on ? "on" : "off");
    pill.title = on ? "House rules are on: click to trade without them" : "House rules are off: click to switch them back on";
    $("#tStop").placeholder = on ? "required (rules on)" : "optional";
    $("#tTargets").placeholder = on ? "T1, T2, T3" : "optional: T1, T2, T3";
    const lev = $("#tLev"); lev.max = on ? LEV_MAX.on : LEV_MAX.off;
    if (Number(lev.value) > Number(lev.max)) lev.value = lev.max;
    levLabel(); previewSoon(); banner();
  }
  document.addEventListener("rules", e => onRules(e.detail));
  $("#tRules").onclick = () => setRules(!rulesOn);
  function ticket(){
    const t = { symbol: $("#tSym").value, side, stop: num($("#tStop").value),
      targets: $("#tTargets").value.split(/[,\s/]+/).map(num).filter(x => x != null && isFinite(x)),
      leverage: Number($("#tLev").value), p_t1: Number($("#tP").value), note: $("#tNote").value, source: "ticket" };
    if (ord === "limit") t.limit = num($("#tLimit").value);
    const sz = num($("#tSize").value);
    if (sizeMode === "risk") t.risk_pct = sz; else if (sizeMode === "size") t.size = sz; else t.contracts = sz;
    return t;
  }
  function previewSoon(){ clearTimeout(previewTimer); previewTimer = setTimeout(preview, 350); }
  async function preview(){
    const t = ticket(), box = $("#tPreview");
    const planned = t.stop != null && t.targets.length, sized = sizeMode !== "risk" && t.size != null || t.contracts != null;
    if (rulesOn ? !planned : !(planned || sized)){
      lastPreview = null; $("#tSubmit").disabled = true;
      box.innerHTML = `<p class="muted">${rulesOn ? "Add a stop and at least one target to size the trade."
        : "Rules are off: add a stop and targets, or size by units/contracts and go without them."}</p>`; return;
    }
    let j; try { j = await post("/api/paper/preview", t); } catch(e){ box.innerHTML = `<p class="err">Preview failed: ${esc(e)}</p>`; return; }
    if (j.error){ lastPreview = null; box.innerHTML = `<p class="err">${esc(j.error)}</p>`; $("#tSubmit").disabled = true; return; }
    lastPreview = j.preview; $("#tSubmit").disabled = false;
    const p = j.preview, breaks = p.rules.filter(r => r.result === "BREAKS");
    box.innerHTML =
      `<div class="pv-grid">` +
      `<div><small>Size</small><b>${p.contracts != null ? `${n(p.contracts, 0)} ${esc(p.contract_name)}` : n(p.units, 4)}</b></div>` +
      `<div><small>Risk to ${p.risk_to === "liquidation" ? "liquidation" : "stop"}</small><b class="down">${usd(p.risk)}</b><em>${pct(p.risk_pct, false)} of equity</em></div>` +
      `<div><small>Notional / margin</small><b>${usd(p.notional)}</b><em>${usd(p.margin)}</em></div>` +
      `<div><small>Liquidation</small><b>${n(p.liquidation)}</b></div>` +
      `<div><small>Weighted R</small><b class="${p.weighted_R == null ? "" : p.weighted_R >= 2 ? "up" : "warn"}">${p.weighted_R == null ? "—" : n(p.weighted_R, 2)}</b></div>` +
      `</div>` + (t.stop != null && p.targets.length ? ladder(t, p) : "") +
      (p.rules_off ? `<p class="rules-off-note">House rules are off: nothing checked or blocked. The numbers above are information.</p>`
        : p.rules.length ? `<ul class="rules">${p.rules.map(r => `<li class="${r.result === "BREAKS" ? "brk" : "ok"}">${r.result === "BREAKS" ? "✕" : "✓"} ${esc(r.rule)} <small>${esc(r.value)} vs ${esc(r.limit)}</small></li>`).join("")}</ul>` : "") +
      (p.notes.length ? `<p class="muted small">${p.notes.map(esc).join(" · ")}</p>` : "") +
      (p.xp && p.xp.free ? `<div class="xp-preview xp-free"><b>Freestyle XP</b><small>${esc(p.xp.note)}</small></div>`
        : p.xp ? `<div class="xp-preview ${p.xp.total >= 0 ? "xp-up" : "xp-down"}"><b>${p.xp.total >= 0 ? "+" : ""}${p.xp.total} XP</b> if you open it` +
        `<small>${p.xp.parts.map(x => `${esc(x.label)} ${x.xp > 0 ? "+" : ""}${x.xp}`).join(" · ")}${p.xp.note ? ` · ${esc(p.xp.note)}` : ""}</small></div>` : "");
    $("#tSubmit").classList.toggle("warn", breaks.length > 0);
  }
  // horizontal stop → entry → targets ladder, with liquidation when it's on the chart
  function ladder(t, p){
    const pts = [{ v: t.stop, k: "stop", l: "Stop" }, { v: p.entry, k: "entry", l: "Entry" },
      ...p.targets.map((x, i) => ({ v: x.price, k: "tgt", l: `T${i + 1} ${rr(x.R)}` }))];
    if (p.liquidation > 0) pts.push({ v: p.liquidation, k: "liq", l: "Liq" });
    const lo = Math.min(...pts.map(x => x.v)), hi = Math.max(...pts.map(x => x.v)), span = hi - lo || 1;
    const pos = v => (side === "long" ? (v - lo) / span : (hi - v) / span) * 100;
    const e = pos(p.entry), s = pos(t.stop);
    return `<div class="ladder" role="img" aria-label="Stop, entry and targets">` +
      `<i class="risk" style="left:${Math.min(e, s)}%;width:${Math.abs(e - s)}%"></i>` +
      `<i class="reward" style="left:${e}%;width:${Math.max(...p.targets.map(x => pos(x.price))) - e}%"></i>` +
      pts.map((x, k) => `<span class="mk ${x.k}" style="left:${pos(x.v)}%;--i:${k}" title="${esc(x.l)} ${n(x.v)}"><b>${esc(x.l)}</b><em>${n(x.v)}</em></span>`).join("") + `</div>`;
  }
  async function submit(){
    if (!lastPreview) return;
    const breaks = lastPreview.rules_off ? [] : lastPreview.rules.filter(r => r.result === "BREAKS");
    if (breaks.length && !confirm(`This paper trade breaks ${breaks.length} of your rules:\n\n` +
      breaks.map(r => `• ${r.rule}: ${r.value} vs ${r.limit}`).join("\n") + "\n\nOpen it anyway (it's practice, but the journal will show it)?")) return;
    const btn = $("#tSubmit"); btn.disabled = true;
    try {
      const j = await post("/api/paper/open", ticket());
      if (j.error){ toast(j.error); return; }
      toast(`${j.id}: paper ${j.side} ${j.symbol} ${j.status === "open" ? "filled at " + n(j.entry) : "limit placed at " + n(j.limit)}`);
      btn.classList.add("fired"); setTimeout(() => btn.classList.remove("fired"), 700);
      $("#tNote").value = ""; refresh();
    } catch(e){ toast("Open failed: " + e); } finally { btn.disabled = false; }
  }

  // ---- quote ----
  async function quote(){
    const sym = $("#tSym").value; if (!sym) return;
    let q; try { q = await (await fetch("/api/paper/quote?symbol=" + encodeURIComponent(sym))).json(); } catch(e){ return; }
    if (sym !== $("#tSym").value) return;
    if (q.error){ $("#tPx").textContent = "unavailable"; $("#tSrc").textContent = q.error.slice(0, 80); return; }
    const px = $("#tPx"), prev = lastPx;
    lastPx = q.price; lastQuote = q; px.textContent = n(q.price);
    if (prev != null && prev !== q.price){ px.classList.remove("flash-up", "flash-down"); void px.offsetWidth; px.classList.add(q.price > prev ? "flash-up" : "flash-down"); }
    $("#tChg").textContent = pct(q.chg_24h_pct) + " 24h"; $("#tChg").className = tone(q.chg_24h_pct);
    $("#tSrc").textContent = q.source;
    if (pendingPlan) applyPlanPrice();
    if (ord === "market") previewSoon();
  }

  // ---- desk ----
  async function refresh(){
    let s; try { s = await (await fetch("/api/paper")).json(); } catch(e){ $("#pUpdated").textContent = "server unreachable"; $("#pLive").classList.add("off"); return; }
    if (s.error){ $("#pUpdated").textContent = s.error; $("#pLive").classList.add("off"); return; }
    lastSnap = s; updatedAt = Date.now(); $("#pLive").classList.toggle("off", Object.keys(s.errors).length > 0);
    $("#pUpdated").textContent = Object.keys(s.errors).length ? "feed error: " + Object.keys(s.errors).join(", ") : "live · updated just now";
    tiles(s); positions(s); journal(s); noteProgress(s.progress); noteChallenge(s.challenge);
  }
  // ---- daily challenge: banner on the ticket when it's today's symbol; one celebration when it's done ----
  let challenge = null;
  function noteChallenge(ch){
    challenge = ch || null; banner();
    if (ch && ch.done && window.FX) FX.once(`challenge:${ch.date}`, () => { FX.confetti(); toast(`Daily challenge complete! +${ch.reward.done} XP`); });
  }
  function banner(){
    const b = $("#tChallenge"), ch = challenge;
    b.hidden = !ch || ch.symbol !== $("#tSym").value;
    if (b.hidden) return;
    b.className = "ch-banner" + (ch.done ? " done" : "");
    b.innerHTML = ch.done ? `🏆 Today's challenge is done (${esc(ch.trade_id)}). ${ch.t1 ? "T1 hit too." : `+${ch.reward.t1} XP more if it reaches T1 first.`}`
      : !rulesOn ? `★ Today's challenge symbol. The challenge needs a rule-checked ticket: switch house rules on to play it.`
      : `★ Daily challenge symbol: a rule-clean ticket with a P(T1) call and ${ch.min_R}R+ earns <b>+${ch.reward.done} XP</b>.`;
  }
  // ---- XP: chip on the desk, "+N XP" pop and level-up toast when the journal earns something new ----
  function noteProgress(pg){
    if (!pg) return;
    const chip = $("#pXp"); chip.hidden = false;
    const fs = pg.freestyle;
    chip.innerHTML = rulesOn || !fs
      ? `<b>Lv ${pg.level}</b><i style="--w:${pg.pct.toFixed(1)}%"></i><span>${pg.xp} XP</span>${pg.streak ? `<em>🔥${pg.streak}</em>` : ""}`
      : `<b>FS ${fs.level}</b><i class="fs" style="--w:${fs.pct.toFixed(1)}%"></i><span>${fs.xp} Freestyle XP</span>`;
    let seen = null; try { seen = JSON.parse(localStorage.getItem("ta_xp_seen") || "null"); } catch(e){}
    localStorage.setItem("ta_xp_seen", JSON.stringify({ xp: pg.xp, level: pg.level }));
    if (!seen || seen.xp === pg.xp) return;               // first visit: nothing to celebrate
    const d = pg.xp - seen.xp, why = pg.recent.length ? pg.recent[0].label : "";
    pop(`${d > 0 ? "+" : ""}${d} XP`, d > 0);
    if (pg.level > seen.level){ toast(`Level up! Level ${pg.level} · ${pg.rank}`); if (window.FX) FX.confetti({ count: 200 }); }
    else toast(`${d > 0 ? "+" : ""}${d} XP${why ? " · " + why : ""}`);
  }
  function pop(text, good){
    const el = document.createElement("div"); el.className = "xp-pop " + (good ? "up" : "down"); el.textContent = text;
    document.body.appendChild(el); setTimeout(() => el.remove(), 1600);
  }
  function tiles(s){
    const a = s.account, st = s.stats;
    const defs = [["equity", "Equity", a.equity, v => usd(v), pct(a.return_pct) + " all time", tone(a.return_pct)],
      ["unrealized", "Open P&L", a.unrealized, v => usd(v, true), `${s.trades.filter(t => t.status === "open").length} open · before fees`, tone(a.unrealized)],
      ["balance", "Balance", a.balance, v => usd(v), `start ${usd(a.start_equity)}`, ""],
      ["open_risk", "Open risk", a.open_risk_pct, v => pct(v, false), usd(a.open_risk) + " to stops", a.open_risk_pct > 5 ? "down" : ""],
      ["win", "Win rate", st.win_rate, v => v == null ? "—" : n(v, 0) + "%", `${st.closed} closed`, ""],
      ["avgR", "Avg R", st.avg_R, v => rr(v), st.profit_factor ? `PF ${n(st.profit_factor, 2)}` : "profit factor —", tone(st.avg_R)]];
    const box = $("#pTiles");
    if (!box.children.length) box.innerHTML = defs.map(([k, l]) => `<div class="tile" data-k="${k}"><span class="t-label">${l}</span><span class="t-value"></span><span class="t-sub"></span></div>`).join("");
    defs.forEach(([k, , v, f, sub, tn]) => {
      const el = box.querySelector(`[data-k="${k}"]`), val = el.querySelector(".t-value");
      val.className = "t-value " + tn; el.querySelector(".t-sub").textContent = sub;
      countUp(val, shown[k], v, f); shown[k] = v;
    });
  }
  function countUp(el, from, to, f){
    if (to == null || from == null || !animOn() || from === to){ el.textContent = f(to); return; }
    const t0 = performance.now(), d = 600;
    const stepFn = now => { const k = Math.min(1, (now - t0) / d), e = 1 - Math.pow(1 - k, 3);
      el.textContent = f(from + (to - from) * e); if (k < 1) requestAnimationFrame(stepFn); };
    requestAnimationFrame(stepFn);
  }
  function positions(s){
    const live = s.trades.filter(t => t.status === "open" || t.status === "pending");
    $("#pCount").textContent = live.length ? `${live.length} live` : "";
    const box = $("#pOpen");
    if (!live.length){ box.innerHTML = `<div class="empty-desk"><b>No open paper trades.</b><span>Build one in the ticket, or run a quick check and hit <em>Paper this setup</em>.</span></div>`; return; }
    box.innerHTML = live.map((t, k) => {
      const s1 = t.side === "long" ? 1 : -1, px = t.mark ?? t.entry, prev = marks[t.id];
      const flash = prev != null && prev !== px ? (px > prev ? "flash-up" : "flash-down") : ""; marks[t.id] = px;
      const out = t.stop ?? t.liquidation;   // where the trade is forced out (no stop when rules are off)
      const tg = t.targets.map(x => x.price), lo = Math.min(out, t.entry, ...tg, px), hi = Math.max(out, t.entry, ...tg, px), span = hi - lo || 1;
      const pos = v => ((s1 > 0 ? v - lo : hi - v) / span * 100).toFixed(2);
      const size = t.contracts != null ? `${n(t.contracts, 0)} ${esc(t.contract_name)}` : n(t.size * t.remaining, 4);
      return `<article class="pos ${t.side} ${t.status}" style="--i:${k}">` +
        `<header><span class="pill ${t.side}">${t.side === "long" ? "▲" : "▼"} ${esc(t.side)}</span><b>${esc(t.symbol)}</b><small>${esc(t.id)} · ${size} · ${n(t.leverage, 0)}x</small>` +
        (t.status === "pending" ? `<span class="pill pend">limit ${n(t.limit)}</span>` : "") + `</header>` +
        `<div class="pos-nums"><div><small>${t.status === "pending" ? "Limit" : "Entry"}</small><b>${n(t.entry)}</b></div>` +
        `<div><small>Mark</small><b class="${flash}">${n(t.mark)}</b></div>` +
        (t.status === "open" ? `<div><small>Net P&amp;L</small><b class="${tone(t.unrealized + t.realized)}">${usd(t.unrealized + t.realized, true)}</b></div>` +
          `<div><small>R</small><b class="${tone(t.open_R)}">${rr(t.open_R)}</b></div>` : "") + `</div>` +
        `<div class="track" title="${t.stop != null ? "stop " + n(t.stop) : "no stop"} · liq ${n(t.liquidation)}"><i class="risk" style="left:${Math.min(pos(out), pos(t.entry))}%;width:${Math.abs(pos(t.entry) - pos(out))}%"></i>` +
        `<span class="tk ${t.stop != null ? "stop" : "liq"}" style="left:${pos(out)}%"></span>` +
        t.targets.map(x => `<span class="tk tgt ${x.hit_at ? "hit" : ""}" style="left:${pos(x.price)}%" title="${n(x.price)}${x.hit_at ? " (hit)" : ""}"></span>`).join("") +
        `<span class="tk entry" style="left:${pos(t.entry)}%"></span><span class="dot ${tone(s1 * (px - t.entry))}" style="left:${pos(px)}%"></span></div>` +
        `<footer><small>${t.mode === "free" ? `<span class="free-tag">freestyle</span> ` : ""}${t.stop != null ? "stop " + n(t.stop) : "no stop"} · ` +
        `${t.targets.length ? "targets " + t.targets.map(x => (x.hit_at ? "✓" : "") + n(x.price)).join(" / ") : "no targets"} · liq ${n(t.liquidation)}${t.p_t1 != null ? ` · P(T1) ${n(t.p_t1, 0)}%` : ""}</small>` +
        `<button class="ghost sm" data-act="${t.status === "pending" ? "cancel" : "close"}" data-id="${esc(t.id)}">${t.status === "pending" ? "Cancel" : "Close at market"}</button></footer></article>`;
    }).join("");
  }
  function journal(s){
    const st = s.stats;
    $("#pInsights").innerHTML = st.insights.map(x => `<li>${esc(x)}</li>`).join("");
    $("#pCalib").innerHTML = st.calibration.length ? st.calibration.map(c =>
      `<div class="cal"><span>${esc(c.bucket)} <small>n=${c.n}</small></span><div class="cal-bars"><i class="called" style="--w:${c.called}%"></i><i class="actual" style="--w:${c.actual}%"></i></div>` +
      `<small>${n(c.called, 0)}% → ${n(c.actual, 0)}%</small></div>`).join("") : `<p class="muted small">Set P(T1) on each ticket; once trades close, this shows whether your calls match what happens.</p>`;
    curve(st.curve);
    const closed = s.trades.filter(t => t.status === "closed" || t.status === "cancelled");
    $("#pClosed").innerHTML = closed.length ? `<table class="closed"><thead><tr><th>ID</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th><th>R</th><th>P&amp;L</th><th>Why</th><th>P(T1)</th><th>T1 first</th><th>Closed</th></tr></thead><tbody>` +
      closed.slice(0, 50).map(t => { const last = t.exits.length ? t.exits[t.exits.length - 1].price : null;
        return `<tr class="${t.status}"><td>${esc(t.id)}</td><td>${esc(t.symbol)}</td><td><span class="pill ${t.side}">${esc(t.side)}</span></td><td>${n(t.entry)}</td><td>${n(last)}</td>` +
          `<td class="${tone(t.R)}">${t.status === "cancelled" ? "—" : rr(t.R)}</td><td class="${tone(t.realized)}">${t.status === "cancelled" ? "—" : usd(t.realized, true)}</td>` +
          `<td>${esc(t.status === "cancelled" ? "cancelled" : t.exit_reason)}</td><td>${t.p_t1 != null ? n(t.p_t1, 0) + "%" : "—"}</td><td>${t.t1_before_stop == null ? "—" : t.t1_before_stop ? "yes" : "no"}</td><td>${esc(ago(t.closed_at))}</td></tr>`; }).join("") +
      `</tbody></table>` : `<p class="muted small">Closed paper trades land here with their R, exit reason and whether T1 came before the stop.</p>`;
  }
  function curve(pts){
    const box = $("#pCurve");
    if (!pts || pts.length < 2){ box.innerHTML = `<p class="muted small">The curve starts after your first closed trade.</p>`; return; }
    const W = 520, H = 160, P = 8, ys = pts.map(p => p.equity), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
    const x = i => P + i / (pts.length - 1) * (W - 2 * P), y = v => H - P - (v - lo) / span * (H - 2 * P);
    const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
    const up = ys[ys.length - 1] >= ys[0], base = y(pts[0].equity).toFixed(1);
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="eq ${up ? "up" : "down"}" role="img" aria-label="Paper equity curve">` +
      `<defs><linearGradient id="eqg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".35"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>` +
      `<line x1="${P}" x2="${W - P}" y1="${base}" y2="${base}" class="base"/>` +
      `<polygon points="${x(0)},${H - P} ${line} ${x(pts.length - 1)},${H - P}" fill="url(#eqg)"/>` +
      `<polyline points="${line}" class="ln" pathLength="100"/>` +
      pts.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.equity).toFixed(1)}" r="3.5"><title>${esc(p.id || "start")}: ${usd(p.equity)}</title></circle>`).join("") + `</svg>`;
  }
  async function act(e){
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const what = b.dataset.act, id = b.dataset.id;
    if (what === "close" && !confirm(`Close ${id} at the current price?`)) return;
    b.disabled = true;
    try { const j = await post(`/api/paper/${what}`, { id }); toast(j.error ? j.error : `${id} ${what === "close" ? "closed" + (j.R != null ? ` at ${rr(j.R)}` : "") : "cancelled"}`); }
    catch(err){ toast("Failed: " + err); }
    refresh();
  }
  async function resetAcct(){
    const v = prompt("Start a new paper account with this equity (the current journal is archived in memory/paper.json):", lastSnap ? Math.round(lastSnap.account.start_equity) : 10000);
    if (v == null) return; const eq = num(v);
    if (!(eq > 0)){ toast("Equity must be a positive number"); return; }
    const j = await post("/api/paper/reset", { equity: eq }); toast(j.error || `New paper account: ${usd(eq)}`);
    Object.keys(shown).forEach(k => delete shown[k]); refresh();
  }

  // ---- "Paper this setup" from a quick check ----
  let pendingPlan = null;
  function prefill(plan){
    go("paper");
    if (cfg.watchlist.includes(plan.symbol)){ $("#tSym").value = plan.symbol; onSymbol(); }
    setSide(plan.side);
    $("#tStop").value = plan.stop; $("#tTargets").value = plan.targets.join(", ");
    if (plan.leverage){ $("#tLev").value = plan.leverage; levLabel(); }
    setOrd("market"); pendingPlan = plan;
    if (lastPx) applyPlanPrice();
    $("#ticket").classList.remove("glow"); void $("#ticket").offsetWidth; $("#ticket").classList.add("glow");
    toast("Ticket filled from the quick check. Review it, then open it yourself.");
  }
  function applyPlanPrice(){   // the quick check's entry becomes a limit if it's away from the live price on the right side
    const p = pendingPlan; pendingPlan = null;
    if (!p || p.entry == null || !lastPx) return;
    const s1 = p.side === "long" ? 1 : -1;
    if (Math.abs(p.entry / lastPx - 1) > 0.0025 && s1 * (p.entry - lastPx) < 0){ setOrd("limit"); $("#tLimit").value = p.entry; }
    previewSoon();
  }

  // ---- wiring ----
  $("#paperBtn").onclick = () => { closeDrawers(); go("paper"); };
  document.querySelectorAll(".side-toggle .side").forEach(b => b.onclick = () => setSide(b.dataset.side));
  document.querySelectorAll("#tOrd .seg-btn").forEach(b => b.onclick = () => setOrd(b.dataset.v));
  document.querySelectorAll("#tSizeMode .seg-btn").forEach(b => b.onclick = () => setSizeMode(b.dataset.v));
  $("#tSym").onchange = () => onSymbol();
  ["#tStop", "#tTargets", "#tSize", "#tLimit"].forEach(s => $(s).addEventListener("input", previewSoon));
  $("#tLev").addEventListener("input", () => { levLabel(); previewSoon(); });
  $("#tP").addEventListener("input", () => { $("#tPOut").textContent = $("#tP").value + "%"; });
  $("#tSubmit").onclick = submit; $("#pOpen").onclick = act; $("#pReset").onclick = resetAcct;
  $("#planBtn").onclick = () => { if (Paper.plan) prefill(Paper.plan); };
  document.addEventListener("keydown", e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && (e.key === "p" || e.key === "P")) go("paper");
  });
  document.addEventListener("visibilitychange", () => { if (visible()){ refresh(); quote(); } });
  setInterval(() => { if (updatedAt && visible() && !$("#pLive").classList.contains("off"))
    $("#pUpdated").textContent = `live · updated ${Math.round((Date.now() - updatedAt) / 1000)}s ago`; }, 1000);
  init();
  $("#pXp").onclick = () => go("home");
  return { setView, prefill, openSymbol, noteProgress, plan: null };
})();
// A top-level const is not a window property; app.js looks the desk up as window.Paper.
window.Paper = Paper;
