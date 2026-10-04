# Skill: trade-plan

## Purpose
Turn a setup into an executable plan: staged entries with sizes, the average entry and liquidation **after each add**, a partial take-profit ladder, and breakeven and trailing-stop rules. It answers "if I add at zone 2, what's my new average, liquidation and R:R?" before you add, not after.

## Required inputs
- Side, stop, targets (from `levels-and-entries`); leverage, MMR, fee (from the user or `[ASSUMPTION]`)
- Entries: either a zone (30/30/40 pyramid, largest piece at the best price) plus a total size, or explicit `PRICE:SIZE` stages
- Size: given, or sized from `--equity` and `--risk-pct` so a full fill stopped out loses exactly that % (fees included)
- Optional: 4H ATR with `--trail-atr K`, `--breakeven-after N` (default: after T1), `--addon PRICE:SIZE` (an add in profit, after T1)
- Tool: `python tools/trade_plan.py ...`

## Procedure
1. Run the tool.
2. **Stage table:** each row is the position *after* that fill: cumulative size, average entry, margin, liquidation, risk to stop ($ and % of equity), R to each target from the new average, and weighted R. Risk is planned as if every stage fills.
3. **Pyramid trap check:** if a later stage sits beyond the liquidation price of the earlier fills, the position is liquidated before that stage can fill (FAIL). Fix it by lowering leverage or tightening the zone.
4. **Liquidation vs stop:** after every stage, liquidation must stay beyond the stop (FAIL otherwise). Then run `leverage-volatility-check` on the final average entry.
5. **Ladder:** at each target the planned % is closed and P&L is realized. After T1 the stop moves to the average entry (breakeven), so the remaining position risks nothing but fees. From T2, with `--trail-atr`, the stop trails K × ATR behind the last target hit, locking in profit.
6. **Add-on (optional, in profit only):** shown after T1 with the stop at breakeven. FAIL if the add sits at or beyond the protective stop. WARN if it's more than 50% of the original size, if it chases past T2, or if the combined position risks more at its stop than the original plan did.
7. Put the stage table and ladder in the report under the milestone table.

## Formulas
```
avg_k        = Σ_{i≤k} price_i·size_i / Σ_{i≤k} size_i
liq_k        = avg_k × (1 − 1/L + MMR)        (long; short: × (1 + 1/L − MMR)), isolated, same L on every add
risk_k       = cum_size_k × |avg_k − stop| + fees
R_target(k)  = side × (target − avg_k) / |avg_k − stop|
trail_stop   = last_target − side × K × ATR
```

**Worked example:** BCH/USDT long, zone 296.30–298.96, stop 293.64, targets 308.60 / 322.60 / 366.10 (40/40/20), 10x, equity $10,000, risk 1%:
| After stage | Fill | Cum size | Avg | Liquidation | Risk | Weighted R |
|---|---|---|---|---|---|---|
| 1 | 298.96 | 7.22 | 298.96 | 270.56 | $40.57 (0.41%) | 5.03 |
| 2 | 297.63 | 14.45 | 298.30 | 269.96 | $71.54 (0.72%) | 5.89 |
| 3 | 296.30 | 24.08 | 297.50 | 269.23 | $100.00 (1.00%) | 7.31 |
- **Adding at zone 2** moves the average to 298.30 and liquidation to 269.96. Weighted R rises from 5.03 to 5.89.
- Ladder (trail 1.5 × ATR 5.32): T1 realizes +$101.88 and moves the stop to 297.50 (0 risk). T2 realizes +$342.14 and trails the stop to 314.62, locking in $82.47. T3 brings the total to +$671.67.
- Add-on of 10 at 309 after T1: average 302.20, liquidation 273.49, position risk at the breakeven stop $115.03, more than the original $100 (WARN).
- Pyramid trap example: 20x long 100 then 93 with stop 90. After stage 1, liquidation is 95.50, so the 93 add can never fill (FAIL).

## Output format
`Trade plan:` the stage table, the ladder, the add-on line (if any), and flags.

## Common failure modes
- Sizing for the first stage only. Risk must assume every stage fills.
- Adding to a loser. The add-on is only modeled after T1 with the stop at breakeven, and adding at or beyond the stop is a FAIL.
- Forgetting that every add moves liquidation toward price (longs) when leverage stays the same.
- Cross margin: the per-stage liquidation shown is isolated. With cross margin, use `portfolio-exposure` with equity.
- Trailing too tight: a K × ATR trail with K < 1 gets stopped by normal noise (see `leverage-volatility-check`).

## Missing inputs (AGENT.md fallback)
- No size, equity or risk %: ask (F1). Without an answer, run with size 1 and say the dollar figures are per unit.
- No leverage: 1x, so no liquidation column.
- No ATR: skip trailing and use breakeven only.
