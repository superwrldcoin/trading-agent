# Skill: risk-and-sizing

## Purpose
Size a position so the loss at the stop, including fees, equals a set percentage of equity. Check the setup's reward-to-risk and suggest a sensible leverage ceiling. This is guidance only. The user picks the final size.

## Required inputs
- Equity (from the user, `[DATA:user]`)
- Risk % per trade (from `memory/user-preferences.md`; currently TBD)
- Blended entry, stop, targets, scale-out % (`levels-and-entries`)
- Fee rate (`leveraged-position-math` inputs)

## Rules (defaults until the user sets preferences)
| Rule | Default | Status |
|---|---|---|
| Risk per trade | 1% of equity | **default, user TBD** |
| Hard cap per trade | 2% | **default, user TBD** |
| Total open risk, all positions | 5% | **default, user TBD** |
| Minimum weighted R:R | 2.0 | **default, user TBD** |
| T1 at least 0.5R | flag if lower | default |
| Max effective leverage | crypto 5x, metals 10x, equities 2x | **guidance only, user TBD** |

Defaults are shown in the report as `[ASSUMPTION]` until `user-preferences.md` sets them.

## Procedure
1. Get equity and risk %. If either is missing → F1.
2. Per-unit risk = |entry − stop| plus round-trip fees per unit.
3. Units = risk $ / per-unit risk. Notional = units × entry.
4. Weighted R = Σ (scale %ᵢ × Rᵢ) over the targets. Below 2.0 → "fails R:R", and no execution zone is shown.
5. Effective leverage = notional / equity. Compare it to the guidance. Liquidation checks belong to `leveraged-position-math`.
6. Check total open risk against the cap if the user has listed open positions.

## Formulas
```
risk_$      = equity × risk_%
per_unit    = |entry − stop| + entry·fee + stop·fee
units       = risk_$ / per_unit
R_i         = (T_i − entry) / (entry − stop)              (long)
weighted_R  = Σ w_i · R_i
eff_lev     = units · entry / equity
```

**Worked example:** BCH/USDT long, prices from OKX as-of 2026-10-04 16:00 UTC. Equity **$10,000 is an example only** `[ASSUMPTION]`; the fee is 0.05% `[ASSUMPTION]`.
- Risk $ = 10,000 × 1% = **$100**
- Per unit = (309.80 − 293.64) + 309.80 × 0.0005 + 293.64 × 0.0005 = 16.16 + 0.155 + 0.147 = **16.4617**
- Units = 100 / 16.4617 = **6.0747 BCH**. Notional = 6.0747 × 309.80 = **$1,881.94**
- R₁ = (322.60 − 309.80) / 16.16 = **0.79R** (≥ 0.5, OK). R₂ = (366.10 − 309.80) / 16.16 = **3.48R**
- Weighted R = 0.5 × 0.79 + 0.5 × 3.48 = **2.14** → passes the 2.0 minimum
- Effective leverage = 1,881.94 / 10,000 = **0.19x**. No leverage is needed at 1% risk. Any leverage only frees up margin.

Note that without the fee term, units = 100 / 16.16 = 6.188, and the real loss at the stop would be $101.87 (1.02%). That's why fees are in the formula.

## Output format
```
Sizing (equity $10,000 [ASSUMPTION example], risk 1% [ASSUMPTION default])
| Item | Value | Tag |
|---|---|---|
| Risk $ | $100.00 | [CALC] |
| Units / notional | 6.0747 BCH / $1,881.94 | [CALC] |
| Weighted R | 2.14 (min 2.0, passes) | [CALC] |
| Effective leverage | 0.19x (guidance ≤ 5x crypto) | [CALC] |
```

## Common failure modes
- Sizing from the zone top instead of the blended entry, or for the first tranche only.
- Leaving fees out (the stop loses more than planned).
- Treating leverage as a sizing tool. Size comes from the stop distance; leverage only affects margin.
- Ignoring scale-outs when computing R:R (using only the last target).
- Presenting defaults as the user's rules. Until `user-preferences.md` is filled in, they're `[ASSUMPTION]`.

## Missing inputs (AGENT.md fallback)
- No equity (F1): ask once. Without an answer, report R multiples and weighted R only: `Sizing skipped: equity [MISSING]`.
- No risk % preference: use the 1% default, tagged `[ASSUMPTION]`, and suggest the user set it in preferences.
- No fee rate: size without fees, tag `[ASSUMPTION] fees excluded`, and note the stop loss will be slightly bigger.
