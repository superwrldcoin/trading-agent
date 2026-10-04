# Metals (class): market notes

Applies to gold (XAU/USD, GLD) and silver (SI). Instrument files: `SI.md`, `GLD.md`. Spot gold (XAU/USD) has no separate file yet; its tool data notes are in `memory/core.md`.

## Identity
- Instruments: spot gold via the OKX `XAUT-USDT` proxy, COMEX silver futures `SI=F` (yfinance), gold ETF `GLD` (yfinance) (added: 2026-10-04)

## Trading hours
- COMEX futures (SI): open Sunday 18:00 ET. Hourly bars run around the clock Sun–Fri (evidence: yfinance SI=F 1H, first Sunday bar 18:00 ET, 120d to 2026-10-02; added: 2026-10-04). Daily 17:00–18:00 ET break: per the CME schedule, **not verified in data**.
- Spot gold: the tools treat Fri 17:00 ET → Sun 18:00 ET as closed and drop XAUT weekend bars (evidence: tools/levels.py; added: 2026-10-04)
- GLD: exchange hours only, see `GLD.md`
- Holidays: **unknown** (not modeled by the tools)

## Typical volatility (measured)
- Silver 1y daily σ 4.19% vs GLD 1.87%: silver moves about 2.2× as much as gold (evidence: yfinance daily 1y, as-of 2026-10-02; added: 2026-10-04)
- Both had their largest 1-day move of the year on **2026-01-30** (SI 31.3%, GLD 10.3%, absolute size; direction not recorded). Cause: **unknown** (evidence: yfinance daily; added: 2026-10-04)

## Drivers and correlations (30d daily returns, as-of 2026-10-04)
- Gold vs silver ρ = 0.84. Treat positions in both as one exposure.
- Gold vs DXY ρ = −0.43; silver vs DXY ρ = −0.48
- Gold vs 10Y yield change ρ = −0.49
(evidence: tools/output/2026-10-04_1856_indicators.md; added: 2026-10-04)

## Behavior around news and events
- Reaction to CPI / FOMC / NFP: **unknown**. Not measured yet.

## Data quirks
- XAUT volume is token volume, not gold market volume. Don't use it (added: 2026-10-04)
- yfinance has no spot gold; `GC=F` futures ran about $22 above spot on 2026-10-04 (evidence: memory/core.md; added: 2026-10-04)

## Unknowns
- Holiday schedule handling, contract-roll effects on SI=F levels: **unknown**

Last reviewed: 2026-10-04
