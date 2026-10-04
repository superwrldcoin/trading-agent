# Skill: macro-and-catalysts

## Purpose
Flag scheduled events and cross-market drivers that could invalidate a technical setup, and decide when a news search is worth doing. This skill adds context and warnings. It never creates levels, and its output never goes into memory (news is excluded by the memory protocol).

## Required inputs
- Watchlist instruments and the report time
- Event dates for the next 48h (from a search, the user, or `[MISSING]`)
- Daily returns of the instrument and its drivers (for correlation)

## Drivers per instrument
| Instrument | Main drivers (usual sign) | Key scheduled events |
|---|---|---|
| XAU/USD | DXY (−), US real yields (−), risk-off flows (+) | FOMC, CPI, PCE, NFP, Fed speakers |
| SI | Gold (+, strong), DXY (−), industrial/risk sentiment (+) | Same as gold, plus China PMI |
| MSFT | Nasdaq-100 (+), 10Y yield (− for growth stocks) | MSFT earnings, CPI, FOMC, big-tech earnings |
| BCH/USDT | BTC (+, strong), overall crypto risk appetite | US macro prints, BTC/crypto-specific events |

The signs are typical relationships, not rules. Always measure the current correlation instead of assuming it.

## Procedure
1. **Event check:** list the high-impact events within 48h of the report for each instrument's drivers. Within **24h** → add a `Catalyst risk` note and lower the grade one letter. Within **4h** → no execution zone; report "wait for event".
2. **Correlation check:** correlation of daily returns over the last 30 sessions. |ρ| ≥ 0.7 counts as strong: treat the two as one exposure when the user holds both.
3. **News search, only when:**
   - a 4H bar moved > 2 ATR, or there's a gap > 1 ATR, with no known scheduled event behind it
   - the user asks
   - the event calendar is unknown and a report is due within 48h of a likely event
   **Interactive sessions:** searches need the user's approval (WebSearch is set to ask first). **Full-agent runs** (web page / `ask.ps1 -Agent`): the interface allows WebSearch, so run the event check (high-impact events in the next 48h for this asset) on every setup. If it comes back denied (because `.claude/settings.json` still lists WebSearch under "ask"), write `calendar not checked [MISSING]` and cap the grade at B. Cite the source and time for each news item, tag it `[DATA:news]`, and never quote unsourced headlines.
   **Options:** an event before expiry means IV crush afterwards. Flag it in the `options-greeks` section.
4. Summarize in at most 3 bullets. News and events **never** go into memory.

## Formulas
```
r_t = close_t / close_(t−1) − 1                     (daily)
ρ   = corr(r_A, r_B) over the last N sessions        (Pearson, N = 30)
```

**Worked example:** `python tools/indicators.py` (run 2026-10-04, last 30 daily returns):
- Gold vs silver ρ = **0.84** → strong. Long gold and long silver is effectively **one** metals bet, so combined risk should be counted against the open-risk cap in `risk-and-sizing`.
- Gold/silver ratio = 4,143.10 / 60.42 = **68.58** (context only, `[CALC]`).
- Gold vs DXY **−0.43**, gold vs 10Y yield change **−0.49**: the usual inverse signs, but only moderate right now.
- MSFT vs NDX **0.37**, BCH vs BTC **0.35**: weaker than "typical". That's exactly why the skill measures instead of assuming.

Driver series (yfinance daily): DXY `DX-Y.NYB`, 10Y yield `^TNX` (compared by level change), Nasdaq-100 `^NDX`, BTC `BTC-USD`. If a fetch fails, the tool prints `n/a (fetch failed)`; report that as `[MISSING]`, never as a typical value.

## Output format
```
Macro & catalysts:
- Events <48h: <event, time UTC, source> or "calendar not checked [MISSING]"
- Correlation: gold/silver ρ 0.84 (30d), treat as one exposure [CALC]
- News: none searched (no trigger) | <headline, source, time> [DATA:news]
```

## Common failure modes
- Stating event dates from memory ("CPI is on the 15th"). They must come from a source or be `[MISSING]`.
- Using typical correlations instead of measured ones.
- Letting news override structure without a price reaction. News explains moves; it doesn't create levels.
- Writing news or event outcomes into memory.
- Searching news for every report. Only search when a trigger in step 3 fires.

## Missing inputs (AGENT.md fallback)
- No event calendar (F1/F2): ask the user or request a search once. Otherwise mark `calendar not checked [MISSING]`, and the grade can't be A.
- Driver series not available (F4): correlation `n/a`.
- Search denied by the user: continue without it and note "news not checked".
