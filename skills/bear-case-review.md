# Skill: bear-case-review (red team)

## Purpose
Before the report goes out, argue **against** the setup with the strongest evidence available, then list exactly what would have to happen for the thesis to fail. The point is to find the weakest link while it's still cheap. This is a separate, systematic pass, not a sentence tacked onto Notes. For a short setup, the "bear case" is the bull case.

## Required inputs
- The finished analysis: setup, grade, levels, conviction table, structure, vol check, rules check, macro and portfolio results
- No new tool. This pass reuses the numbers the tools already produced. Every claim must point to one of them. Use `[CALC]` or `[DATA]` for evidence and `[JUDGMENT]` for the argument.

## Procedure
1. **Steelman the other side** in at most 5 bullets, strongest first. Pull from these sources, in this order:
   - **Conviction checks against the trade:** each −1 in the EMA + VWAP table (e.g. "1D EMA50 still under EMA200: the longer trend hasn't turned").
   - **Opposite-side levels:** the nearest reference level, swing, EMA or VWAP sitting between entry and T1. That's where the other side defends.
   - **Structure:** range position against the trade (e.g. a long in the upper third), an unconfirmed break, a recent sweep in the other direction.
   - **Volatility:** the stop's touch odds from `vol_check.py`. If the stop is hit within 1 day on a large share of days, noise alone can kill the trade.
   - **Macro and correlation:** an event before T1, a driver moving against the trade, a correlated position doubling the bet.
   - **History:** journal lessons and post-mortems that match this setup.
2. **Failure conditions:** 2–4 observable, falsifiable statements, each with a level or a time. For example: "4H close below 296.30", "no tag of T1 by Friday's close (time stop)", "DXY > X after CPI". Each one says what you'd see and where.
3. **Weakest link:** one line naming the single assumption the trade depends on most.
4. **Verdict effect:** if the bear case contains a FAIL-level fact the main analysis missed, fix the analysis (grade, flags) and say so. Otherwise the grade stands. Never lower the grade just because a bear case exists. It always exists.
5. Keep it short: steelman (≤ 5 bullets), failure conditions, weakest link.

## Formulas
None of its own. Every number comes from another skill's tool output.

**Worked example** (BCH/USDT long from the 296.30–298.96 range-low zone, tool outputs from 2026-10-04):
- **Steelman (bear):**
  1. 1D EMA50 273.38 is still below the 1D EMA200 310.04. The daily trend hasn't turned, so this is a counter-trend bounce on the higher timeframe `[CALC]`.
  2. The zone sits on a level already swept on 10-02 (296.30). A second sweep to the 293s is common after a first one, and the stop is only 0.73 ATR below `[CALC/JUDGMENT]`.
  3. 1D EMA200 at 310.04 sits between entry and T2: overhead supply `[CALC]`.
  4. BCH correlates only 0.35 with BTC, so a BTC rally may not lift it `[CALC]`.
- **Failure conditions:** a 4H close below 293.64; a rejection at 310 (1D EMA200) before T2; no T1 (308.60) within 5 days.
- **Weakest link:** that 296.30 holds a second test.
- **Verdict effect:** none. All of this was already reflected in the B grade.

## Output format
```
Bear case (red team):
- <strongest argument against> [CALC/JUDGMENT]
- ...
Fails if: <condition 1>; <condition 2>; <condition 3>
Weakest link: <one line>
```
Placed after Notes and before "What would change this".

## Common failure modes
- Strawmanning: weak or generic objections ("markets are uncertain"). Use the actual numbers.
- No levels or times in the failure conditions, so they can't be checked later in a post-mortem.
- Letting the bear case silently replace the analysis. Either it found something (fix and say so) or the grade stands.

## Missing inputs (AGENT.md fallback)
- A tool's output missing (e.g. vol check not run because there's no leverage): skip that source and say which.
- No setup (No valid setup): the bear case isn't needed. State what the other side's best case is in one line instead.
