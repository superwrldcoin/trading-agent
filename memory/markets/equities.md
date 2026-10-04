# Equities (class): market notes

Applies to US stocks and ETFs. Instrument files: `MSFT.md`, `GLD.md` (an ETF, but its behavior is covered in `metals.md`).

## Identity
- Data source: yfinance 1H, resampled to 4H anchored at 09:30 ET, regular session only (evidence: tools/levels.py EQUITY session; added: 2026-10-04)

## Trading hours
- Regular session 09:30–16:00 ET, Mon–Fri. Hourly bars start 09:30 and end with the 15:30 bar (evidence: yfinance 1H, MSFT and GLD, 120d to 2026-10-02; added: 2026-10-04)
- Pre-market and after-hours trading exists but is **not in the tools' data**. Gaps happen at the open (see the instrument files).
- 4H bars: two per day (09:30–13:30, 13:30–16:00), so indicators need about 200 calendar days to settle (evidence: skills/multi-timeframe-momentum.md; added: 2026-10-04)
- Holidays and half-days: **unknown** (not modeled)

## Typical volatility
- See the instrument files.

## Behavior around news and events
- Earnings: the largest MSFT move of the past year was the session after an earnings release (see `MSFT.md`). This is one data point, not a rule yet.
- Reaction to CPI / FOMC: **unknown**. Not measured yet.

## Data quirks
- yfinance stock data is about 15 minutes delayed (added: 2026-10-04)
- Daily levels use the ET calendar date of the regular session (evidence: tools/levels.py; added: 2026-10-04)

## Unknowns
- Extended-hours behavior, holiday handling: **unknown**

Last reviewed: 2026-10-04
