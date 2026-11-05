"use strict";
// Home: start screen with live tickers, modes, your paper progress and your rules.
// Uses $, esc, go, settings, animOn from app.js and Paper from paper.js (both loaded first).
// The player card is a preview: XP, levels and badges come from tools/paper.py in the next build.
const Home = (() => {
  const TICKER_MAX = 8, REFRESH_MS = 30000;
  let cfg = null, timer = 0, loaded = false;

  const n = (x, nd = 2) => x == null || !isFinite(x) ? "—" : Number(x).toLocaleString("en-US", { minimumFractionDigits: nd, maximumFractionDigits: nd });
  const px = x => n(x, Math.abs(x) < 10 ? 4 : 2);
  const tone = x => x == null || Math.abs(x) < 1e-9 ? "" : x > 0 ? "up" : "down";

  async function config(){
    if (!cfg){ try { cfg = await (await fetch("/api/config")).json(); } catch(e){ cfg = { watchlist: [], rules: null, claude_cli: false }; } }
    return cfg;
  }
  // your Settings symbol shortcuts first, then the rest of the watchlist
  function tickerSymbols(c){
    const mine = (settings.symbols || []).filter(s => c.watchlist.includes(s));
    return mine.concat(c.watchlist.filter(s => !mine.includes(s))).slice(0, TICKER_MAX);
  }

  async function tickers(){
    const c = await config(), syms = tickerSymbols(c), box = $("#hTicker");
    if (!syms.length){ box.innerHTML = `<p class="muted small">Watchlist unavailable.</p>`; return; }
    if (box.dataset.syms !== syms.join()){
      box.dataset.syms = syms.join();
      box.innerHTML = syms.map((s, k) => `<button class="h-tick" data-sym="${esc(s)}" style="--i:${k}" title="Open ${esc(s)} on the paper desk">` +
        `<b>${esc(s.replace("/USDT", ""))}</b><span class="t-px">…</span><small class="t-chg"></small></button>`).join("");
    }
    await Promise.all(syms.map(async s => {
      const el = box.querySelector(`[data-sym="${CSS.escape(s)}"]`); if (!el) return;
      let q; try { q = await (await fetch("/api/paper/quote?symbol=" + encodeURIComponent(s))).json(); } catch(e){ q = { error: "offline" }; }
      const p = el.querySelector(".t-px"), ch = el.querySelector(".t-chg");
      if (q.error){ p.textContent = "n/a"; ch.textContent = "feed error"; ch.className = "t-chg"; el.title = q.error; return; }
      const prev = Number(el.dataset.px);
      p.textContent = px(q.price); el.dataset.px = q.price;
      if (prev && prev !== q.price){ p.classList.remove("flash-up", "flash-down"); void p.offsetWidth; p.classList.add(q.price > prev ? "flash-up" : "flash-down"); }
      ch.textContent = (q.chg_24h_pct > 0 ? "+" : "") + n(q.chg_24h_pct) + "%"; ch.className = "t-chg " + tone(q.chg_24h_pct);
    }));
  }

  async function player(){
    const box = $("#hPlayer");
    let s; try { s = await (await fetch("/api/paper")).json(); } catch(e){ s = { error: "server unreachable" }; }
    if (s.error){ box.innerHTML = `<h3>Your desk</h3><p class="muted small">Paper desk unavailable: ${esc(s.error)}</p>`; return; }
    const a = s.account, st = s.stats, open = s.trades.filter(t => t.status === "open" || t.status === "pending").length;
    box.innerHTML =
      `<div class="pl-head"><span class="avatar" aria-hidden="true">◆</span><div><small>Level 1</small><h3>Rookie</h3></div>` +
      `<span class="badge-soon" title="XP, levels and badges arrive in the next build">XP soon</span></div>` +
      `<div class="xp" role="img" aria-label="XP bar, not active yet"><i style="--w:0%"></i></div>` +
      `<small class="muted">XP will come from rule-clean trades, journal notes, accurate P(T1) calls and paper P&amp;L (capped per day).</small>` +
      `<div class="pl-stats">` +
      `<div><small>Paper equity</small><b class="${tone(a.return_pct)}">$${n(a.equity)}</b></div>` +
      `<div><small>Closed</small><b>${st.closed}</b></div>` +
      `<div><small>Win rate</small><b>${st.win_rate == null ? "—" : n(st.win_rate, 0) + "%"}</b></div>` +
      `<div><small>Avg R</small><b class="${tone(st.avg_R)}">${st.avg_R == null ? "—" : (st.avg_R > 0 ? "+" : "") + n(st.avg_R) + "R"}</b></div>` +
      `<div><small>Live</small><b>${open}</b></div></div>` +
      `<button class="ghost sm" id="hDesk">${open ? "Manage open trades →" : "Open your first paper trade →"}</button>`;
    $("#hDesk").onclick = () => go("paper");
  }

  async function rules(){
    const r = (await config()).rules, box = $("#hRules");
    if (!r){ box.innerHTML = `<h3>Your rules</h3><p class="muted small">Rules: none set yet.</p>`; return; }
    const lev = r.max_leverage && typeof r.max_leverage === "object"
      ? Object.entries(r.max_leverage).map(([k, v]) => `${k} ${v}x`).join(" · ") : r.max_leverage != null ? `${r.max_leverage}x` : null;
    const rows = [["Risk per trade", r.max_risk_pct_per_trade != null && `${r.max_risk_pct_per_trade}%`],
      ["Open risk", r.max_open_risk_pct != null && `${r.max_open_risk_pct}%`],
      ["Min weighted R", r.min_weighted_r != null && `${r.min_weighted_r}R`],
      ["Trades per day", r.max_trades_per_day], ["Cooldown after a loss", r.loss_cooldown_hours != null && `${r.loss_cooldown_hours}h`],
      ["Stop", r.require_stop ? "required" : null], ["Max leverage", lev]].filter(([, v]) => v != null && v !== false);
    box.innerHTML = `<h3>House rules <small>the game is played inside these</small></h3>` +
      `<ul class="rule-list">${rows.map(([k, v]) => `<li><span>${esc(k)}</span><b>${esc(String(v))}</b></li>`).join("")}</ul>`;
  }

  function setView(v){
    $("#homeView").hidden = v !== "home";
    clearInterval(timer);
    if (v !== "home") return;
    if (!loaded){ loaded = true; config().then(c => { if (!c.claude_cli){ $("#hAgentTag").textContent = "Not found"; } }); rules(); }
    tickers(); player();
    timer = setInterval(() => { if (!document.hidden && $("main").dataset.view === "home") tickers(); }, REFRESH_MS);
  }

  // ---- wiring ----
  $("#hPlay").onclick = () => go("paper");
  $("#hAnalyze").onclick = () => { go("analyze"); $("#q").focus(); };
  $("#hTicker").onclick = e => { const b = e.target.closest(".h-tick"); if (b && window.Paper) Paper.openSymbol(b.dataset.sym); };
  document.querySelectorAll(".mode[data-mode]").forEach(b => b.onclick = () => {
    const m = b.dataset.mode;
    if (m === "paper") return go("paper");
    go("analyze");
    const btn = $(m === "agent" ? "#agent" : "#quick");
    btn.classList.remove("glow"); void btn.offsetWidth; btn.classList.add("glow");
    $("#q").focus();
  });
  $(".brand").onkeydown = e => { if (e.key === "Enter") go("home"); };
  return { setView };
})();
window.Home = Home;
