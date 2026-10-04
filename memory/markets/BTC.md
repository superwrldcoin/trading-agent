# BTC: market notes

## Identity
- Instrument: Bitcoin vs USD. Class notes: `crypto.md`
- Data source: yfinance `BTC-USD` daily (driver series in tools/indicators.py) (added: 2026-10-04)
- On the tools watchlist: **no**. It's used only as a driver for BCH. No 4H levels or indicators are produced for it.

## Trading hours
- 24/7 (see `crypto.md`)

## Typical volatility (measured, yfinance daily, as-of 2026-10-04)
- Daily return σ: 30d 1.96%, 90d 2.03%, 1y 2.35% (annualized 1y ≈ 45%)
- Average daily range (30d): 2.55%
- 4H ATR14: 593.83 = 0.70% of price (from 1H → 4H UTC bins, 120d)
- Largest 1-day move in the last year: 14.1% on 2026-02-05. Cause: **unknown**
(evidence: yfinance BTC-USD daily 1y and 1H 120d, measured 2026-10-04; added: 2026-10-04)

## Drivers and correlations
- BCH vs BTC ρ = 0.35 (30d) (evidence: tools/output/2026-10-04_1856_indicators.md; added: 2026-10-04)
- BTC vs DXY, Nasdaq, rates: **unknown**. Not measured.

## Behavior around news and events
- **unknown**

## Data quirks
- None known

## Unknowns
- Which venue or pair the user trades BTC on (if at all), leverage used: **unknown**

Last reviewed: 2026-10-04
