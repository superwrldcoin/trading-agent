# SI (silver): market notes

## Identity
- Instrument: COMEX silver futures, continuous front contract `SI=F` (yfinance). Class notes: `metals.md`
- On the tools watchlist: **yes** (`SI`)

## Trading hours
- Sun 18:00 ET open, trades around the clock Sun–Fri (see `metals.md`). 4H bars anchored at 18:00 ET; session date rolls at 18:00 ET (evidence: tools/levels.py CME session; added: 2026-10-04)

## Typical volatility (measured, yfinance SI=F, as-of 2026-10-02)
- Daily return σ: 30d 2.07%, 90d 2.56%, 1y 4.19% (annualized 1y ≈ 67%). Recent volatility is about half the yearly figure.
- Average daily range (30d): 1.77%
- 4H ATR14: 0.92 = 1.53% of price
- Largest 1-day move in the last year: 31.3% on 2026-01-30. Cause: **unknown**
- Daily opening gaps > 2%: 81 in 1y. Likely inflated by contract rolls in the continuous series. **Unverified, don't rely on it.**
(evidence: yfinance SI=F daily 1y and 1H 120d, measured 2026-10-04; added: 2026-10-04)

## Drivers and correlations (30d)
- vs gold ρ = 0.84; vs DXY ρ = −0.48 (evidence: tools/output/2026-10-04_1856_indicators.md; added: 2026-10-04)

## Behavior around news and events
- **unknown**

## Data quirks
- This is futures, not spot. The user's chart levels differed by up to about 1.3% (e.g. PWL 62.70 vs 63.51), likely spot vs futures (evidence: skills/data-ingest.md worked example; added: 2026-10-04)

## Unknowns
- Whether the user trades futures, spot XAG/USD, or an ETF: **unknown**
- How contract rolls affect levels around expiry: **unknown**

Last reviewed: 2026-10-04
