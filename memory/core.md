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

- Verified 2026-10-04 against the user's charts: gold within $0.04, MSFT $0.23, SI $0.03, BCH $0.40. Gold PDH and PWH matched the user's levels within a few dollars.
- Blocked from this location: Binance (HTTP 451), Bybit (HTTP 403). yfinance has no spot gold (`XAUUSD=X` delisted), and `GC=F` sits about $22 above spot.
- Day boundaries: crypto uses UTC; spot gold rolls at 17:00 ET; CME rolls at 18:00 ET; equities use the ET calendar date. Weeks run Mon to Sun.
- These are unofficial public endpoints and can change without notice. If a feed fails, report it; don't substitute numbers.
