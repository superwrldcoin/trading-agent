# Skill: leverage-volatility-check

## Purpose
Check whether the liquidation price (and the stop) sits safely outside the asset's normal movement. Leverage is set by volatility, not conviction: at 40x, liquidation is about 2% from entry (2.5% minus maintenance margin), and BTC or silver move that far in a normal day. This gate runs on **every leveraged setup**, right after `position_calc.py`.

## Required inputs
- Symbol, side, leverage, entry (default: current price), stop (optional), maintenance margin (default 0.5% `[ASSUMPTION]`)
- Tool: `python tools/vol_check.py <SYMBOL> <long|short> --leverage L [--entry E] [--stop S] [--mmr M]`. `quick_check.py` runs it automatically whenever leverage > 1.

## Procedure
1. Run the tool. Read the liquidation distance in %, in 4H ATR, in daily ATR, and as a multiple of the 30-day average daily range.
2. Read the **touch table**: the share of days in the last ~500 daily bars on which price moved at least the liquidation (and stop) distance against the position within 1, 3, 5, and 10 days, using daily highs/lows from each day's close.
3. Apply the gates:
   | Condition | Result |
   |---|---|
   | Stop beyond liquidation | **FAIL**: liquidated before the stop can fill |
   | Liquidation < 1 daily ATR away | **FAIL**: inside one normal day's move |
   | 1-day liquidation-touch frequency > 5% | **FAIL** |
   | Liquidation < 2 daily ATR away | WARN |
   | 5-day frequency > 10% | WARN |
   | Stop–liquidation gap < 1 × 4H ATR | WARN: a fast move or gap can skip the stop |
   | Leverage above the "≤ 5% 5-day odds" leverage | WARN, and state that leverage |
4. Report the **leverage that kept 5-day liquidation-touch odds ≤ 5%** as the volatility-based ceiling. If `user-preferences.md` sets a lower max leverage (rules-check), the lower one wins.
5. A FAIL here makes the setup "Fails as specified" whatever the grade. Conviction doesn't override volatility.

## Formulas
```
liq_dist        = 1/L − MMR                                 (isolated, linear; long and short)
adverse_N(t)    = (close_t − min(low_{t+1..t+N})) / close_t  (long; short uses max(high))
P(touch within N) = share of days t in the lookback with adverse_N(t) ≥ distance
L_safe          = 1 / (q_95(adverse_5) + MMR)               (q_95 = 95th percentile of 5-day adverse moves)
```

**Worked example** (`tools/vol_check.py`, 2026-10-04, last 500 daily bars):
- BTC/USDT long 40x from 85,969.70: liquidation 84,250.31, **2.00%** away = 2.81 × 4H ATR (610.86) = **0.80 × daily ATR** (2,149.51). Touched within 1d / 3d / 5d / 10d on **31% / 52% / 64% / 73%** of days. L_safe = **9.1x**. Verdict: FAIL (inside one day's move; 31% one-day odds).
- SI long 40x from 60.42 with a 59.80 stop: liquidation 59.21 (2.00% = 0.78 daily ATR), touch odds 25% / 47% / 56% / 65%; L_safe = **7.2x**. The stop at 1.02% gets touched within 1 day on 40% of days: a tight stop on silver gets hit by normal noise.

## Output format
In the report, after the milestone table:
```
Leverage vs volatility: liquidation 2.00% away = 0.80 daily ATR; historical touch odds 1d/3d/5d/10d 31%/52%/64%/73% (500 days, not a forecast); 5-day <= 5% leverage: 9.1x [CALC]
```

## Common failure modes
- Treating the frequencies as forecasts. They describe the past ~2 years. In a volatility spike they understate risk. Say "historical frequency".
- Measuring from the current price when the entry is elsewhere. Distances are from **entry**.
- Ignoring gaps: for gold, silver, and equities the daily bars don't show weekend or overnight gaps inside the lookback statistics as fills. See `stress-test`.
- Cross margin: liquidation depends on the whole account. The tool assumes isolated. With cross margin, use `portfolio-exposure` with your equity.
- Exchange-specific MMR tiers: big positions have higher MMR, so liquidation is closer. Pass `--mmr` from your exchange.

## Missing inputs (AGENT.md fallback)
- No leverage given: skip this check (1x has no liquidation).
- Fewer than 30 daily bars: frequencies `n/a` (F4). Report the ATR multiples only.
- Daily fetch failed (F2): `Leverage vs volatility: n/a [MISSING]`, and the setup can't be graded above C if leverage > 5x.
