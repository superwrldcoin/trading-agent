# Skill: multi-timeframe-momentum (EMA + VWAP conviction)

## Purpose
Grade how strongly trend (EMAs) and volume-weighted value (VWAPs) agree with a proposed setup's direction, A, B, or C. This is the **conviction** behind every setup. The user chose EMA + VWAP as the conviction indicators on 2026-10-04. RSI, MACD, and the 5-timeframe alignment are context only.

## Required inputs
- Output of `python tools/indicators.py <SYMBOL>`. It fetches 15M, 1H, 4H, 1D, and 1W and prints the conviction table, scores, and grades for both sides.
- Trade direction (long or short): from the user's request or from `levels-and-entries`

## Indicators
**Conviction inputs (fixed; nothing else changes the grade):**
| Indicator | Settings | Source bars |
|---|---|---|
| EMA | 21, 50, 200 on close | 4H and 1D |
| Session VWAP | typical price (H+L+C)/3 × volume, restarting each session | 15M |
| Weekly anchored VWAP | restarts Monday of the session week | 1H |
| Monthly anchored VWAP | restarts on the 1st of the session month | 1H |

Sessions, weeks, and months use the same calendar as PDH/PWH/PMthH (`memory/core.md`): crypto UTC days, gold 17:00 ET roll, CME 18:00 ET roll, equities regular session.

**Context only:** EMA 9, RSI14, MACD (12, 26, 9), ATR14, volume trend, and the 5-TF alignment score (EMA20/50 + RSI per timeframe), plus the old EMA20/50 + RSI grade, which the tool prints as "Momentum context". Mention them in Notes when they disagree with conviction, but never use them to change the grade.

## Procedure
1. Run `python tools/indicators.py <SYMBOL>`. Read the **Conviction (EMA + VWAP)** table.
2. The 8 checks, each +1 if it agrees with the direction, −1 if against, 0 if `n/a`:
   | # | Check (long: first value above the second) |
   |---|---|
   | 1 | 4H price vs EMA21 |
   | 2 | 4H EMA21 vs EMA50 |
   | 3 | 4H price vs EMA200 |
   | 4 | 1D price vs EMA21 |
   | 5 | 1D EMA50 vs EMA200 |
   | 6 | price vs session VWAP |
   | 7 | price vs weekly VWAP |
   | 8 | price vs monthly VWAP |
   For a short, every sign flips.
3. **Score** = sum, from −8 to +8 (EMA part −5..+5, VWAP part −3..+3).
4. **Grade before modifiers:**
   | Score | Grade |
   |---|---|
   | +6 to +8 | **A** |
   | +3 to +5 | **B** |
   | +1 to +2 | **C (weak)** |
   | ≤ 0 | **C (counter-trend)** |
5. Apply grade modifiers from other skills, all of them, in this order: wide stop (`levels-and-entries`) −1, source conflict (F5) −1, high-impact event within 24h (`macro-and-catalysts`) −1, event calendar not checked → capped at B. C is the floor. A C setup can be reported, but it must be labeled "weak" or "counter-trend" with a `[JUDGMENT]` reason.
6. **Dynamic levels:** the tool also lists every EMA and VWAP with its distance in % and ATR. Use them as **confluence** for zone anchors (`levels-and-entries` step 2), e.g. "PDL 308.60 + 4H EMA50 312.50 within 0.5 ATR". They are never targets on their own.

## Formulas
```
EMA_t   = α·close_t + (1 − α)·EMA_(t−1),   α = 2 / (n + 1), seeded with the SMA of the first n closes
TP      = (high + low + close) / 3
VWAP_t  = Σ(TP·volume) / Σ(volume), summed from the anchor (session / week / month start) to bar t
score   = Σ_{i=1..8} sign · cmp(a_i, b_i),   cmp = +1 above, −1 below, 0 if either is n/a
```

**Worked example:** BCH/USDT long, `tools/indicators.py` run 2026-10-04 ~19:50 UTC, price 316.30:
| Check | Value | vs | Long |
|---|---|---|---|
| 4H price vs EMA21 | 316.30 | 314.47 | +1 |
| 4H EMA21 vs EMA50 | 314.47 | 312.50 | +1 |
| 4H price vs EMA200 | 316.30 | 282.78 | +1 |
| 1D price vs EMA21 | 316.30 | 298.08 | +1 |
| 1D EMA50 vs EMA200 | 273.38 | 310.04 | **−1** |
| price vs session VWAP | 316.30 | 317.78 | **−1** |
| price vs weekly VWAP | 316.30 | 311.71 | +1 |
| price vs monthly VWAP | 316.30 | 311.78 | +1 |
EMA +3, VWAP +1 → score **+4 → B**. The daily EMA50 is still under the EMA200 (the longer trend hasn't turned), and price is under today's session VWAP. A long here is a B, not an A, even though the old EMA20/50 + RSI grade said A.

The same run had BTC/USDT long at **+8 → A** (all 8 checks agree), and gold short at **+8 → A**. Gold's VWAP uses XAUT token volume, so the tool flags those 3 checks as lower quality.

## Output format
Inside the report's `Grade:` line:
```
Grade: B, conviction +4 long (EMA +3/5, VWAP +1/3; against: 1D EMA50 < EMA200, price < session VWAP) [CALC → JUDGMENT]
```

## Common failure modes
- **Short history:** EMA200 is `n/a` when the SMA seed still carries more than 10% of the weight (e.g. gold weekly, which starts 2022). An `n/a` check scores 0, so the best possible score drops. Say which checks were `n/a`.
- **Early in the month or week:** the monthly VWAP on day 1–3 is close to the current price, so check 8 flips easily. Mention it when the distance is under 0.25 ATR.
- **Proxy volume:** XAUT VWAP is token-volume weighted. GLD VWAP (real ETF volume) is a cross-check for gold.
- **Weekend / closed markets:** for MSFT, GLD, and SI on a weekend, the "session" VWAP is the last session's. Label it "as of last session".
- Using RSI or MACD to upgrade a grade. They're context only.
- Grading without a direction: the score only means something relative to the setup's side.

## Missing inputs (AGENT.md fallback)
- 15M or 1H fetch failed (F2/F4): the VWAP checks are `n/a` (0). Grade from what's left and say "VWAP unavailable". With all 3 VWAP checks missing, the maximum is +5, so the grade is capped at B.
- 4H or 1D failed: `Grade: n/a [MISSING]`. Never estimate EMAs or VWAP by eye.
- No direction given (F6): report both sides' scores and state the assumption.
- Stale data (F3): grade allowed, but no execution zone.
