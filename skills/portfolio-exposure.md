# Skill: portfolio-exposure

## Purpose
Show what all open positions add up to. Several leveraged positions can be one bet in disguise: BTC and BCH, or silver, gold and GLD. This skill reports net and gross exposure by theme, correlation between held symbols, combined risk to stops, and what a market-wide move would do to the account.

## Required inputs
- Open positions in `memory/positions.json` (private), managed with `python tools/positions.py add / add-option / close / list`
- Account equity: `python tools/positions.py set-equity N`. Without it, no % figures.
- Tool: `python tools/portfolio.py`

## Procedure
1. Run `python tools/portfolio.py`. If there are no positions, say so and stop.
2. **Per position:** price, unrealized P&L, notional (options as **delta-notional** = delta × contracts × multiplier × spot), margin, liquidation (isolated only; cross = `n/a`), and **risk to stop**, measured from the current price. Long options risk their current value. Short options and positions with no stop show "undefined".
3. **By theme** (crypto: BTC, BCH; precious metals: XAU, SI, GLD; equities: MSFT): net notional (longs − shorts) and gross notional, as % of equity.
4. **Correlation:** daily-return ρ over 30 and 90 days for each pair held. |ρ| ≥ 0.7 on either window → "treat as one bet". Size them as one position against the rules' limits.
5. **Combined open risk:** the sum of risk to stops, as % of equity. Compare it with `max_open_risk_pct` in the rules (`rules-check`).
6. **"What if everything moves 5%":** read the "all −5%" and "all +5%" rows from the stress table (`stress-test`).
7. Report flags: correlated pairs, undefined risk, theme gross exposure > 100% of equity, and the worst scenario > 20% of equity.

## Formulas
```
notional_linear  = side × size × price
notional_option  = side × delta × qty × multiplier × spot
theme_net        = Σ notional,  theme_gross = Σ |notional|
risk_to_stop     = max(0, side × (price − stop)) × size
open_risk_pct    = Σ risk_to_stop / equity × 100
```

**Worked example** (live prices 2026-10-04, **sample positions** for illustration, equity $10,000):
| Position | Notional | Risk to stop |
|---|---|---|
| BTC long 0.2 @ 85,000, 20x, no stop | 17,210 | undefined |
| BCH long 20 @ 310, 10x, stop 298 | 6,336 | 376 |
| SI short 100 @ 61, 5x, stop 62.90 | −6,042 | 249 |
| MSFT 2 × 520 call @ 12.50 | 54,278 (delta) | 4,197 (premium value at risk) |
- Crypto net 23,546 = **235% of equity**; equities (option delta) 543%; metals −60%
- BTC/SI ρ 0.51 (30d) and BCH/BTC 0.56 (90d): positive but under 0.7, so not flagged as one bet in this sample
- Open risk to stops **$4,822 = 48% of equity**, almost all of it the option premium
- Flags: BTC has no stop (undefined risk); crypto and equities gross exposure > 100%

## Output format
A `Portfolio` section: the position table, theme table, correlation table, open risk line, and flags. Then the stress table (`stress-test`).

## Common failure modes
- Adding up notional for options as if they were stock. Use delta-notional, and say the max loss of a long option is its premium.
- Ignoring correlation: two 1% risks on ρ 0.9 assets are closer to one 2% risk.
- Stale positions: the file is only as good as your updates. Always print the "opened" dates and ask whether anything was closed.
- Cross margin: liquidation depends on the whole account. The tool shows `n/a`. Say that the equity number is what's at risk.

## Missing inputs (AGENT.md fallback)
- No positions file: "No open positions recorded." Offer the `positions.py add` command.
- No equity (F1): skip the % figures and say so.
- Price fetch failed for a symbol (F2): `FETCH FAILED`. Don't value that position with a remembered price.
