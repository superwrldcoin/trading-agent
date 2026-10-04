# Skill: data-ingest

## Purpose
Bring outside data into the analysis safely: tool CSVs, CSVs from the user, screenshots, and pasted prices. Normalize timestamps, label where each number came from, and check user-supplied numbers against a feed before relying on them.

## Required inputs
One or more of:
- `tools/output/<stamp>_<SYMBOL>_4h.csv` (from `tools/levels.py`)
- A user CSV (any OHLC export)
- A chart screenshot
- Pasted text (prices, matrices, levels)

## Procedure
1. **Identify** the source and give it a tag: tool CSV → `[DATA]`; anything the user provides → `[DATA:user]`, and for screenshots `[DATA:screenshot]`.
2. **Tool CSVs:** columns are `ts, open, high, low, close, volume[, confirmed]`. `ts` is the bar **open** time in UTC (ISO 8601). If `confirmed` is `False`, the last bar is still forming. Use it for current price, never for swings or closes that need to be final.
3. **User CSVs:** map the columns (case-insensitive: time/date/timestamp, open, high, low, close, volume). Find the timezone: an explicit offset, the header, or ask. Check whether time means bar open or close (TradingView exports use bar open). Convert to UTC. Sort ascending, drop duplicates, and check high ≥ max(open, close) and low ≤ min(open, close) on every row.
4. **Screenshots:** read only what's clearly printed (symbol, timeframe, price scale, labeled lines). Record the chart's as-of time if it's visible. Never measure prices by eye from pixel positions. Screenshot values are for context only until a feed confirms them.
5. **Pasted prices:** pull out each number with its label. Compare against the latest feed price: deviation ≤ 0.5% → consistent. > 0.5% → F5 conflict.
6. **Record** every ingested input on the report's `Data:` line (source, as-of, tag).

## Timestamp rules
- Store and report in UTC, ISO 8601 (`2026-10-04 16:00 UTC`).
- Bars are labeled by **open** time. A 4H bar `16:00` covers 16:00–20:00 UTC.
- Session dates follow `memory/core.md`: crypto by UTC day, gold rolls at 17:00 ET, CME at 18:00 ET, equities by the ET date.
- An input with no timestamp gets the as-of "unknown". It can't be used to build zones or stops.
- Remember daylight saving: New York is UTC−4 in summer and UTC−5 in winter. Convert with a tz library, never with a fixed offset.

## Formulas
```
deviation_% = (user_value / feed_value − 1) × 100
```

**Worked example:** the user's pasted matrix said "XAU/USD Current Price: $4,140.36", as-of unknown. OKX XAUT-USDT live on 2026-10-04 was 4,140.40.
- Deviation = (4,140.36 / 4,140.40 − 1) × 100 = **−0.001%** → consistent (≤ 0.5%)
- The user's PDH of 4,190 vs the tool's 4,190.80 → −0.02%, consistent. The level can be cited as `PDH 4,190.80 [CALC]`, with the user's figure as confirmation.
- Silver: the user's PWL of 62.70 vs the tool's 63.51 (from SI=F) → −1.28% → **F5 conflict**. Show both and lower the grade. The likely cause is spot vs futures, which counts as a `[JUDGMENT]`.

## Output format
```
Data: OKX BCH-USDT 4H to 2026-10-04 16:00 UTC (last bar open) [DATA]; user matrix as-of unknown [DATA:user], checked vs feed: max deviation 0.02% (gold), 1.28% (silver PWL, conflict)
```

## Common failure modes
- Treating a bar's open-time label as its close time, which shifts every level by one bar.
- Parsing a local-time CSV as UTC.
- Using the still-forming last bar as a confirmed close or swing.
- Measuring levels by eye off a screenshot.
- Quietly preferring the user's number over the feed, or quietly dropping it. Always show the comparison.
- Reading a European number format ("4.140,36") as 4.14.

## Missing inputs (AGENT.md fallback)
- Timezone or bar-time convention unclear (F1): ask once. Until then the file is context only, as-of unknown.
- A pasted number with no feed to check it against (F2): `[DATA:user] unverified`. Show it, but don't build zones from it.
- Corrupt rows (high < low, gaps): drop them, report how many, and continue with what's left (F4).
