"use strict";
/* Report view: turns the markdown report from quick_check / the full agent / portfolio into a sectioned,
   interactive read (verdict, key numbers, price ladder, risk charts, flags, context).
   Parsing is defensive: anything it doesn't recognise is still shown under "More details", and the
   original text is always available under "Full report". All report text goes through esc(). */
const Report = (() => {
  const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const TAGS = { data: "Fetched or user-supplied value", calc: "Computed this session", judgment: "Interpretation, not a calculation",
    missing: "An input was missing", assumption: "Assumed because an input was missing" };
  const TAG_RE = /\[((?:DATA(?::user)?|CALC|JUDGMENT|MISSING|ASSUMPTION)(?:\s*(?:\/|→|->)\s*(?:DATA|CALC|JUDGMENT))*)\]/g;
  const tagChip = t => { const k = t.split(/[\/:→\s-]/)[0].toLowerCase(); const user = /:user/.test(t) ? " (you)" : "";
    return `<span class="tag tag-${k}" title="${esc(TAGS[k] || "")}">${esc(k)}${user}</span>`; };
  const STATUS_ICON = { fail: "✕", warn: "!", info: "i", pass: "✓" };

  function inline(s){
    return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|\s)_([^_]+)_(?=\s|$|[.,;])/g, "$1<i>$2</i>")
      .replace(TAG_RE, (_, t) => tagChip(t))
      .replace(/\b(FAIL|WARN)\b/g, (_, w) => `<span class="st st-${w === "FAIL" ? "fail" : "warn"}">${STATUS_ICON[w === "FAIL" ? "fail" : "warn"]} ${w}</span>`);
  }
  function md(text){
    const L = String(text).replace(/\r/g, "").split("\n"); let h = "", i = 0;
    while (i < L.length){
      const l = L[i];
      if (l.startsWith("```")){ const c = []; i++; while (i < L.length && !L[i].startsWith("```")) c.push(L[i++]); i++;
        h += "<pre>" + esc(c.join("\n")) + "</pre>"; continue; }
      if (/^\|.*\|\s*$/.test(l.trim())){ const rows = []; while (i < L.length && /^\|.*\|\s*$/.test(L[i].trim())) rows.push(L[i++].trim());
        h += tableHtml(parseTable(rows)); continue; }
      const m = l.match(/^(#{1,4})\s+(.*)/); if (m){ h += `<h${m[1].length + 2}>${inline(m[2])}</h${m[1].length + 2}>`; i++; continue; }
      if (/^\s*([-*]|\d+\.)\s+/.test(l)){ const it = []; while (i < L.length && /^\s*([-*]|\d+\.)\s+/.test(L[i])) it.push(L[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        h += "<ul>" + it.map(x => `<li>${inline(x)}</li>`).join("") + "</ul>"; continue; }
      if (/^-{3,}\s*$/.test(l.trim())){ h += "<hr>"; i++; continue; }
      if (l.trim() === ""){ i++; continue; }
      const p = []; while (i < L.length && L[i].trim() !== "" && !/^(\||#|```|\s*([-*]|\d+\.)\s)/.test(L[i].trim())) p.push(L[i++]);
      if (!p.length){ p.push(L[i++]); }
      h += "<p>" + inline(p.join(" ")) + "</p>";
    }
    return h;
  }

  // ---------- parsing ----------
  const toNum = s => { if (s == null) return NaN; const m = String(s).replace(/−/g, "-").replace(/,/g, "").match(/[+-]?\d*\.?\d+/); return m ? parseFloat(m[0]) : NaN; };
  const cellsOf = r => r.trim().replace(/^\|/, "").replace(/\|\s*$/, "").split("|").map(c => c.trim());
  function parseTable(rows){
    const body = rows.filter(r => !/^\|[\s:|-]+\|\s*$/.test(r));
    return { head: cellsOf(body[0] || ""), rows: body.slice(1).map(cellsOf) };
  }
  function levelOf(w){
    w = w.toUpperCase();
    if (/^(FAIL|FAILED|CRITICAL|HIGH|ERROR|BREACH)$/.test(w)) return "fail";
    if (/^(WARN|WARNING|MEDIUM|SERIOUS|CAUTION)$/.test(w)) return "warn";
    if (/^(PASS|OK|GOOD)$/.test(w)) return "pass";
    return "info";
  }
  const stripTags = s => String(s).replace(TAG_RE, "").replace(/\*\*/g, "").replace(/\s+/g, " ").trim();

  function parse(markdown){
    const text = String(markdown || "").replace(/\r/g, "");
    const out = { raw: text, preamble: [], sections: [], disclaimer: "", footer: "" };
    let cur = null;
    for (const l of text.split("\n")){
      if (/^##\s+/.test(l)){ cur = { title: l.replace(/^##\s+/, "").trim(), lines: [] }; out.sections.push(cur); continue; }
      if (/not financial advice/i.test(l)){ out.disclaimer = l.trim(); continue; }
      if (/^_?Full agent run:/.test(l.trim())){ out.footer = l.trim().replace(/^_|_$/g, ""); continue; }
      if (/^-{3,}\s*$/.test(l.trim())) continue;
      (cur ? cur.lines : out.preamble).push(l);
    }
    if (!out.sections.length){ out.sections.push({ title: "", lines: out.preamble }); out.preamble = []; }
    out.sections = out.sections.map(s => analyze(s.title, s.lines));
    return out;
  }

  function analyze(title, lines){
    const s = { title, kv: [], lists: [], tables: [], milestones: null, flags: [], paras: [], facts: [], used: new Set() };
    let i = 0, pendingKey = null;
    const KV = /^\**([A-Za-z(][^:]{0,70}?)\**:\s+(.+)$/;
    while (i < lines.length){
      const l = lines[i], t = l.trim();
      if (!t){ i++; continue; }
      if (/^\|.*\|\s*$/.test(t)){
        const rows = []; while (i < lines.length && /^\|.*\|\s*$/.test(lines[i].trim())) rows.push(lines[i++].trim());
        const tb = parseTable(rows);
        if (!s.milestones && /milestone/i.test(tb.head[0] || "")) s.milestones = tb; else s.tables.push(Object.assign({ title: pendingKey }, tb));
        pendingKey = null; continue;
      }
      if (/^```/.test(t)){ const c = [t]; i++; while (i < lines.length && !/^```/.test(lines[i].trim())) c.push(lines[i++]); c.push("```"); i++;
        s.paras.push(c.join("\n")); continue; }
      if (/^#{3,}\s+/.test(t)){ pendingKey = t.replace(/^#+\s+/, "").replace(/:$/, ""); i++; continue; }
      if (/^\s*([-*]|\d+\.)\s+/.test(l)){
        const items = []; while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        const rest = [];
        items.forEach(it => { const f = it.match(/^\*\*([A-Z]{2,10})\*\*:?\s*(.*)$/);
          if (f) s.flags.push({ level: levelOf(f[1]), label: f[1], text: f[2] }); else rest.push(it); });
        if (rest.length){ const key = pendingKey || "Details"; const ex = s.lists.find(x => x.key === key);
          if (ex) ex.items.push(...rest); else s.lists.push({ key, items: rest }); }
        pendingKey = null; continue;
      }
      const head = t.match(/^\**([^:*|]{1,90}?)\**:\s*$/);
      if (head){ pendingKey = head[1].trim(); i++; continue; }
      if (pendingKey && !KV.test(t)){ // continuation text under a "Key:" heading
        const ex = s.kv.find(x => x.key === pendingKey);
        if (ex) ex.value += " " + t; else s.kv.push({ key: pendingKey, value: t });
        i++; continue;
      }
      pendingKey = null;
      t.split(/\s+\|\s+/).forEach(seg => { const m = seg.match(KV);
        if (m) s.kv.push({ key: m[1].trim(), value: m[2].trim() }); else s.facts.push(seg.trim()); });
      i++;
    }
    s.metrics = metricsOf(s);
    s.isInstrument = !!(s.milestones || s.metrics.current != null || /as-of|quick check/i.test(title));
    return s;
  }

  function kvGet(s, re, mark = true){
    const x = s.kv.find(k => re.test(k.key)); if (x && mark) s.used.add(x); return x ? x.value : null;
  }

  function metricsOf(s){
    const all = [s.title, ...s.kv.map(k => `${k.key}: ${k.value}`), ...s.facts, ...s.flags.map(f => f.text),
      ...s.lists.flatMap(l => l.items)].join("\n");
    const m = {}; const g = (re, k = 1) => { const x = all.match(re); return x ? x[k] : null; };
    const tm = s.title.match(/^([A-Z0-9^=.\-]{1,15}(?:\/[A-Z0-9]{2,6})?)(?=[\s:]|$)/);
    m.symbol = tm ? tm[1] : null;
    m.side = ((s.title.match(/\b(long|short)\b/i) || all.match(/\b(long|short)\b/i) || [])[1] || "").toLowerCase() || null;
    const after = s.title.includes(":") ? s.title.split(":").slice(1).join(":").trim() : "";
    if (/as-of/i.test(after)){ m.asOf = after.replace(/^.*as-of\s*/i, ""); m.timeframe = (after.match(/^(\w+)/) || [])[1]; }
    else if (after) m.verdict = after;
    if (!m.asOf){ const lb = g(/last bar ([0-9-]+ [0-9:]+ UTC)/); if (lb) m.asOf = lb; }
    const noSetup = kvGet(s, /^No valid setup/i, false); if (noSetup) m.verdict = "No valid setup: " + stripTags(noSetup);
    const ns2 = g(/No valid setup:\s*([^\n]+)/); if (!m.verdict && ns2) m.verdict = "No valid setup: " + stripTags(ns2);

    // milestone rows -> ladder points
    m.rows = [];
    if (s.milestones){
      const H = s.milestones.head;
      s.milestones.rows.forEach(c => {
        const name = (c[0] || "").replace(/\*/g, "").trim(); const price = toNum(c[1]);
        if (!name || isNaN(price)) return;
        const cells = {}; H.forEach((h, k) => { if (k > 1 && c[k] && c[k] !== "-") cells[h] = c[k]; });
        m.rows.push({ name, priceText: (c[1] || "").replace(/\*/g, ""), price, cells, kind: kindOf(name) });
      });
    }
    const curRow = m.rows.find(r => r.kind === "now");
    const cp = g(/Current price[:\s]+([0-9][\d,]*\.?\d*)/i);
    m.current = curRow ? curRow.price : (cp ? toNum(cp) : null); m.currentText = curRow ? curRow.priceText : cp;
    m.entry = m.rows.find(r => r.kind === "entry" && /entry/i.test(r.name)) || null;
    m.stop = m.rows.find(r => r.kind === "stop") || null;
    m.liq = m.rows.find(r => r.kind === "liq") || null;
    m.targets = m.rows.filter(r => r.kind === "target");

    const n = re => { const v = g(re); return v == null ? null : toNum(v); };
    m.atr = n(/ATR14\s+([\d,.]+)/);
    m.grade = g(/Grade[^:\n]*:\s*\**\s*([ABC])(?![A-Za-z])/);
    const cv = all.match(/Conviction\s+(long|short):\s*([+\-−]?\d+)\s*\/\s*(\d+)/i);
    if (cv){ m.conv = toNum(cv[2]); m.convMax = toNum(cv[3]); }
    else { const c2 = all.match(/conviction\s+([+\-−]?\d+)\s+(long|short)/i); if (c2){ m.conv = toNum(c2[1]); m.convMax = 8; } }
    m.convNote = g(/->\s*[ABC],\s*([a-z\- ]+?)\s*\(/i) || g(/\b(counter-trend|with-trend|trend-aligned)\b/i);
    m.wR = n(/Weighted R(?: at targets)?:?\s*([\d.]+)/i);
    m.minR = n(/([\d.]+)\s+minimum/i) || n(/min weighted R \([\d.]+ vs your limit ([\d.]+)\)/i) || 2.0;
    m.stopAtr = n(/stop width ([\d.]+) ATR/i) || n(/Stop is wide at ([\d.]+) ATR/i);
    m.bufferLev = n(/max leverage for 1 ATR liq buffer ([\d.]+)x/i);
    m.liqPct = n(/liquidation ([\d.]+)% away/i);
    m.liqAtr = n(/=\s*([\d.]+) daily ATR/i);
    const kept = all.match(/kept (\d+)-day (?:liquidation-touch )?odds <= ?([\d.]+)%(?: historically)?:?\s*([\d.]+)x/i)
      || all.match(/Leverage that kept (\d+)-day odds <= ?([\d.]+)%:\s*([\d.]+)x/i);
    if (kept){ m.safeDays = toNum(kept[1]); m.safePct = toNum(kept[2]); m.safeLev = toNum(kept[3]); }
    const lv = (m.liq && m.liq.name.match(/\(([\d.]+)x\)/)) || all.match(/\b(\d+(?:\.\d+)?)x\b/);
    m.lev = lv ? toNum(lv[1]) : null;
    const od = all.match(/within ((?:\d+d\s*\/?\s*)+) on ((?:[\d.]+%\s*\/?\s*)+) of days(?: in the lookback)?\s*(\([^)]*\))?/i);
    if (od){
      const days = od[1].split("/").map(x => toNum(x)).filter(x => !isNaN(x)), pct = od[2].split("/").map(x => toNum(x)).filter(x => !isNaN(x));
      if (days.length && days.length === pct.length) m.odds = { days, pct, note: (od[3] || "").replace(/[()]/g, "") };
    }
    const lim = all.match(/(\d+)-day liquidation-touch odds \(([\d.]+)% vs your limit ([\d.]+)%\)/i);
    if (lim) m.oddsLimit = { days: toNum(lim[1]), limit: toNum(lim[3]) };
    const rl = all.match(/(\d+) broken of (\d+) set/i);
    if (rl) m.rules = { broken: toNum(rl[1]), total: toNum(rl[2]) };
    else if (/no rule broken/i.test(all)) m.rules = { broken: 0, total: null };
    else if (/none set yet/i.test(all)) m.rules = { none: true };
    m.pT1 = n(/P\(T1 before stop\):\s*(\d+)%/i);
    const rg = all.match(/range\s+([\d,.]+)\s*[-–]\s*([\d,.]+)[^\n]*?price at ([\d.]+)%/i);
    if (rg){ m.range = { lo: toNum(rg[1]), hi: toNum(rg[2]), pos: toNum(rg[3]), loText: rg[1], hiText: rg[2] };
      const z = all.match(/zone at ([\d.]+)%/i); if (z) m.range.zone = toNum(z[1]); }
    m.structure = g(/Structure[^:\n]*:\s*([a-z][a-z\- ]*?)[,;]/i);
    m.refs = pairsOf(kvGet(s, /^Reference levels/i, false));
    return m;
  }
  function kindOf(name){
    if (/current|^now\b/i.test(name)) return "now";
    if (/liq/i.test(name)) return "liq";
    if (/stop|invalid/i.test(name)) return "stop";
    if (/target|\bT\d\b|runner|take.?profit|\bTP\d?\b/i.test(name)) return "target";
    if (/entry|zone/i.test(name)) return "entry";
    return "level";
  }
  // "PDH 82,642.80, PDL 79,775.10" / "session 82,186.17, week 82,500.15" -> [{name, value, text}]
  function pairsOf(v){
    if (!v) return [];
    return stripTags(v).split(/,\s+(?=[A-Za-z])/).map(p => { const m = p.trim().match(/^(.+?)\s+([+-]?[\d,]*\.?\d+)\s*$/);
      return m ? { name: m[1], value: toNum(m[2]), text: m[2] } : null; }).filter(Boolean);
  }

  // ---------- rendering ----------
  const anim = () => document.documentElement.dataset.anim !== "off" && !matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fmt = (v, d = 2) => Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  const pctFrom = (v, base) => base ? (v / base - 1) * 100 : null;
  const signed = (v, d = 2) => (v > 0 ? "+" : v < 0 ? "−" : "") + fmt(Math.abs(v), d);
  const counter = (v, d = 2, pre = "", suf = "") => `<span class="num" data-count="${v}" data-dec="${d}" data-pre="${esc(pre)}" data-suf="${esc(suf)}">${esc(pre + fmt(v, d) + suf)}</span>`;

  function tableHtml(tb, cls = ""){
    if (!tb.head.length) return "";
    return `<div class="tbl-wrap"><table class="tbl ${cls}"><thead><tr>${tb.head.map(h => `<th>${inline(h)}</th>`).join("")}</tr></thead><tbody>` +
      tb.rows.map((r, k) => `<tr style="--i:${Math.min(k, 20)}">${r.map(c => `<td>${inline(c)}</td>`).join("")}</tr>`).join("") + "</tbody></table></div>";
  }
  const section = (id, title, body, extra = "") => `<section class="rsec" id="${id}" data-title="${esc(title)}"><div class="rsec-head"><h3>${esc(title)}</h3>${extra}</div>${body}</section>`;

  function verdictTone(s){
    const m = s.metrics, fails = s.flags.filter(f => f.level === "fail").length;
    if (m.verdict && /fail|no valid|invalid|reject|stale|unavailable/i.test(m.verdict)) return "bad";
    if (fails) return "bad";
    if (m.grade === "C") return "bad";
    if (s.flags.some(f => f.level === "warn") || m.grade === "B") return "warn";
    return "good";
  }

  function heroHtml(s, idx){
    const m = s.metrics, tone = verdictTone(s), fails = s.flags.filter(f => f.level === "fail");
    const verdict = m.verdict || (fails.length ? `${fails.length} blocking issue${fails.length > 1 ? "s" : ""}` : m.grade ? `Grade ${m.grade} setup` : "Analysis");
    const icon = { bad: "✕", warn: "!", good: "✓" }[tone];
    const dataLine = kvGet(s, /^Data$/i);
    const tiles = [];
    if (m.current != null) tiles.push(`<div class="tile"><span class="t-label">Current price</span><span class="t-value">${esc(m.currentText || fmt(m.current))}</span>${m.asOf ? `<span class="t-sub">as of ${esc(m.asOf)}</span>` : ""}</div>`);
    if (m.entry){ const d = pctFrom(m.entry.price, m.current);
      tiles.push(`<div class="tile"><span class="t-label">${esc(m.entry.name)}</span><span class="t-value">${esc(m.entry.priceText)}</span>${d != null && Math.abs(d) > 0.005 ? `<span class="t-sub">${signed(d)}% from current${m.atr ? ` · ${fmt(Math.abs(m.entry.price - m.current) / m.atr, 1)} ATR` : ""}</span>` : ""}</div>`); }
    if (m.wR != null){ const ok = m.wR >= m.minR, max = Math.max(m.wR, m.minR) * 1.35;
      tiles.push(`<div class="tile"><span class="t-label">Reward : risk (weighted)</span><span class="t-value">${counter(m.wR, 2, "", "R")}</span>` +
        `<span class="t-sub"><span class="st st-${ok ? "pass" : "fail"}">${ok ? "✓ meets" : "✕ below"}</span> the ${fmt(m.minR, 1)}R minimum</span>` +
        `<div class="bullet" role="img" aria-label="Weighted R ${m.wR} vs minimum ${m.minR}"><i class="b-fill ${ok ? "good" : "bad"}" style="--w:${Math.min(100, m.wR / max * 100)}%"></i><b class="b-mark" style="left:${m.minR / max * 100}%"></b></div></div>`); }
    if (m.liqPct != null) tiles.push(`<div class="tile"><span class="t-label">Liquidation distance</span><span class="t-value">${counter(m.liqPct, 2, "", "%")}</span><span class="t-sub">${m.liqAtr ? `${fmt(m.liqAtr, 2)} daily ATR away` : "away from entry"}</span></div>`);
    if (m.rules){ const r = m.rules;
      tiles.push(`<div class="tile"><span class="t-label">Your rules</span>` + (r.none ? `<span class="t-value small">none set</span><span class="t-sub">add them in user-preferences</span>`
        : `<span class="t-value">${counter(r.broken, 0)}${r.total ? `<small> / ${r.total}</small>` : ""}</span><span class="t-sub"><span class="st st-${r.broken ? "fail" : "pass"}">${r.broken ? "✕ broken" : "✓ none broken"}</span></span>`) + `</div>`); }
    if (m.pT1 != null) tiles.push(`<div class="tile"><span class="t-label">Chance T1 before stop</span><span class="t-value">${counter(m.pT1, 0, "", "%")}</span><span class="t-sub">judgment estimate</span></div>`);
    else if (m.stopAtr != null) tiles.push(`<div class="tile"><span class="t-label">Stop width</span><span class="t-value">${counter(m.stopAtr, 2, "", " ATR")}</span><span class="t-sub">${m.stopAtr > 2.5 ? "wide" : m.stopAtr < 0.8 ? "tight: noise can hit it" : "normal"}</span></div>`);
    const why = fails.slice(0, 3).map(f => `<li><span class="st st-fail">✕</span>${inline(f.text)}</li>`).join("");
    const more = s.flags.length > 3 || (s.flags.length && !fails.length);
    return section(`r${idx}-overview`, "Overview",
      `<div class="hero tone-${tone}">
        <div class="hero-main">
          <div class="hero-id">${m.symbol ? `<span class="sym">${esc(m.symbol)}</span>` : ""}${m.side ? `<span class="side side-${m.side}">${m.side === "long" ? "▲" : "▼"} ${m.side.toUpperCase()}</span>` : ""}${m.lev ? `<span class="pill">${esc(fmt(m.lev, m.lev % 1 ? 1 : 0))}x</span>` : ""}${m.timeframe ? `<span class="pill">${esc(m.timeframe)}</span>` : ""}</div>
          <div class="verdict"><span class="v-icon">${icon}</span><span>${inline(verdict)}</span></div>
          ${dataLine ? `<p class="hero-data">${inline(dataLine)}</p>` : ""}
        </div>
        ${m.grade ? `<div class="grade g-${m.grade}" title="Setup grade (A best, C weakest)"><svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="28" class="g-track"/><circle cx="32" cy="32" r="28" class="g-arc" pathLength="100" style="--g:${{ A: 100, B: 66, C: 33 }[m.grade]}"/></svg><span class="g-letter">${m.grade}</span><small>grade</small></div>` : ""}
      </div>
      ${tiles.length ? `<div class="tiles">${tiles.join("")}</div>` : ""}
      ${why ? `<div class="why"><h4>Why it ${tone === "bad" ? "fails" : "is flagged"}</h4><ul>${why}</ul></div>` : ""}
      ${more ? `<button class="link" data-goto="r${idx}-flags">See all ${s.flags.length} flags →</button>` : ""}`);
  }

  // ---- interactive price ladder (SVG) ----
  function ladderHtml(s, idx){
    const m = s.metrics; if (m.rows.length < 2) return "";
    const far = isFar(m);
    const opts = (far ? `<button class="seg-btn active" data-zoom="setup">Setup</button><button class="seg-btn" data-zoom="full">Include current price</button>` : "") +
      (m.refs.length ? `<button class="seg-btn toggle" data-refs aria-pressed="false">Reference levels</button>` : "");
    const ctl = opts ? `<div class="seg sm" role="group" aria-label="Ladder options">${opts}</div>` : "";
    const kinds = [["target", "Target"], ["entry", "Entry / zone"], ["stop", "Stop"], ["liq", "Liquidation"], ["now", "Current price"], ["ref", "Reference"]]
      .filter(([k]) => k === "ref" ? m.refs.length : m.rows.some(r => r.kind === k));
    const legend = `<div class="legend">${kinds.map(([k, t]) => `<span>${legendShape(k)}${t}</span>`).join("")}</div>`;
    return section(`r${idx}-levels`, "Price ladder",
      `${legend}<div class="ladder-grid"><div class="ladder" data-ladder="${idx}" data-zoom="${far ? "setup" : "full"}" data-refs="0"></div>
       <div class="ladder-table">${milestoneTable(s, idx)}</div></div><p class="hint-line">Hover or tab through a level for details. Rows are sorted by price, like the chart.</p>`, ctl);
  }
  function isFar(m){
    const pts = m.rows.filter(r => r.kind !== "now").map(r => r.price); if (m.current == null || pts.length < 2) return false;
    const hi = Math.max(...pts), lo = Math.min(...pts), span = hi - lo || hi * 0.01;
    return m.current > hi + span * 1.5 || m.current < lo - span * 1.5;
  }
  function milestoneTable(s, idx){
    const tb = s.milestones;
    return `<table class="tbl ms"><thead><tr>${tb.head.map(h => `<th>${inline(h)}</th>`).join("")}</tr></thead><tbody>` +
      s.metrics.rows.map((r, k) => { const raw = tb.rows.find(c => (c[0] || "").replace(/\*/g, "").trim() === r.name && toNum(c[1]) === r.price) || [];
        return `<tr class="k-${r.kind}" data-i="${k}" style="--i:${k}">${tb.head.map((h, j) => `<td>${j === 0 ? legendShape(r.kind) : ""}${inline(raw[j] || "")}</td>`).join("")}</tr>`; }).join("") +
      "</tbody></table>";
  }
  // Marker shape per level type, so identity never rests on the green/red pair alone (colour-blind safe).
  function marker(kind, x, y, cls = "dot"){
    if (kind === "target") return `<path class="${cls}" d="M${x} ${y - 7}L${x + 7} ${y}L${x} ${y + 7}L${x - 7} ${y}Z"/>`;
    if (kind === "stop") return `<rect class="${cls}" x="${x - 5.5}" y="${y - 5.5}" width="11" height="11" rx="1.5"/>`;
    if (kind === "liq") return `<path class="${cls}" d="M${x - 7} ${y - 5}L${x + 7} ${y - 5}L${x} ${y + 7}Z"/>`;
    return `<circle class="${cls}" cx="${x}" cy="${y}" r="${kind === "ref" || kind === "level" ? 4 : 6}"/>`;
  }
  const legendShape = k => `<svg class="lg-svg" viewBox="0 0 16 16" aria-hidden="true"><g class="rung k-${k}" style="animation:none">${marker(k, 8, 8)}</g></svg>`;

  function drawLadder(el, s){
    const m = s.metrics, zoom = el.dataset.zoom, showRefs = el.dataset.refs === "1";
    let pts = m.rows.map((r, i) => Object.assign({ i }, r));
    if (showRefs) pts = pts.concat(m.refs.map(r => ({ i: -1, name: r.name, priceText: r.text, price: r.value, cells: {}, kind: "ref" })));
    let off = null;
    if (zoom === "setup"){ const now = pts.find(p => p.kind === "now"); pts = pts.filter(p => p.kind !== "now");
      if (now){ const core = pts.filter(p => p.kind !== "ref").map(p => p.price), hi = Math.max(...core), lo = Math.min(...core), span = hi - lo || 1;
        pts = pts.filter(p => p.kind !== "ref" || (p.price <= hi + span && p.price >= lo - span));
        off = { now, above: now.price > hi }; } }
    pts.sort((a, b) => b.price - a.price);
    // drawn at the element's real width so labels stay at their CSS size instead of being scaled down
    const W = Math.max(340, Math.round(el.clientWidth || 560)), axisX = 104, labelX = 130, gap = 26, padT = off && off.above ? 52 : 26, padB = off && !off.above ? 52 : 26;
    const H = Math.max(260, pts.length * gap + padT + padB);
    const hi = Math.max(...pts.map(p => p.price)), lo = Math.min(...pts.map(p => p.price)), span = (hi - lo) || hi * 0.01 || 1;
    const y = p => padT + (hi - p) / span * (H - padT - padB);
    const ys = pts.map(p => y(p.price)), ly = ys.slice();
    for (let k = 1; k < ly.length; k++) ly[k] = Math.max(ly[k], ly[k - 1] + gap);
    ly[ly.length - 1] = Math.min(ly[ly.length - 1], H - padB);
    for (let k = ly.length - 2; k >= 0; k--) ly[k] = Math.min(ly[k], ly[k + 1] - gap);
    const find = k => pts.find(p => p.kind === k);
    const entry = m.entry && pts.find(p => p.i === m.rows.indexOf(m.entry)), stop = find("stop");
    const targets = pts.filter(p => p.kind === "target"), zone = pts.filter(p => /zone/i.test(p.name));
    const band = (a, b, cls) => { if (a == null || b == null) return "";
      const top = Math.min(y(a), y(b)), h = Math.max(2, Math.abs(y(a) - y(b)));
      return `<rect class="band ${cls}" x="${axisX}" width="${W - axisX - 8}" y="${top}" height="${h}" rx="4"/>` +
        (h >= 16 && cls !== "zone" ? `<text class="band-t" x="${W - 14}" y="${top + h / 2 + 4}" text-anchor="end">${cls}</text>` : ""); };
    const nowP = find("now"), origin = nowP ? y(nowP.price) : off ? (off.above ? 8 : H - 8) : H / 2;
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price ladder for ${esc(m.symbol || "this setup")}">`;
    if (entry && stop) svg += band(entry.price, stop.price, "risk");
    if (entry && targets.length){ const far = targets.reduce((a, b) => Math.abs(b.price - entry.price) > Math.abs(a.price - entry.price) ? b : a); svg += band(entry.price, far.price, "reward"); }
    if (zone.length >= 2) svg += band(Math.max(...zone.map(z => z.price)), Math.min(...zone.map(z => z.price)), "zone");
    svg += `<line class="axis" x1="${axisX}" x2="${axisX}" y1="${padT - 10}" y2="${H - padB + 10}"/>`;
    pts.forEach((p, k) => {
      const yy = ys[k], l = ly[k], meta = p.cells.R && p.cells.R !== "" ? p.cells.R : p.cells.Dist || "";
      svg += `<g class="rung k-${p.kind}" tabindex="0" data-i="${p.i}" data-k="${k}" style="--from:${(origin - yy).toFixed(1)}px;--d:${k * 45}ms">` +
        `<rect class="hit" x="0" y="${l - gap / 2}" width="${W}" height="${gap}"/>` +
        `<line class="lvl" x1="${axisX}" x2="${W - 8}" y1="${yy}" y2="${yy}"/>` +
        (Math.abs(l - yy) > 1 ? `<path class="leader" d="M${axisX + 7} ${yy} L${labelX - 6} ${l}"/>` : "") +
        (p.kind === "now" ? `<circle class="ping" cx="${axisX}" cy="${yy}" r="6"/>` : "") +
        marker(p.kind, axisX, yy) +
        `<text class="price" x="${axisX - 14}" y="${l + 4}" text-anchor="end">${esc(p.priceText)}</text>` +
        `<text class="name" x="${labelX}" y="${l + 4}">${esc(p.kind === "now" ? "Current price" : p.name)}${meta && p.kind !== "now" ? `<tspan class="meta" dx="8">${esc(stripTags(meta))}</tspan>` : ""}</text></g>`;
    });
    if (off){ const oy = off.above ? 16 : H - 16, d = m.entry ? pctFrom(m.entry.price, off.now.price) : null;
      svg += `<g class="offscale" tabindex="0" data-i="${m.rows.indexOf(off.now)}"><text x="${axisX}" y="${oy + 4}" text-anchor="middle" class="arrow">${off.above ? "▲" : "▼"}</text>` +
        `<text class="name" x="${labelX}" y="${oy + 4}">Current price ${esc(off.now.priceText)}<tspan class="meta" dx="8">off this scale${d != null ? `: entry is ${signed(d)}% from it` : ""}</tspan></text></g>`; }
    el.innerHTML = svg + `</svg><div class="tip" role="tooltip" hidden></div>`;
    if (!anim()) el.querySelectorAll(".rung").forEach(g => g.style.animation = "none");
  }
  function tipHtml(s, i, kind, el){
    const r = i >= 0 ? s.metrics.rows[i] : null;
    if (!r){ const k = +el.dataset.k; const ref = el.querySelector(".name"); return `<b>${esc(ref ? ref.textContent : "")}</b>`; }
    const d = pctFrom(r.price, s.metrics.current);
    const cells = Object.entries(r.cells).filter(([h]) => !/^tag$/i.test(h)).map(([h, v]) => `<dt>${esc(h)}</dt><dd>${inline(v)}</dd>`).join("");
    return `<b>${esc(r.name)}</b><span class="tip-price">${esc(r.priceText)}</span>` +
      `<dl>${cells || (d != null ? `<dt>From current</dt><dd>${signed(d)}%</dd>` : "")}</dl>` + (r.cells.Tag ? inline(r.cells.Tag) : "");
  }
  function wireLadder(root, s, idx){
    const el = root.querySelector(`[data-ladder="${idx}"]`); if (!el) return;
    const sec = el.closest(".rsec"), table = sec.querySelector(".ms");
    const draw = () => { drawLadder(el, s); bindRungs(); };
    function highlight(i, on){
      el.querySelectorAll(`.rung[data-i="${i}"]`).forEach(g => g.classList.toggle("hl", on));
      if (table) table.querySelectorAll(`tr[data-i="${i}"]`).forEach(tr => tr.classList.toggle("hl", on));
    }
    function bindRungs(){
      const tip = el.querySelector(".tip");
      el.querySelectorAll(".rung, .offscale").forEach(g => {
        const i = +g.dataset.i;
        const show = () => { highlight(i, true); tip.innerHTML = tipHtml(s, i, null, g); tip.hidden = false;
          const b = g.querySelector(".dot, .arrow").getBoundingClientRect(), host = el.getBoundingClientRect();
          const x = Math.min(host.width - 230, Math.max(8, b.left - host.left + 24)), yy = b.top - host.top + b.height / 2;
          tip.style.left = x + "px"; tip.style.top = Math.max(4, Math.min(host.height - tip.offsetHeight - 4, yy - tip.offsetHeight / 2)) + "px"; };
        const hide = () => { highlight(i, false); tip.hidden = true; };
        g.addEventListener("mouseenter", show); g.addEventListener("mouseleave", hide);
        g.addEventListener("focus", show); g.addEventListener("blur", hide);
      });
    }
    if (table) table.querySelectorAll("tr[data-i]").forEach(tr => {
      tr.addEventListener("mouseenter", () => highlight(+tr.dataset.i, true));
      tr.addEventListener("mouseleave", () => highlight(+tr.dataset.i, false));
    });
    sec.querySelectorAll("[data-zoom]").forEach(b => b.onclick = () => {
      sec.querySelectorAll("[data-zoom]").forEach(x => x.classList.toggle("active", x === b)); el.dataset.zoom = b.dataset.zoom; draw(); });
    const rb = sec.querySelector("[data-refs]");
    if (rb) rb.onclick = () => { const on = el.dataset.refs !== "1"; el.dataset.refs = on ? "1" : "0"; rb.classList.toggle("active", on); rb.setAttribute("aria-pressed", on); draw(); };
    draw();
    let lastW = el.clientWidth;
    new ResizeObserver(() => { if (Math.abs(el.clientWidth - lastW) > 24){ lastW = el.clientWidth; draw(); el.querySelectorAll(".rung").forEach(g => g.style.animation = "none"); } }).observe(el);
  }

  // ---- liquidation-odds bars, drawn at real width ----
  function drawOdds(el, m){
    const { days, pct } = m.odds, lim = m.oddsLimit, top = Math.max(...pct, lim ? lim.limit : 0, 10) * 1.15;
    const W = Math.max(300, Math.round(el.clientWidth || 420)), H = 200, padL = 40, padR = lim ? 112 : 10, padB = 28, padT = 18;
    const step = (W - padL - padR) / days.length, bw = Math.min(46, step * 0.55);
    const y = v => padT + (1 - v / top) * (H - padT - padB);
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Historical odds a move reached your liquidation price">`;
    [0, top / 2].forEach(v => svg += `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}"/><text class="ax" x="${padL - 8}" y="${y(v) + 4}" text-anchor="end">${Math.round(v)}%</text>`);
    days.forEach((d, k) => { const x = padL + step * k + (step - bw) / 2, h = H - padB - y(pct[k]);
      svg += `<g class="bar" tabindex="0" style="--d:${k * 90}ms"><title>Within ${d} day${d > 1 ? "s" : ""}: reached on ${pct[k]}% of days</title>` +
        `<rect x="${x}" y="${y(pct[k])}" width="${bw}" height="${Math.max(1, h)}" rx="4"/>` +
        `<text class="val" x="${x + bw / 2}" y="${y(pct[k]) - 7}" text-anchor="middle">${pct[k]}%</text>` +
        `<text class="ax" x="${x + bw / 2}" y="${H - 9}" text-anchor="middle">${d} day${d > 1 ? "s" : ""}</text></g>`; });
    if (lim && lim.limit < top) svg += `<line class="limit" x1="${padL}" x2="${W - padR + 6}" y1="${y(lim.limit)}" y2="${y(lim.limit)}"/>` +
      `<text class="limit-t" x="${W - padR + 12}" y="${y(lim.limit) - 2}">your ${lim.days}-day</text><text class="limit-t" x="${W - padR + 12}" y="${y(lim.limit) + 12}">limit ${lim.limit}%</text>`;
    el.innerHTML = svg + "</svg>";
  }

  // ---- risk: leverage scale, liquidation-odds bars ----
  function riskHtml(s, idx){
    const m = s.metrics, parts = [];
    if (m.lev != null && (m.safeLev != null || m.bufferLev != null)){
      const max = Math.max(m.lev, m.safeLev || 0, m.bufferLev || 0) * 1.12, at = v => Math.min(100, v / max * 100);
      const edge = v => at(v) < 14 ? " left" : at(v) > 86 ? " right" : "";   // keep tick captions inside the card
      const over = m.safeLev != null && m.lev > m.safeLev;
      parts.push(`<div class="card"><h4>Leverage vs volatility</h4>
        <div class="scale" role="img" aria-label="Your leverage ${m.lev}x; historically safe ${m.safeLev}x">
          ${m.safeLev != null ? `<i class="zone-safe" style="--w:${at(m.safeLev)}%"></i>` : ""}
          ${m.safeLev != null ? `<span class="tick${edge(m.safeLev)}" style="left:${at(m.safeLev)}%"><b>${fmt(m.safeLev, 1)}x</b><small>historically safe</small></span>` : ""}
          ${m.bufferLev != null ? `<span class="tick alt${edge(m.bufferLev)}" style="left:${at(m.bufferLev)}%"><b>${fmt(m.bufferLev, 1)}x</b><small>1 ATR liq buffer</small></span>` : ""}
          <span class="you ${over ? "bad" : "good"}" style="--x:${at(m.lev)}%"><b>You: ${fmt(m.lev, m.lev % 1 ? 1 : 0)}x</b></span>
        </div>
        <p class="explain">${m.safeLev != null ? `<span class="st st-${over ? "fail" : "pass"}">${over ? "✕" : "✓"}</span> ${over ? `${fmt(m.lev / m.safeLev, 1)}× the leverage that historically stayed safe` : "Within the historically safe leverage"}: ${fmt(m.safeLev, 1)}x kept ${m.safeDays || 5}-day liquidation odds at or below ${m.safePct || 5}%.` : ""}</p></div>`);
    }
    if (m.odds){
      const note = m.odds.note ? ` (${esc(m.odds.note)})` : "";
      parts.push(`<div class="card"><h4>How often price reached your liquidation</h4><p class="explain">Share of past days where a move this size happened within the window${note}${/forecast/i.test(note) ? "" : ". History, not a forecast"}.</p><div class="odds" data-odds="${idx}"></div></div>`);
    }
    const lvv = kvGet(s, /^Leverage vs volatility/i);
    if (lvv && !m.odds) parts.push(`<div class="card"><h4>Leverage vs volatility</h4><p>${inline(lvv)}</p></div>`);
    const sizing = s.facts.filter(f => /weighted R|units|margin|stop width/i.test(f));
    if (sizing.length) parts.push(`<div class="card wide"><h4>Sizing &amp; risk numbers</h4><ul class="facts">${sizing.map(f => `<li>${inline(f)}</li>`).join("")}</ul></div>`);
    const pi = kvGet(s, /^Portfolio impact/i); if (pi) parts.push(`<div class="card wide"><h4>Portfolio impact</h4><p>${inline(pi)}</p></div>`);
    return parts.length ? section(`r${idx}-risk`, "Risk", `<div class="cards">${parts.join("")}</div>`) : "";
  }

  // ---- flags ----
  function flagsHtml(s, idx){
    const rules = kvGet(s, /^Rules$/i);
    if (!s.flags.length && !rules) return "";
    const counts = { fail: 0, warn: 0, info: 0, pass: 0 }; s.flags.forEach(f => counts[f.level]++);
    const order = { fail: 0, warn: 1, info: 2, pass: 3 };
    const names = { fail: "Fails", warn: "Warnings", info: "Info", pass: "Passes" };
    const filt = `<div class="seg sm" role="group" aria-label="Filter flags"><button class="seg-btn active" data-filter="all">All <span class="cnt">${s.flags.length}</span></button>` +
      Object.keys(counts).filter(k => counts[k]).map(k => `<button class="seg-btn" data-filter="${k}"><span class="st st-${k}">${STATUS_ICON[k]}</span> ${names[k]} <span class="cnt">${counts[k]}</span></button>`).join("") + `</div>`;
    const cards = s.flags.slice().sort((a, b) => order[a.level] - order[b.level]).map((f, k) => {
      const rule = /breaks your rule/i.test(f.text);
      return `<li class="flag f-${f.level}" data-level="${f.level}" style="--i:${k}"><span class="st st-${f.level}">${STATUS_ICON[f.level]} ${esc(f.label)}</span>` +
        `<span class="f-text">${rule ? `<span class="pill rule">your rule</span>` : ""}${inline(f.text.replace(/^breaks your rule:\s*/i, ""))}</span></li>`; }).join("");
    return section(`r${idx}-flags`, "Flags", `${rules ? `<p class="rules-line"><b>Rules</b> ${inline(rules)}</p>` : ""}<ul class="flags">${cards}</ul>`, s.flags.length ? filt : "");
  }

  // ---- context: structure, momentum, positioning, levels, lists, leftovers ----
  function contextHtml(s, idx){
    const m = s.metrics, cards = [];
    const st = kvGet(s, /^Structure/i);
    if (st || m.range){
      let bar = "";
      if (m.range){ const r = m.range;
        bar = `<div class="range" role="img" aria-label="Price at ${r.pos}% of the range"><i class="third"></i><i class="third"></i><i class="third"></i>` +
          (r.zone != null ? `<span class="rmark zone" style="left:${r.zone}%"><small>zone ${r.zone}%</small></span>` : "") +
          `<span class="rmark now" style="left:${Math.max(0, Math.min(100, r.pos))}%"><small>now ${r.pos}%</small></span></div>` +
          `<div class="range-ends"><span>${esc(r.loText)}<small>range low</small></span><span class="mid">lower · middle · upper third</span><span>${esc(r.hiText)}<small>range high</small></span></div>`; }
      cards.push(`<div class="card"><h4>Market structure</h4>${bar}${st ? `<p>${inline(st)}</p>` : ""}</div>`);
    }
    const cv = kvGet(s, /^Conviction/i), gr = kvGet(s, /^Grade/i), al = kvGet(s, /^Alignment/i);
    if (m.conv != null || cv || gr || al){
      let meter = "";
      if (m.conv != null){ const max = m.convMax || 8, v = Math.max(-max, Math.min(max, m.conv)), half = 50, w = Math.abs(v) / max * 50;
        meter = `<div class="meter" role="img" aria-label="Conviction ${v} of ${max}"><i class="m-mid"></i><i class="m-fill ${v >= 0 ? "pos" : "neg"}" style="left:${v >= 0 ? half : half - w}%;--w:${w}%"></i></div>` +
          `<div class="meter-ends"><span>−${max} against</span><b>${signed(v, 0)} of ${max}${m.convNote ? ` · ${esc(m.convNote.trim())}` : ""}</b><span>+${max} with you</span></div>`; }
      cards.push(`<div class="card"><h4>Momentum &amp; conviction</h4>${meter}${[["Conviction", cv], ["Grade", gr], ["Alignment", al]].filter(x => x[1])
        .map(([k, x]) => `<p><b>${k}</b> ${inline(x)}</p>`).join("")}</div>`);
    }
    const pos = kvGet(s, /^Positioning/i);
    if (pos) cards.push(`<div class="card"><h4>Positioning <small>context, not graded</small></h4><div class="chips">${stripTags(pos).split(/;\s*/).map(p => `<span class="chip static">${esc(p)}</span>`).join("")}</div></div>`);
    const ratio = s.kv.filter(k => /^Ratio/i.test(k.key)); ratio.forEach(r => s.used.add(r));
    if (ratio.length) cards.push(`<div class="card"><h4>Ratios</h4>${ratio.map(r => `<p><b>${inline(r.key.replace(/^Ratio\s*/i, ""))}</b> ${inline(r.value)}</p>`).join("")}</div>`);
    const lvls = [];
    if (m.refs.length){ kvGet(s, /^Reference levels/i); lvls.push(["Reference levels", m.refs]); }
    const vw = kvGet(s, /^VWAP/i); if (vw) lvls.push(["VWAP", pairsOf(vw)]);
    s.kv.filter(k => /EMA\d/i.test(k.key) && !s.used.has(k)).forEach(k => { s.used.add(k);
      const nums = stripTags(k.value).split(/\s*\/\s*/).map(x => x.trim()); const per = (k.key.match(/EMA([\d\/]+)/i) || [])[1];
      const labels = per ? per.split("/").map(p => "EMA" + p) : nums.map((_, j) => "EMA " + (j + 1));
      lvls.push([k.key.replace(/EMA[\d\/]+/i, "EMAs").trim(), nums.map((x, j) => ({ name: labels[j] || "", value: toNum(x), text: x }))]); });
    if (lvls.length){
      const chip = r => { const d = pctFrom(r.value, m.current);
        return `<span class="lvl-chip"><small>${esc(r.name)}</small><b>${esc(r.text)}</b>${d != null && !isNaN(d) ? `<em>${d >= 0 ? "▲" : "▼"} ${signed(d)}%</em>` : ""}</span>`; };
      cards.push(`<div class="card wide"><h4>Levels &amp; averages <small>distance from current price</small></h4>${lvls.map(([t, rs]) => `<div class="lvl-row"><span class="lvl-title">${esc(t)}</span><div class="lvl-chips">${rs.map(chip).join("")}</div></div>`).join("")}</div>`);
    }
    const accs = [];
    const listOrder = l => /what would change/i.test(l.key) ? 0 : /bear case/i.test(l.key) ? 1 : /notes/i.test(l.key) ? 2 : 3;
    s.lists.slice().sort((a, b) => listOrder(a) - listOrder(b)).forEach((l, k) =>
      accs.push(`<details class="acc"${k === 0 || /notes/i.test(l.key) ? " open" : ""}><summary>${inline(l.key)}<span class="cnt">${l.items.length}</span></summary><div class="acc-body"><ul>${l.items.map(x => `<li>${inline(x)}</li>`).join("")}</ul></div></details>`));
    s.tables.forEach(t => accs.push(`<details class="acc" open><summary>${t.title ? inline(t.title) : "Table"}</summary><div class="acc-body">${tableHtml(t)}</div></details>`));
    const left = s.kv.filter(k => !s.used.has(k) && !/^(Data|Request)$/i.test(k.key));
    const facts = s.facts.filter(f => !/^(Current price|Weighted R|units|margin|stop width|max leverage)/i.test(f));
    const req = kvGet(s, /^Request$/i);
    if (left.length || facts.length || req || s.paras.length)
      accs.push(`<details class="acc"><summary>More details</summary><div class="acc-body">${req ? `<p><b>Request</b> ${inline(req)}</p>` : ""}<dl class="kv">${left.map(k => `<dt>${inline(k.key)}</dt><dd>${inline(k.value)}</dd>`).join("")}</dl>` +
        `${facts.length ? `<ul class="facts">${facts.map(f => `<li>${inline(f)}</li>`).join("")}</ul>` : ""}${s.paras.map(md).join("")}</div></details>`);
    if (!cards.length && !accs.length) return "";
    return section(`r${idx}-context`, "Context", `${cards.length ? `<div class="cards">${cards.join("")}</div>` : ""}${accs.length ? `<div class="accs">${accs.join("")}</div>` : ""}`);
  }

  function genericHtml(s, idx){
    // An untitled result is usually an error or a one-line notice: lift its first line into the headline.
    let head = s.title;
    if (!head && s.facts.length) head = s.facts.shift();
    else if (!head && s.kv.length){ const k = s.kv.shift(); head = `**${k.key}:** ${k.value}`; }
    const failed = !s.title && /fail|error|couldn't|could not|not found|timed out|lost|rejected|no credit|isn't logged in|setup needed/i.test(head || "");
    const tone = failed || s.flags.some(f => f.level === "fail") ? "bad" : s.flags.some(f => f.level === "warn") ? "warn" : s.flags.length ? "good" : "neutral";
    const body = `<div class="hero tone-${tone} compact"><div class="hero-main"><div class="verdict">${tone === "bad" && failed ? `<span class="v-icon">✕</span>` : ""}<span>${inline(head || "Report")}</span></div></div></div>` +
      (s.kv.length ? `<dl class="kv">${s.kv.map(k => `<dt>${inline(k.key)}</dt><dd>${inline(k.value)}</dd>`).join("")}</dl>` : "") +
      (s.facts.length ? s.facts.map(f => `<p>${inline(f)}</p>`).join("") : "") +
      s.tables.map(t => (t.title ? `<h4>${inline(t.title)}</h4>` : "") + tableHtml(t)).join("") +
      s.lists.map(l => `<h4>${inline(l.key)}</h4><ul>${l.items.map(x => `<li>${inline(x)}</li>`).join("")}</ul>`).join("") +
      s.paras.map(md).join("");
    s.kv.forEach(k => s.used.add(k));
    return section(`r${idx}-overview`, "Overview", body) + flagsHtml(s, idx);
  }

  function instrumentHtml(s, idx){
    return heroHtml(s, idx) + (document.documentElement.dataset.ladder === "off" ? "" : ladderHtml(s, idx)) + riskHtml(s, idx) + flagsHtml(s, idx) + contextHtml(s, idx);
  }

  // ---------- mount ----------
  function render(root, markdown, opts = {}){
    const rep = parse(markdown);
    const secs = rep.sections;
    const label = s => s.metrics.symbol ? `${s.metrics.symbol}${s.metrics.side ? " " + s.metrics.side : ""}` : (s.title || "Report").slice(0, 28);
    const tabs = secs.length > 1 ? `<div class="itabs" role="tablist">${secs.map((s, k) => `<button role="tab" class="itab${k ? "" : " active"}" data-tab="${k}" aria-selected="${!k}">` +
      `${s.metrics.grade ? `<span class="badge ${s.metrics.grade}">${s.metrics.grade}</span>` : `<span class="dot tone-${verdictTone(s)}"></span>`}${esc(label(s))}</button>`).join("")}</div>` : "";
    const panes = secs.map((s, k) => `<div class="ipane" data-pane="${k}"${k ? " hidden" : ""}>${s.isInstrument ? instrumentHtml(s, k) : genericHtml(s, k)}</div>`).join("");
    const pre = rep.preamble.filter(l => l.trim()).join("\n");
    root.innerHTML = tabs + `<nav class="secnav" aria-label="Report sections"><div class="secnav-track"></div><i class="secnav-ind"></i><div class="secnav-actions"><button class="ghost sm" data-copy>Copy report</button></div></nav>` +
      (pre ? `<div class="preamble">${md(pre)}</div>` : "") + panes +
      `<section class="rsec" id="r-full" data-title="Full report"><details class="acc full"><summary>Full report text</summary><div class="acc-body md">${md(rep.raw.split("\n").filter(l => !/not financial advice/i.test(l)).join("\n"))}</div></details></section>` +
      (rep.footer ? `<p class="run-meta">${inline(rep.footer)}</p>` : "") +
      `<p class="disclaimer">${esc(rep.disclaimer || "Analysis only, not financial advice. You make and execute all trading decisions.")}</p>`;
    secs.forEach((s, k) => {
      if (!s.isInstrument) return;
      wireLadder(root, s, k);
      const od = root.querySelector(`[data-odds="${k}"]`);
      if (od){ drawOdds(od, s.metrics); let w = od.clientWidth;
        new ResizeObserver(() => { if (Math.abs(od.clientWidth - w) > 24){ w = od.clientWidth; drawOdds(od, s.metrics); } }).observe(od); }
    });
    wireCommon(root, opts);
    return rep;
  }

  function wireCommon(root, opts){
    const nav = root.querySelector(".secnav"), track = nav.querySelector(".secnav-track"), ind = nav.querySelector(".secnav-ind");
    let io = null;
    const smooth = () => anim() ? "smooth" : "auto";
    function buildNav(){
      const pane = root.querySelector(".ipane:not([hidden])");
      const secs = [...(pane ? pane.querySelectorAll(".rsec") : []), root.querySelector("#r-full")].filter(Boolean);
      track.innerHTML = secs.map(s => `<button class="secnav-btn" data-target="${s.id}">${esc(s.dataset.title)}</button>`).join("");
      track.querySelectorAll("button").forEach(b => b.onclick = () => document.getElementById(b.dataset.target).scrollIntoView({ behavior: smooth(), block: "start" }));
      if (io) io.disconnect();
      io = new IntersectionObserver(es => { es.forEach(e => { if (e.isIntersecting) activate(e.target.id); }); }, { rootMargin: "-30% 0px -60% 0px" });
      secs.forEach(s => io.observe(s));
      activate(secs[0] && secs[0].id);
    }
    function activate(id){
      const b = track.querySelector(`[data-target="${id}"]`); if (!b) return;
      track.querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
      ind.style.width = b.offsetWidth + "px"; ind.style.transform = `translateX(${b.offsetLeft - track.scrollLeft}px)`;
    }
    track.addEventListener("scroll", () => { const a = track.querySelector(".active"); if (a) activate(a.dataset.target); });
    root.querySelectorAll(".itab").forEach(t => t.onclick = () => {
      root.querySelectorAll(".itab").forEach(x => { x.classList.toggle("active", x === t); x.setAttribute("aria-selected", x === t); });
      root.querySelectorAll(".ipane").forEach(p => p.hidden = p.dataset.pane !== t.dataset.tab);
      const pane = root.querySelector(`.ipane[data-pane="${t.dataset.tab}"]`); pane.classList.remove("swap"); void pane.offsetWidth; pane.classList.add("swap");
      countUp(pane); buildNav(); });
    root.querySelectorAll("[data-goto]").forEach(b => b.onclick = () => document.getElementById(b.dataset.goto).scrollIntoView({ behavior: smooth(), block: "start" }));
    root.querySelectorAll(".rsec").forEach(sec => sec.querySelectorAll("[data-filter]").forEach(b => b.onclick = () => {
      sec.querySelectorAll("[data-filter]").forEach(x => x.classList.toggle("active", x === b));
      sec.querySelectorAll(".flag").forEach(f => f.classList.toggle("out", b.dataset.filter !== "all" && f.dataset.level !== b.dataset.filter)); }));
    const copy = root.querySelector("[data-copy]"); if (copy && opts.onCopy) copy.onclick = opts.onCopy;
    // reveal sections as they scroll into view
    if (anim()){ const ro = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting){ e.target.classList.add("in"); ro.unobserve(e.target); } }), { threshold: 0.08 });
      root.querySelectorAll(".rsec").forEach(s => ro.observe(s)); }
    else root.querySelectorAll(".rsec").forEach(s => s.classList.add("in"));
    countUp(root); buildNav();
    window.addEventListener("resize", () => { const a = track.querySelector(".active"); if (a) activate(a.dataset.target); });
  }

  function countUp(scope){
    scope.querySelectorAll(".num[data-count]").forEach(el => {
      const end = parseFloat(el.dataset.count), dec = +el.dataset.dec, pre = el.dataset.pre || "", suf = el.dataset.suf || "";
      if (!anim() || isNaN(end)){ el.textContent = pre + fmt(end, dec) + suf; return; }
      const t0 = performance.now(), dur = 900;
      const step = t => { const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
        el.textContent = pre + fmt(end * e, dec) + suf; if (p < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    });
  }

  return { parse, render, md, inline, esc };
})();
