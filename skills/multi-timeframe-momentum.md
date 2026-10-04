# Skill: multi-timeframe-momentum

## Purpose
Decide whether momentum on the daily (bias) and 4H (setup) timeframes agrees with the direction of a proposed setup, and grade it A, B, or C.

## Required inputs
- 4H OHLC from `tools/levels.py` (CSV in `tools/output/`)
- Daily closes, built from the 4H bars by session date
- Trade direction (long or short), taken from the setup in `levels-and-entries`

## Indicators (fixed, nothing else)
| Indicator | Settings | Used on |
|---|---|---|
| EMA | 20 and 50, on close | 4H (stack); daily (EMA20 only, see failure modes) |
| RSI | 14, Wilder smoothing | 4H |

## Procedure
1. Compute 4H EMA20, EMA50, and RSI14, plus daily EMA20.
2. Score each timeframe for the trade direction:
   - **4H aligned (long):** close > EMA20 > EMA50 **and** RSI14 > 50. For short, all reversed.
   - **4H mixed:** the EMA stack and RSI disagree.
   - **4H opposed:** the stack and RSI both point the other way.
   - **Daily aligned (long):** daily close > daily EMA20. Reverse for short.
3. Assign the grade:
   | Grade | Rule |
   |---|---|
   | **A** | 4H aligned **and** daily aligned **and** RSI not stretched (long 50–70, short 30–50) |
   | **B** | Both aligned but RSI stretched, **or** 4H aligned with daily opposed, **or** 4H mixed with daily aligned |
   | **C** | 4H opposed, **or** 4H mixed with daily opposed (a counter-trend setup) |
4. Apply grade modifiers from other skills, all of them, in this order: wide stop (`levels-and-entries`) −1, source conflict (F5) −1, high-impact event within 24h (`macro-and-catalysts`) −1, event calendar not checked → capped at B. C is the floor. A C-grade setup can be reported, but it must be labeled "counter-trend" with a `[JUDGMENT]` reason.

## Formulas
```
EMA_t = α·close_t + (1 − α)·EMA_(t−1),   α = 2 / (n + 1)          (EMA20: α = 0.0952)
RSI   = 100 − 100 / (1 + RS),   RS = avg_gain_14 / avg_loss_14     (Wilder: avg_t = (13·avg_(t−1) + x_t) / 14)
```

**Worked examples** (`python tools/indicators.py`, 120 days, run 2026-10-04; last bars 2026-10-02 for gold/MSFT, 2026-10-04 16:00 UTC for BCH):

| | XAU/USD (long idea) | MSFT (long) | BCH/USDT (long) |
|---|---|---|---|
| 4H close | 4,143.10 | 517.86 | 316.40 |
| 4H EMA20 / EMA50 | 4,171.65 / 4,210.65 | 508.84 / 500.46 | 314.55 / 312.50 |
| 4H RSI14 | 38.7 | 61.6 | 53.4 |
| Daily EMA20 | 4,267.62 | 503.44 | 299.36 |
| 4H | **opposed** (close < 20 < 50, RSI < 50) | aligned | aligned |
| Daily | opposed (4,143 < 4,268) | aligned | aligned |
| Grade | **C**: counter-trend long | **A** | **A** (then −1 for wide stop → B) |

Gold's short grade is **A** (the tool grades both sides). Gold RSI check: avg gain 4.846, avg loss 7.692 → RS = 0.630 → RSI = 100 − 100 / 1.630 = **38.7**. The user's 4H matrix had gold as a long from the 4,120–4,135 zone, which this skill grades C. The report must say so instead of quietly agreeing.

## Output format
One line per instrument, inside the report's `Grade:` line:
```
Grade: C, counter-trend long: 4H opposed (4,143.10 < EMA20 4,171.65 < EMA50 4,210.76, RSI 38.7), daily opposed [CALC → JUDGMENT]
```

## Common failure modes
- **EMA warm-up:** an EMA needs about 3× its period in bars to settle. `tools/indicators.py` fetches 120 days by default and prints a `Warm-up:` warning below 150 4H bars or 60 sessions. MSFT has only 2 RTH bars a day, so 120 days gives just 164 bars, barely above the threshold. Use `--days 200` if you shorten the default. A short history really does move values: with 40 days, MSFT's EMA50 read 503.14 instead of 500.46.
- Grading without a direction: the grade only means something relative to the setup's side.
- Building daily bars from UTC days for gold, silver, or MSFT. Use the session dates the levels tool uses.
- Reading XAUT weekend bars as momentum. The tool already removes them; don't add them back.
- Adding extra indicators (MACD, Stochastic) without updating this skill. The point is a fixed, repeatable rule.

## Missing inputs (AGENT.md fallback)
- Fewer than 50 bars on 4H (F4): 4H stack `n/a`. Grade from RSI and daily only, capped at B.
- No direction given (F6): grade both sides and state the assumption.
- Stale data (F3): grade allowed, but no execution zone.
- `tools/indicators.py` failed for a symbol (F2): `Grade: n/a: indicators not computed [MISSING]`. Never estimate RSI or EMA by eye.
