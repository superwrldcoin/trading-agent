# MSFT: market notes

## Identity
- Instrument: Microsoft common stock (Nasdaq). Class notes: `equities.md`
- Data source: yfinance `MSFT` 1H → 4H, regular session only (added: 2026-10-04)
- On the tools watchlist: **yes**

## Trading hours
- 09:30–16:00 ET Mon–Fri; two 4H bars a day (see `equities.md`)

## Typical volatility (measured, yfinance MSFT, as-of 2026-10-02)
- Daily return σ: 30d 1.38%, 90d 2.56%, 1y 2.07% (annualized 1y ≈ 33%)
- Average daily range (30d): 2.03%
- 4H ATR14: 7.85 = 1.52% of price
- Largest 1-day move in the last year: 15.5% on 2026-07-30, with a 12.1% opening gap the same day
- Opening gaps > 2%: 19 in 1y
(evidence: yfinance MSFT daily 1y and 1H 120d, measured 2026-10-04; added: 2026-10-04)

## Drivers and correlations (30d)
- vs Nasdaq-100 ρ = 0.37; vs 10Y yield change ρ = −0.28 (evidence: tools/output/2026-10-04_1856_indicators.md; added: 2026-10-04)

## Behavior around news and events
- Earnings: the 2026-07-30 move came the session after the 2026-07-29 earnings release (date from an earnings calendar listing, akrostec.com). This is **one observation**. Earnings gaps can jump past 4H stops, which the regular-session 4H data can't show.
- Next earnings: check every time; don't store the date (it's news).
- Reaction to CPI / FOMC: **unknown**

## Data quirks
- About 15-minute data delay; no pre-market or after-hours data in the tools

## Unknowns
- Whether the user trades shares, options, or CFDs, and with what leverage: **unknown**

Last reviewed: 2026-10-04
