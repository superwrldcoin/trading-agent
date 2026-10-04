# Skill: leveraged-position-math

## Purpose
Turn a plan (entry, stop, targets, size) into leveraged-position numbers: margin vs notional, liquidation price, P&L per target net of fees, and the most leverage the plan can carry while keeping liquidation safely beyond the stop. This is math only. It never chooses size (`risk-and-sizing` does that) and never places orders.

## Required inputs
- Side, blended entry, stop, targets with scale-out % (`levels-and-entries`)
- Position size in units (`risk-and-sizing`)
- Leverage the user intends to use, and margin mode (isolated or cross)
- Exchange parameters: maintenance margin rate (MMR), taker/maker fee, funding rate. Take them from the user or the exchange docs and tag `[DATA:user]`. Never assume them silently.
- ATR(14) on 4H

## Procedure
1. Confirm the inputs. Any missing exchange parameter → F1 (ask). If the user wants a rough figure anyway, use the placeholder and tag it `[ASSUMPTION]`.
2. Notional = units × entry. Margin = notional / leverage. Effective leverage = notional / equity.
3. Liquidation price (isolated, linear USDT contract) from the formula below.
4. Check the liquidation buffer: the distance between stop and liquidation must be ≥ 1 ATR. If not, the plan fails. Report the max leverage that would pass.
5. P&L per target: gross, minus the exit fee and that tranche's share of the entry fee.
6. P&L at the stop, net of fees. It should equal the planned risk $ (sizing already includes fees).
7. Funding: estimated cost per 8h interval at the stated rate.

## Formulas
```
notional        = units × entry
margin          = notional / L
effective_lev   = notional / equity
liq_long        ≈ entry × (1 − 1/L + MMR)
liq_short       ≈ entry × (1 + 1/L − MMR)
L_max (long)    = 1 / (1 + MMR − (stop − k·ATR) / entry)        # liq at least k ATR below stop, k = 1
pnl_target_i    = w_i·units × (T_i − entry) − w_i·units·T_i·fee − w_i·entry_fee
entry_fee       = notional × fee
funding_per_8h  = notional × funding_rate
```
These are isolated-margin approximations. Exchanges also account for fees, mark price, and tiered MMR. For cross margin, liquidation depends on the whole account balance, so report "cross: depends on account balance, not computed" unless the user gives the balance.

**Worked example:** BCH/USDT long. Prices are from OKX data as-of 2026-10-04 16:00 UTC. Exchange parameters are placeholders `[ASSUMPTION]`: 10x isolated, MMR 0.5%, taker fee 0.05%, funding 0.01%/8h. Size from `risk-and-sizing` is 6.0747 BCH (1% of a $10,000 example account).
- Entry 309.80, stop 293.64, T1 322.60 (50%), T2 366.10 (50%), ATR 5.32
- Notional = 6.0747 × 309.80 = **$1,881.94**. Margin = 1,881.94 / 10 = **$188.19**. Effective leverage = 1,881.94 / 10,000 = **0.19x**
- Liquidation = 309.80 × (1 − 0.10 + 0.005) = 309.80 × 0.905 = **280.37**
- Buffer = 293.64 − 280.37 = 13.27 = **2.49 ATR** ≥ 1 ATR → passes
- L_max = 1 / (1.005 − (293.64 − 5.32) / 309.80) = 1 / (1.005 − 0.93066) = **13.45x**
- Entry fee = 1,881.94 × 0.0005 = $0.94 (0.47 per half)
- T1: 3.0374 × (322.60 − 309.80) = $38.88 gross − 0.49 exit fee − 0.47 = **+$37.92**
- T2: 3.0374 × (366.10 − 309.80) = $171.00 gross − 0.56 − 0.47 = **+$169.98**
- Stop: 6.0747 × (293.64 − 309.80) = −$98.17 − 0.89 exit fee − 0.94 entry fee = **−$100.00** (= planned 1% risk ✓)
- Funding ≈ 1,881.94 × 0.0001 = **$0.19 per 8h**
- At the stop, the loss on margin is −53.1%. That number looks dramatic but means nothing: the dollar loss is the same $100 at 2x or 10x. **Leverage changes margin and liquidation, not the loss at the stop.**

## Output format
```
Leverage check: BCH/USDT long, 10x isolated [DATA:user]
| Item | Value | Tag |
|---|---|---|
| Notional / margin | $1,881.94 / $188.19 | [CALC] |
| Liquidation (≈) | 280.37 (stop 293.64, buffer 2.49 ATR) | [CALC] |
| Max leverage (liq ≥ 1 ATR beyond stop) | 13.45x | [CALC] |
| Net P&L at T1 / T2 / stop | +$37.92 / +$169.98 / −$100.00 | [CALC] |
| Funding per 8h | $0.19 | [CALC] |
```

## Common failure modes
- Liquidation price above the stop on a long (or below it on a short): the position gets liquidated before the stop can trigger. This is always a hard fail.
- Mixing up notional and margin, e.g. quoting P&L as % of margin and calling it % of the account.
- Leaving out fees, so the stop loss ends up larger than the planned risk.
- Using the formula for an inverse (coin-margined) contract on a linear one, or the reverse.
- Using a cross-margin liquidation price without the account balance.
- Treating funding as fixed. It changes every interval; label it an estimate.

## Missing inputs (AGENT.md fallback)
- No leverage, MMR, or fee (F1): ask once. Without an answer, show only notional and P&L before fees, and add `Leverage check skipped: <items> [MISSING]`.
- No position size (F1): needs equity first; see `risk-and-sizing`.
- Cross margin without balance: liquidation `n/a` (F4). Never estimate it.
