# Skill: market-structure

## Purpose
Classify the 4H structure as uptrend, downtrend, or range. Identify the support and resistance that matter, and read volume. This skill decides which side `levels-and-entries` is allowed to build.

## Required inputs
- 4H OHLCV CSV and reference levels (`tools/levels.py`)
- Confirmed swing points (definition in `levels-and-entries`: 2 bars on each side)

## Procedure
1. **Swing sequence:** list the last 4 confirmed swing highs and the last 4 swing lows, oldest first.
2. **Classify:**
   - **Uptrend:** the last 2 swing highs are higher highs **and** the last 2 swing lows are higher lows.
   - **Downtrend:** lower highs **and** lower lows.
   - **Range:** anything else. Range high = highest swing high since the range began; range low = lowest swing low.
3. **Break of structure:** a 4H close beyond the last swing in the opposite direction (uptrend: a close below the last higher low) ends the trend label.
4. **Support/resistance:** reference levels and swings within 2 ATR of price. A level counts more if 2 or more sources fall within 0.5 ATR of it (e.g. PDH = swing high).
5. **Sweep:** a wick beyond a level whose bar closes back inside. This is the typical pattern the user's zones look for (e.g. "PDL sweep retest").
6. **Range position:** `(price − range low) / (range high − range low)`. Lower third favors longs, upper third favors shorts, and the middle third gets "no edge, wait" `[JUDGMENT]`.
7. **Volume:** relative volume = bar volume / 20-bar average. ≥ 1.5 on a breakout or sweep supports it; < 0.7 is a weak move.

## Formulas
```
range_pos = (P − range_low) / (range_high − range_low)
rvol      = volume_t / mean(volume_(t−19..t))
```

**Worked example 1:** XAU/USD, OKX XAUT 4H to 2026-10-02 20:00 UTC.
- Swing highs: 4,310.6 (09-25) → 4,216.8 (09-30) → 4,190.8 (10-01) → 4,221.6 (10-02)
- Swing lows: 4,117.5 (09-28) → 4,143.7 (10-01) → 4,140.0 (10-02) → 4,132.1 (10-02)
- The last two highs are higher (4,190.8 → 4,221.6) but the last two lows are lower (4,140.0 → 4,132.1). Neither trend rule holds → **Range**, 4,117.5 (= PMthL) to 4,221.6
- Range position = (4,143.10 − 4,117.5) / (4,221.6 − 4,117.5) = 25.6 / 104.1 = **24.6%** → lower third
- This fits the user's "bearish pullback into local base consolidation". Range = base. Longs only from the range low, which momentum still grades C (see `multi-timeframe-momentum`).

**Worked example 2:** BCH/USDT relative volume, OKX 4H.
- The 2026-10-03 20:00 bar (the swing high at PDH 322.6): volume 3,143 vs 20-bar average 1,970 → rvol = **1.60**, a high-volume rejection.
- The last bar (2026-10-04 16:00, still forming): rvol **0.36**. Low, but the bar isn't finished. Never judge a forming bar's volume.

## Output format
`Structure:` line in the report:
```
Structure: Range 4,117.50-4,221.60 (PMthL to 10-02 swing high), price at 24.6% (lower third); last low swept 4,140.0 -> 4,132.1 [CALC/JUDGMENT]
```

## Common failure modes
- Calling a trend from one higher high (you need both highs **and** lows).
- Using unconfirmed swings from the last 2 bars.
- **Volume on proxies:** XAUT volume is crypto-exchange token volume, not gold market volume. Don't read volume on XAU/USD. SI=F and MSFT volume are real. BCH is single-exchange (OKX) volume only.
- Treating every reference level as equally strong. Confluence matters.
- Counting a wick through a level as a break. Breaks need a 4H close (user preference).

## Missing inputs (AGENT.md fallback)
- Fewer than 2 swing highs or lows in the data (F4): structure `n/a`. No setup.
- No volume column (some user CSVs): skip step 7 and note it (F4).
- Stale data (F3): structure may be described, labeled with its as-of time.
