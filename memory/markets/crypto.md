# Crypto (class): market notes

Applies to BTC, BCH, and any other crypto pairs. Instrument files: `BTC.md`, `BCH.md`.

## Identity
- Instrument / class: spot crypto vs USD/USDT
- Data source used by the tools: OKX public API (4H and finer), yfinance daily `*-USD` for driver series (evidence: memory/core.md; added: 2026-10-04)

## Trading hours
- Session: 24/7. There are no gaps in daily or hourly data over the last year (0 opening gaps > 2% on BTC-USD and BCH-USD daily, 1y) (evidence: yfinance daily, as-of 2026-10-04; added: 2026-10-04)
- Day boundary used for PDH/PDL: 00:00 UTC. Weeks run Mon to Sun UTC (evidence: tools/levels.py CRYPTO session; added: 2026-10-04)
- Weekends / holidays: trades through them. How weekend liquidity and volatility compare to weekdays: **unknown**
- Daily break: none

## Typical volatility (measured)
- See the instrument files. BCH runs about 2× BTC's 1y daily σ (4.17% vs 2.35%) (evidence: yfinance daily 1y, as-of 2026-10-04; added: 2026-10-04)

## Drivers and correlations
- BCH vs BTC daily returns ρ = 0.35 (30d), weaker than the "alts follow BTC" assumption. Don't assume BTC direction carries over (evidence: tools/output/2026-10-04_1856_indicators.md; added: 2026-10-04)

## Behavior around news and events
- Reaction to US macro releases (CPI, FOMC, NFP): **unknown**. Not measured yet.
- Crypto-specific events (exchange incidents, regulation, forks, ETF flows): **unknown**

## Data quirks
- Binance (HTTP 451) and Bybit (HTTP 403) are blocked from this location. Use OKX; KuCoin and Binance.US are backups (evidence: memory/core.md; added: 2026-10-04)
- OKX volume is single-exchange volume, not market-wide (added: 2026-10-04)
- yfinance `BCH-USD` and OKX `BCH-USDT` differ slightly (USD vs USDT, different venues) (evidence: 316.13 vs 316.20 on 2026-10-04; added: 2026-10-04)

## Unknowns
- Funding-rate behavior, typical exchange fees, and leverage the user uses: **unknown**
- Weekend vs weekday volatility: **unknown**

Last reviewed: 2026-10-04
