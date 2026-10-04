# Skill: options-greeks

## Purpose
Analyze options on the watchlist: read the chain (IV, expected move, skew, liquidity), and break a position (one or more legs) down into greeks, breakevens, max profit and loss, a model probability of profit, and P&L by spot move and time. The user trades options (stated 2026-10-04).

## Required inputs
- Underlying (supported): MSFT, GLD (yfinance); BTC (Deribit, premiums converted from BTC to USD, 1 contract = 1 BTC); gold (XAU/USD) uses **GLD options as a proxy**; silver (SI) uses **SLV options as a proxy**. BCH has no listed options.
- For a position: each leg as `long|short call|put STRIKE YYYY-MM-DD @PREMIUM xQTY [iv=0.30]`
- Tools: `python tools/options.py chain <SYM> [--dte N | --expiry D]` and `python tools/options.py analyze --symbol <SYM> --leg "..." [--leg "..."]`. For open option positions, use `positions.py add-option`, then `portfolio.py` covers delta exposure and stress.

## Procedure
1. **Chain first:** run `chain` for the expiry nearest the intended holding period. Read:
   - **ATM IV** and the **expected move** by expiry (± S × IV × √T, about a 68% range under the model). Compare it with the setup's targets: a target beyond the 1-sd move needs a reason.
   - **25-delta skew** (put IV − call IV): positive = downside protection is priced richer (fear), negative = calls are richer.
   - **Liquidity:** bid/ask width and open interest. A spread wider than ~10% of mid makes the greeks unreliable for fills.
2. **Position:** run `analyze` with the legs. If no `iv=` is given, IV is implied from the premium at today's spot.
3. Report the **net greeks**: delta (and its dollar equivalent), gamma, theta per day, vega per vol point. Then breakevens at expiry, max profit and max loss (**UNLIMITED** for naked shorts), and the model probability of profit.
4. **P&L grid:** spot −10%…+10% across today, +7 days, mid-life, and expiry, at the same IV. Read off where time decay hurts, and how much a move is needed just to break even by a given date.
5. **Ties to the rest of the agent:**
   - Direction and conviction still come from `multi-timeframe-momentum` and `market-structure` on the underlying.
   - The expected move vs targets from `levels-and-entries`: a long option needs the move to arrive before theta eats the premium.
   - `rules-check`: `max_option_premium_pct` (premium paid as % of equity).
   - `portfolio-exposure`: options count as delta-notional. A long option's max loss is its premium; short options are undefined risk.
   - `macro-and-catalysts`: an event before expiry (e.g. MSFT earnings) means **IV crush** after it. A long option held through the event can lose even if direction is right.
6. Flags: unlimited loss; theta > 10% of premium per week; spread wider than 10% of mid; target beyond the 1-sd expected move; proxy underlying.

## Formulas
```
d1 = [ln(S/K) + (r + σ²/2)T] / (σ√T),  d2 = d1 − σ√T
call = S·N(d1) − K·e^(−rT)·N(d2),  put = K·e^(−rT)·N(−d2) − S·N(−d1)
delta_c = N(d1), delta_p = N(d1) − 1,  gamma = φ(d1) / (S·σ·√T),  vega = S·φ(d1)·√T / 100 (per vol pt)
theta_c = [−S·φ(d1)·σ / (2√T) − r·K·e^(−rT)·N(d2)] / 365 (per day)
P(ITM) = N(d2) (call), N(−d2) (put)     expected move (1 sd) = S·σ·√T
```
r = 4% `[ASSUMPTION]`, no dividends. US equity/ETF options are American, so early exercise isn't modeled (deep ITM puts slightly undervalued).

**Worked examples** (live, 2026-10-04):
- **MSFT chain**, 2026-11-20 (47 DTE): spot 517.53, ATM IV 31.9%, expected move **±59.13 (11.4%)**, 25-delta skew −0.1 vol pts (flat). The 520 call is 22.70/23.65, delta 0.525, theta −0.29/day.
- **BTC chain** (Deribit), 2026-10-30 (26 DTE): underlying 86,158, ATM IV 33.8%, expected move **±7,760 (9.0%)**.
- **MSFT 520/560 call spread ×2** (long 520 @ 23.20, short 560 @ 9.00): net debit **$2,840**, breakeven **534.20** at expiry (= 520 + 14.20), max profit **$5,160** (= (40 − 14.20) × 200), max loss $2,840, net delta 50.6 (≈ $26k of stock), theta −$11.27/day, model probability of profit **39%**. If MSFT doesn't move, the spread loses $404 by mid-life and $2,404 by expiry: the move has to come.

## Output format
An `Options` section: the chain summary line (IV, expected move, skew), the leg table, the net greeks line, the expiry line (breakeven, max profit/loss), the P&L grid, and flags.

## Common failure modes
- Treating the model probability of profit as a forecast. It's risk-neutral and assumes constant IV.
- Ignoring IV crush around earnings and events.
- Quoting greeks per share when the position is in contracts: multiply by 100 for US options. BTC Deribit contracts are 1 BTC.
- Using GLD or SLV option IV as if it were spot gold or COMEX silver IV. They're proxies, so label them.
- Short options without a plan: unlimited or large loss, so they need a stop rule and a margin check.

## Missing inputs (AGENT.md fallback)
- No expiry or strike: show the chain for ~30 DTE and ask which strike (F1). Headless: analyze the ATM option and state the assumption.
- Premium outside the model range: ask for IV, or pass `iv=` (F1).
- Chain fetch failed (F2): `Options data unavailable [MISSING]`. Never estimate IV.
