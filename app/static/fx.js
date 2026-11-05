"use strict";
// Arcade effects: a short confetti burst for milestones (level up, daily challenge, T1 hit).
// Skipped when animations are off in Settings or the OS asks for reduced motion (animOn from app.js).
const FX = (() => {
  const COLORS = ["#7c5cff", "#22d3ee", "#ff4fd8", "#16c27a", "#fab219"];
  let canvas = null, parts = [], raf = 0;

  function confetti(opts = {}){
    if (typeof animOn === "function" && !animOn()) return;
    const n = opts.count || 140, x0 = (opts.x ?? 0.5) * innerWidth, y0 = (opts.y ?? 0.35) * innerHeight;
    if (!canvas){
      canvas = document.createElement("canvas"); canvas.className = "fx-canvas"; canvas.setAttribute("aria-hidden", "true");
      document.body.appendChild(canvas);
    }
    canvas.width = innerWidth * devicePixelRatio; canvas.height = innerHeight * devicePixelRatio;
    for (let i = 0; i < n; i++){
      const a = Math.random() * Math.PI * 2, v = 4 + Math.random() * 9;
      parts.push({ x: x0, y: y0, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 6, r: 3 + Math.random() * 4,
        rot: Math.random() * 6, vr: (Math.random() - .5) * .4, c: COLORS[i % COLORS.length], life: 90 + Math.random() * 50 });
    }
    if (!raf) raf = requestAnimationFrame(tick);
  }
  function tick(){
    const g = canvas.getContext("2d"), k = devicePixelRatio;
    g.clearRect(0, 0, canvas.width, canvas.height);
    parts = parts.filter(p => p.life-- > 0);
    for (const p of parts){
      p.vy += 0.32; p.vx *= 0.985; p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      g.save(); g.globalAlpha = Math.min(1, p.life / 40); g.translate(p.x * k, p.y * k); g.rotate(p.rot);
      g.fillStyle = p.c; g.fillRect(-p.r * k, -p.r * k / 2, p.r * 2 * k, p.r * k); g.restore();
    }
    if (parts.length) raf = requestAnimationFrame(tick);
    else { raf = 0; g.clearRect(0, 0, canvas.width, canvas.height); }
  }
  // one celebration per key (e.g. "challenge:2026-11-05"), remembered across reloads
  function once(key, fn){
    const seen = JSON.parse(localStorage.getItem("ta_fx_seen") || "[]");
    if (seen.includes(key)) return false;
    localStorage.setItem("ta_fx_seen", JSON.stringify(seen.concat(key).slice(-60)));
    fn(); return true;
  }
  return { confetti, once };
})();
window.FX = FX;
