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
- Risk tolerance / max position size: **unknown**
- Minimum reward:risk to show a setup: **unknown** (skills use a 2.0 default, tagged `[ASSUMPTION]`)
- Report length / detail beyond the matrix: **unknown**
