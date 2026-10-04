# User Preferences

Only preferences the user has stated directly. Do not infer. Fields marked **unknown** mean ask.

## Watchlist (added: 2026-10-04)
- XAU/USD (spot gold)
- MSFT (Microsoft)
- SI (silver)
- BCH/USDT (Bitcoin Cash)
- BTC/USDT (Bitcoin) (added: 2026-10-04)
- GLD (SPDR Gold ETF) (added: 2026-10-04). Relationship to the XAU/USD position (instead of or alongside): **unknown**

## Typical leverage
- **unknown**. Not stated yet. Also unknown: equity, risk % per trade, exchange/broker, fees, margin mode (isolated or cross), instrument type per asset (spot, futures, ETF, CFD). Until these are known, sizing and leverage checks are skipped (AGENT.md F1).
- **Trades options:** yes (stated 2026-10-04). Which underlyings and strategies: **unknown**. (added: 2026-10-04)
- Max leverage, risk limits, cooldowns: set them in the **Trading rules** block at the end of this file. They're checked on every plan.

## Timeframe (added: 2026-10-04)
- Primary: **4H**. Structure, zones, and invalidation are judged on 4H candle closes.
- Tools cover **15M, 1H, 4H, 1D, 1W** (the user's choice over a 5M set). 5M is used only by `tools/verify.py`. (added: 2026-10-04)

## Analysis method (added: 2026-10-04)
- Structure is read off higher-timeframe liquidity reference levels:
  - PDH / PDL: previous day high / low
  - PWH / PWL: previous week high / low
  - PMthH / PMthL: previous month high / low
- Look for sweeps and retests of those levels, demand/supply shelves, and range or base structure.
- Invalidation = a clean 4H close beyond the structural level, not a wick.

## Report format (added: 2026-10-04)
- The **4H Execution Matrix** is the core of each instrument section, laid out as a **milestone table**: every level sorted by price, with distance, R multiple, scale %, reference level, and a tag. The full layout is in `skills/report-format.md`.
- Matrix fields:
  ```
  <INSTRUMENT> (<name>)
  Market Structure: <one-line read of 4H structure>
  Current Price: <fetched price, with as-of time>
  Execution Zone: <low> - <high> (<which reference level / confluence>)
  Structural Invalidation (Stop): <level> (<4H close condition>)
  Target 1 (De-Risk NN%): <level> (<reference level>)
  Target 2 (Expansion NN%): <level> (<reference level>)
  Target 3 (Runner NN%): <level> (<reference level>)   # optional
  ```
- Tag every number and claim: `[DATA]`, `[CALC]`, `[JUDGMENT]`.
- Disclaimer **once**, at the end of the report.
- Scale-out split used so far: 40 / 40 / 20 for three targets, 50 / 50 for two.
- Every level must tie to a named reference (PDH, PWL, etc.) computed from fetched data.

## How the user wants things done (stated corrections and choices)
- The agent is **analysis-only**: it never places trades and never requests or stores API keys or seed phrases. (added: 2026-10-04)
- **The user applies MEMORY_UPDATE blocks after review.** The agent proposes; it doesn't apply them. (added: 2026-10-04)
- The milestone-table/matrix format **replaced** the earlier observations/scenarios report format (user chose "option 1"). (added: 2026-10-04)
- **Substitute data sources are OK** when the exact market isn't available, as long as the substitution is disclosed (e.g. XAUT for spot gold, SI=F for silver). (added: 2026-10-04)
- Asked for 4H findings to be **verified against 15M and 5M bars** (done via `tools/verify.py` in the 2026-10-04 session). Whether this should run every session: **unknown**. (added: 2026-10-04)
- When inputs are missing, follow the **fallback rules** in AGENT.md (F1–F6) instead of guessing. (added: 2026-10-04)
- News searches are allowed, but each one asks the user first (settings). (added: 2026-10-04)
- The repo is **public** (github.com/superwrldcoin/trading-agent). User-supplied chart snapshots are not committed, and `trades.md` and `sessions.md` are gitignored. (added: 2026-10-04)
- **P(T1 before stop)**: the user wants a judgment probability estimate per setup, tagged `[JUDGMENT]`, logged, and checked for calibration in post-mortems (chosen over dropping the field). (added: 2026-10-04)
- **Conviction = EMA + VWAP** (revised 2026-10-04: the user asked to "focus on EMA and VWAP as indicators" for trade conviction). The A/B/C grade comes from 8 checks: 4H price/EMA21, EMA21/EMA50, price/EMA200; 1D price/EMA21, EMA50/EMA200; price vs session, weekly, and monthly VWAP. RSI, MACD, and the 5-TF alignment are context only. This replaces the earlier EMA20/50 + RSI grade. (added: 2026-10-04; revised: 2026-10-04)
- Session logs go to `memory/sessions.md` via `tools/log_entry.py`, not to `journal.md`, so the journal stays lessons-only. (added: 2026-10-04)

## Not yet specified
- Report length / detail beyond the matrix: **unknown**
- (Risk per trade and minimum R:R are now set in the Trading rules below.)

## Trading rules
<!-- rules -->
`tools/rules_check.py` checks every plan against this block before analysis. `null` = not set. Percentages are of account equity.
**Set by the agent on 2026-10-04 at the user's request** ("you set the rules as well"); the user has authorized the agent to adjust them when needed. Every change is noted in the change log below with its reason.
```json
{
  "max_leverage": {"crypto": 10, "precious metals": 7, "equities": 2},
  "max_risk_pct_per_trade": 1.0,
  "max_open_risk_pct": 5.0,
  "max_theme_gross_pct": 300,
  "max_positions": 5,
  "min_weighted_r": 2.0,
  "require_stop": true,
  "max_liq_touch_5d_pct": 5,
  "max_option_premium_pct": 2.0,
  "loss_cooldown_hours": 12,
  "max_trades_per_day": 3
}
```
Why these values (evidence from the tools, 2026-10-04):
- **Leverage, crypto 10x:** `vol_check.py` puts BTC's 5-day ≤ 5% liquidation-touch leverage at 9.1x. BCH runs about 2× BTC's volatility, so `max_liq_touch_5d_pct` catches it at a lower leverage on its own. **Metals 7x:** silver's equivalent was 7.2x. **Equities 2x:** a standard margin account limit.
- **Risk 1% per trade, 5% open:** the risk-and-sizing skill defaults. Correlated positions (ρ ≥ 0.7) count as one bet toward the 5%.
- **Theme gross 300%:** allows leveraged positions but caps one theme at 3× equity, so a 10% theme-wide drop costs at most about 30% before stops.
- **5 positions, 3 trades/day, 12h cooldown after a loss:** limits overtrading and revenge trading after a stop-out.
- **Min weighted R 2.0, stop required:** the skills' existing rules, now enforced.
- **Liquidation-touch ≤ 5% within 5 days:** the same threshold the leverage-volatility gate uses for its ceiling.
- **Options premium ≤ 2% per trade:** a long option can go to zero, so the premium is the risk, and it's held to about 2× the linear-trade risk because options can't be stopped cleanly.

Change log:
- 2026-10-04: initial values set by the agent (user delegated).
