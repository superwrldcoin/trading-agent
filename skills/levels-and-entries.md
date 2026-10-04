# Skill: levels-and-entries

## Purpose
Build the execution zone, the pyramid of entry tranches, the ATR-based invalidation, and the targets. Everything ties to named reference levels (PDH/PDL, PWH/PWL, PMthH/PMthL) or confirmed 4H swing points.

## Required inputs
- Reference levels from `tools/levels.py`
- 4H OHLC CSV (for swings and ATR)
- Direction and structure read (`market-structure`)
- Scale-out split (`memory/user-preferences.md`: 40/40/20 for three targets, 50/50 for two)

## Procedure
**Tools:** swings, ATR, and the 4H structure come from `python tools/indicators.py`. Tranches, blended entry, R per target, and stop width come from `python tools/position_calc.py --zone LOW HIGH --stop .. --targets .. --atr ..`.

1. **Swing points:** a 4H swing high is a bar whose high is above the 2 bars before it and the 2 after it. A swing low is the mirror image. A swing is only confirmed after the 2 bars to its right have closed, so the last 2 bars can never be swings.
2. **Zone anchor:** the reference level or swing in the direction of the pullback (long: the nearest support at or below price that `market-structure` supports).
3. **Execution zone:** anchor to anchor ± 0.5 × ATR(14), stretching into the trade (long: anchor up to anchor + 0.5 ATR).
4. **Pyramid tranches:** split the planned size 30/30/40 across zone top, zone middle, and zone bottom. The largest piece sits at the best price. Blended entry is the weighted average. Risk is always sized as if **all** tranches fill.
5. **Invalidation (stop):** beyond the most recent confirmed swing that the setup depends on, plus a 0.5 × ATR buffer. It triggers on a 4H **close**, not a wick (user preference).
6. **Stop width check:** if (entry − stop) > 3 ATR, flag "wide stop" and lower the grade one letter. If it's > 4 ATR, there's no valid setup.
7. **Targets:** the next reference levels in the trade direction, nearest first. Combine levels within 0.5 ATR of each other into one target. Use 2 or 3 targets and assign the user's scale-out split.
8. **Add-on pyramid (optional, in profit only):** after T1 is hit and the stop is at break-even, an add of ≤ 50% of the original size may be shown at a retest of the broken level. Never add to a losing position.

## Formulas
```
TR      = max(H − L, |H − C_prev|, |L − C_prev|)
ATR_t   = (13·ATR_(t−1) + TR_t) / 14                        (Wilder, 14)
zone    = [anchor, anchor + 0.5·ATR]                         (long)
blended = 0.30·top + 0.30·mid + 0.40·bottom
stop    = swing_low − 0.5·ATR                                (long)
width   = (blended − stop) / ATR
```

**Worked example:** BCH/USDT long, OKX 4H as-of 2026-10-04 16:00 UTC.
- Last bar H 317.5, L 315.6, previous close 317.5 → TR = max(1.9, 0.0, 1.9) = 1.9. ATR = (13 × 5.58 + 1.9) / 14 = **5.32**
- Anchor = PDL **308.60** (2026-10-03). Zone = 308.60 → 308.60 + 2.66 = **311.26**. Middle = 309.93
- Blended = 0.30 × 311.26 + 0.30 × 309.93 + 0.40 × 308.60 = 93.38 + 92.98 + 123.44 = **309.80**
- Last confirmed swing low = **296.30** (2026-10-02 16:00, the sweep below 300). Stop = 296.30 − 2.66 = **293.64**
- Width = (309.80 − 293.64) / 5.32 = 16.16 / 5.32 = **3.04 ATR** → **wide-stop flag**, grade −1
- Targets: PDH **322.60** (equal to the 2026-10-03 swing high, so combined), then PWH = PMthH **366.10**. Two targets → 50/50

Structure check: BCH is in a 296.30–322.60 range and the blended entry sits at 51.3% of it, the middle third. `market-structure` rates that "no edge", so this zone passes the level math but **fails the structure filter**. Always run `market-structure` before presenting a zone.

For comparison, the user-supplied matrix had a 308–312 zone (matches), a 298.50 stop (above the 296.30 swing, so a sweep like Oct 2 would stop it out), and targets 320/345/365. The data has no level at 345, so this skill wouldn't produce that target.

## Output format
Feeds the milestone table in `report-format`. Give each row its price, reference, and the scale % or tranche %.

## Common failure modes
- Using a swing that isn't confirmed yet (one of the last 2 bars).
- Stop exactly on the swing with no buffer: that's where sweeps happen.
- Sizing for only the first tranche, so risk is understated when all three fill.
- Targets with no reference level (round numbers, "uncharted price discovery") presented as data. If a target has no level above it, label it `[JUDGMENT]` and use it only as a runner.
- Zones placed through the middle of a range with nothing anchoring them.

## Missing inputs (AGENT.md fallback)
- A reference level `n/a` (F4): use the next valid one; never use an `n/a` level.
- No confirmed swing for the stop: no valid setup. Don't invent a stop.
- Stale data (F3): show levels, but no zone or tranches.
- Direction unclear (F6): build only the side `market-structure` supports and state the assumption.
