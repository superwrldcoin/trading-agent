# Skill: stress-test

## Purpose
Move every open position at once by −3%, −5%, −10% (and +3/+5/+10%) and by realistic gaps. Show which positions liquidate, which stops fill and where, and what the account looks like afterwards. Stops don't protect against gaps: gold and silver gap over the weekend, equities overnight, and crypto in flash moves.

## Required inputs
- Open positions and equity (`portfolio-exposure`)
- Tool: `python tools/portfolio.py [--shocks -3 -5 -10 3 5 10]`. The stress table is part of its output.

## Procedure
1. Run the tool and read the **Scenario** table.
2. **Orderly mode:** stops fill at the stop price. An isolated position whose liquidation price is crossed before its stop (or that has no stop) loses its whole margin.
3. **Gap mode:** the market opens at the shocked price, so stops fill **at the shocked price**, worse than the stop. Liquidation still caps an isolated position's loss at its margin. Cross-margin positions have no cap. They draw on the account.
4. **Historical gap scenario:** for gap-prone markets (metals, equities, CME), each position is moved against itself by its **95th-percentile opening gap** over the last ~252 sessions. Crypto trades 24/7, so it isn't gapped here. Use gap mode for crypto flash moves.
5. Options are revalued with Black-Scholes at the shocked spot, the same IV, and no time decay. Note that IV usually **rises** in sell-offs, which helps long options and hurts short ones beyond what's shown.
6. Report: the worst scenario and its % of equity, every `LIQUIDATED` position, and the gap between orderly and gap results for the same shock (that gap is your slippage risk).
7. A worst-case loss > 20% of equity is a FAIL flag. Pair it with `rules-check` (`max_open_risk_pct`) and suggest cutting leverage or adding stops on the positions that liquidate.

## Formulas
```
P&L_linear(S') = side × (fill − entry) × size,  fill = S' (no stop hit) | stop (orderly) | S' (gap)
liquidated     → P&L = −margin (isolated)
P&L_option(S') = side × (BS(S', K, T, r, IV) − premium) × qty × multiplier
gap_95         = 95th percentile of max(0, −(open_t / close_{t−1} − 1))   (long; short mirrored)
```

**Worked example** (same sample portfolio as `portfolio-exposure`, live prices 2026-10-04):
| Scenario | Mode | Total P&L | Account | Events |
|---|---|---|---|---|
| all −10% | orderly | −2,166 | 7,834 | BTC **liquidated**, BCH stopped |
| all −10% | gap | −2,424 | 7,576 | BTC liquidated, BCH stopped (gap fill) |
| all −5% | either | −971 | 9,029 | — |
| historical 95th-pct gap | gap | +914 | 10,914 | SI stopped (gap fill); SI gap 4.56%, MSFT 1.81% |
- The BTC 20x long with no stop (liquidation 81,175) is the position that turns a −10% day into a margin wipe-out.
- The $258 between orderly and gap on −10% is the slippage BCH's stop takes when price gaps through it.
- Worst case −24% of equity → FAIL flag.

## Output format
The scenario table, then one line each for the worst case, liquidations, and slippage, then flags.

## Common failure modes
- Treating stops as guaranteed. Only orderly mode assumes that.
- Shocking only the position under analysis. The point is everything at once, because correlated assets fall together.
- Forgetting the weekend: a Friday-evening metals or equity position faces the gap scenario. Crypto faces flash moves at any hour.
- Reading the historical gap as a maximum. It's the 95th percentile, and 1 session in 20 gaps more.

## Missing inputs (AGENT.md fallback)
- No positions: there's nothing to stress. To stress a planned trade, add it temporarily with `positions.py add` and close it after.
- No equity (F1): show P&L only, with no account line or %.
- Fewer than 30 sessions of history: historical gap `n/a` (F4).
