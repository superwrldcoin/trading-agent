# Core

Stable facts about this agent and setup. Changes only via MEMORY_UPDATE (see memory-protocol.md).

- Agent is analysis-only; the user executes all trades. (added: 2026-10-04)
- Analysis outputs are saved to `tools/output/`. (added: 2026-10-04)
- All timestamps stored in UTC. (added: 2026-10-04)

## Data sources (added: 2026-10-04)
All keyless. Run `python tools/levels.py [SYMBOL ...]` to fetch 4H candles and reference levels.

| Symbol | Source | Notes |
|---|---|---|
| XAU/USD | OKX `XAUT-USDT` 4H (Tether Gold token) | Spot proxy. Weekend bars (Fri 17:00 to Sun 18:00 ET) dropped. Cross-checked against Swissquote spot XAU/USD mid; warns if the gap is >0.2%. |
| MSFT | yfinance `MSFT` 1H, resampled to 4H | Regular session only; 4H bars anchored at 09:30 ET; about 15 min delayed. |
| SI | yfinance `SI=F` 1H, resampled to 4H | COMEX silver futures; 4H bars anchored at 18:00 ET session open. |
| BCH/USDT | OKX `BCH-USDT` 4H | UTC days. Backups: KuCoin, Binance.US. |
| BTC/USDT | OKX `BTC-USDT` 4H | UTC days. Added 2026-10-04. |
| GLD | yfinance `GLD` 1H, resampled to 4H | Regular session only; 4H bars anchored at 09:30 ET. Added 2026-10-04. |

- Tools: `fetch_prices.py` (OHLCV on 15M/1H/4H/1D/1W, UTC-stamped, 3-min cache in `tools/output/cache/`, fails loudly), `levels.py`, `indicators.py`, `position_calc.py`, `verify.py`, `log_entry.py`. The commands and per-tool fallbacks are in AGENT.md "Tools". (added: 2026-10-04)
- Timeframe sources: OKX `15m`/`1H`/`4H`/`1Dutc`/`1Wutc` for crypto and XAUT (XAUT weekend bars dropped, but XAUT **weekly** bars still include weekend trading); yfinance `15m` (max 60 days) / `1h` (max 730 days) / `1d` / `1wk` for MSFT, GLD, SI=F; 4H always built by `levels.load_4h`. Default 700 bars per TF (EMA200 seed weight < 1%). OKX XAUT history starts 2022, so gold weekly EMA200 is `n/a`. (added: 2026-10-04)
- Indicators: `python tools/indicators.py [SYMBOL ...] [--bars N] [--tf ...]`. **Conviction (the grade):** 8 EMA + VWAP checks (score −8..+8 → A/B/C), with session VWAP from 15M and weekly/monthly anchored VWAP from 1H (800 bars) on the levels calendar, plus a dynamic-levels table (EMAs and VWAPs with distance in % and ATR). Context: per TF EMA 9/21/50/200, RSI14, MACD(12,26,9), ATR14, volume trend, state, and the alignment score; on 4H: swings and structure; plus correlations. Driver series come from yfinance daily: `DX-Y.NYB` (DXY), `^TNX` (10Y yield, by level change), `^NDX`, `BTC-USD`. (added: 2026-10-04)
- TradingView check (pending, user to compare): BTC/USDT OKX 4H bar closed 2026-10-04 12:00 UTC: EMA9 85,014.45, EMA21 84,787.37, EMA50 84,317.11, EMA200 80,847.86, RSI14 57.71, MACD 252.57 / 244.31 / 8.26, ATR14 621.44. Until the user confirms, treat the indicator values as unverified against TradingView. (added: 2026-10-04)
- 4H data integrity check: OKX native 4H and yfinance 1H->4H match bars rebuilt from 15M/5M exactly (0.000% on all OHLC and reference levels, 4 symbols). Re-run `python tools/verify.py` after any source or pipeline change. (evidence: tools/output/2026-10-04_1856_verify.md; added: 2026-10-04)
- Verified 2026-10-04 against the user's charts: gold within $0.04, MSFT $0.23, SI $0.03, BCH $0.40. Gold PDH and PWH matched the user's levels within a few dollars.
- Blocked from this location: Binance (HTTP 451), Bybit (HTTP 403). yfinance has no spot gold (`XAUUSD=X` delisted), and `GC=F` sits about $22 above spot.
- Day boundaries: crypto uses UTC; spot gold rolls at 17:00 ET; CME rolls at 18:00 ET; equities use the ET calendar date. Weeks run Mon to Sun.
- These are unofficial public endpoints and can change without notice. If a feed fails, report it; don't substitute numbers.
