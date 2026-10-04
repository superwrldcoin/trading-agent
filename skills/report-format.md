# Skill: report-format

## Purpose
Turn the analysis into the report the user reads. Each instrument gets one milestone table that holds the 4H Execution Matrix. Every number and claim carries a source tag, and the disclaimer appears once at the end.

## Required inputs
- Per instrument: `Data:` line facts (source, interval, range, as-of) from `tools/levels.py` output
- Structure read (`market-structure`), momentum grade (`multi-timeframe-momentum`)
- Zone, blended entry, invalidation, targets with scale-out % (`levels-and-entries`)
- R multiples (`risk-and-sizing`). Position size and leverage only if the user gave equity (`leveraged-position-math`)

## Procedure
1. Start with an `[ASSUMPTION]` line only if fallback F6 applied (ambiguous request).
2. For each instrument, in watchlist order:
   1. Header: `## <SYMBOL>: 4H, as-of <last bar UTC>`.
   2. `Data:` line, tagged `[DATA]`.
   3. `Structure:` one line `[JUDGMENT]`, and `Grade:` with its reason `[JUDGMENT]`.
   4. Milestone table (below). Rows are sorted by price, highest first, so the table reads like the chart.
   5. `Notes:` up to 3 bullets for flags (wide stop, proxy premium, stale data, missing inputs).
   6. If there's no valid setup, replace steps 3–5 with `No valid setup: <reason>` and still show the levels table from `tools/levels.py`.
3. `Memory:` the `MEMORY_UPDATE` blocks, or `Memory: none`.
4. Disclaimer, **once**, as the last line.

## Tags
| Tag | Use for | Example |
|---|---|---|
| `[DATA]` | Fetched values or user-supplied values (write `[DATA:user]` for user-supplied) | last price 316.50 |
| `[CALC]` | Anything computed this session | R multiple, blended entry, ATR |
| `[JUDGMENT]` | Interpretation | "range, lower half", grade |
| `[MISSING]` / `[ASSUMPTION]` | Fallback markers (AGENT.md F1–F6) | "Sizing skipped: equity [MISSING]" |

Every table row gets exactly one tag: the weakest source that went into it. A level computed from fetched data is `[CALC]`.

## Formulas
- Distance from current: `dist% = (level / current − 1) × 100`
- R multiple of a level: `R = (level − entry) / (entry − stop)` for longs, `(entry − level) / (stop − entry)` for shorts

**Worked example:** BCH/USDT, OKX data as-of 2026-10-04 16:00 UTC. Current 316.50, blended entry 309.80, stop 293.64.
- T1 at PDH 322.60: dist = (322.60 / 316.50 − 1) × 100 = **+1.93%**; R = (322.60 − 309.80) / (309.80 − 293.64) = 12.80 / 16.16 = **0.79R**
- Stop: dist = (293.64 / 316.50 − 1) × 100 = **−7.22%**; R = **−1.00R**

## Output format
```
## BCH/USDT: 4H, as-of 2026-10-04 16:00 UTC
Data: OKX BCH-USDT, 4H, 2026-08-25 -> 2026-10-04 16:00 UTC, 245 bars, last bar open [DATA]
Structure: Range 296.30-322.60 (10-02 sweep low to PDH); price at 76.8% (upper third), zone at 51.3% (middle third) [CALC/JUDGMENT]
Grade: B (4H and daily aligned bullish = A, minus one: stop is 3.04 ATR wide) [JUDGMENT]

| Milestone | Price | Dist | R | Scale | Reference | Tag |
|---|---|---|---|---|---|---|
| Target 2 (Expansion 50%) | 366.10 | +15.67% | 3.48R | 50% | PWH = PMthH | [CALC] |
| Target 1 (De-Risk 50%) | 322.60 | +1.93% | 0.79R | 50% | PDH | [CALC] |
| Current price | 316.50 | 0.00% | | | last close | [DATA] |
| Execution zone top | 311.26 | −1.66% | | 30% | PDL + 0.5 ATR | [CALC] |
| Blended entry | 309.80 | −2.12% | 0R | | 30/30/40 tranches | [CALC] |
| Execution zone bottom | 308.60 | −2.50% | | 40% | PDL | [CALC] |
| Invalidation (4H close) | 293.64 | −7.22% | −1.00R | 100% | swing low 296.30 − 0.5 ATR | [CALC] |

Notes:
- Weighted R at targets: 2.14R (passes the 2.0 minimum) [CALC]
- Stop is wide at 3.04 ATR; grade lowered one letter [JUDGMENT]
- Sizing skipped: equity [MISSING]
- Zone is mid-range (51.3%): market-structure says "no edge, wait" [JUDGMENT]

Memory: none

Analysis only, not financial advice. The user makes and executes all trading decisions.
```
(The zone-middle tranche at 309.93 is left out to keep the table short. List it if you're asked for tranche detail.)

Read honestly, this setup fails `market-structure` step 6 (zone in the middle third), so a live report would say `No valid setup: zone mid-range, wait for price near 296.30-305` and show only the levels. The full table is here to show the format, using real numbers.

## Common failure modes
- Repeating the disclaimer in every section. It goes once, at the very end.
- Rows not sorted by price, or the current-price row missing, so the user can't read it like a chart.
- A row with no reference level ("Target 3: 380" with nothing behind it). Every price needs a named source.
- Tagging a computed level `[DATA]`, or tagging a user-pasted number `[DATA]` instead of `[DATA:user]`.
- Giving instructions ("buy at 309"). Describe zones and conditions; the user decides.

## Missing inputs (AGENT.md fallback)
- Data feed failed (F2) or stale (F3): report `No valid setup: data unavailable/stale`, plus whatever levels exist.
- Some levels `n/a` (F4): show them in the levels table but never as milestones.
- Proxy vs spot conflict (F5): add both prices in Notes and lower the grade one letter.
- No equity (F1): leave out the sizing columns and add `Sizing skipped: equity [MISSING]`.
