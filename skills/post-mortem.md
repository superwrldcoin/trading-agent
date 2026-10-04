# Skill: post-mortem

## Purpose
Review a closed trade against its original plan. Measure the result in R, separate what went right or wrong in the thesis from what went right or wrong in execution, and propose lesson candidates for `memory/journal.md`. This is the only way a lesson can enter the playbook (see `memory/memory-protocol.md`).

## Required inputs (all required)
| Field | Example |
|---|---|
| Trade ID | T-001 |
| Asset | BCH/USDT |
| Side | long |
| Entry (fills + sizes) | 309.80 blended |
| Exit(s) (price, size, time) | 50% at 322.60, 50% at 309.80 |
| Stop at entry | 293.64 |
| Leverage and margin mode | 10x isolated |
| Thesis (written **before** entry) | "PDL sweep reclaim, range mid → PDH, PWH" |
| Open / close time (UTC) | |
| Original report file | `tools/output/<stamp>_levels.md` |
| Session link (optional) | `S-20261004-1856/BCHUSDT` in `memory/sessions.md` |

## Procedure
1. Check the required fields. Anything missing → F1 (see below). Record the trade in `memory/trades.md` (next free `T-NNN`, using the entry format at the top of that file).
2. **Result:** realized R per exit and overall, plus net $ after fees and funding. Use `tools/position_calc.py` with the actual fills to check the numbers.
3. **Excursions:** MAE (worst price against you) and MFE (best price in your favor) between entry and exit, from the 4H CSV, both in R.
4. **Thesis review:** did the structure, momentum grade, and levels behave as described? Mark each **held**, **failed**, or **untested**.
5. **Execution review:** did the fills, stop, and exits follow the plan? Mark each deviation and its cost in R.
6. **Classify the outcome:**
   | Thesis | Execution | Label |
   |---|---|---|
   | held | followed | good trade |
   | failed | followed | good loss: the process worked |
   | held | deviated | execution problem |
   | failed | deviated | review both |
7. **Lesson candidates:** at most 2, each phrased as a testable rule. Each goes into `journal.md` as a `MEMORY_UPDATE` (`action: add`) citing the trade ID. **Never** propose a playbook entry from one trade (the promotion rule needs 3 trades or a data check).
8. Check `journal.md` for similar earlier lessons. If this trade makes a total of 3, note "promotion candidate" and list the trade IDs.
9. **Calibration:** if the trade links to a session entry with `P(T1 before stop)`, record whether T1 came before the stop (1 or 0). Ask the user to update that entry's `Outcome:` line. Once 10 or more closed trades have estimates, report the Brier score and the hit rate per probability bucket (<40%, 40–60%, >60%). A sample under 10 trades is "too few to judge".

## Formulas
```
R_exit_i     = (exit_i − entry) / (entry − stop)          (long)
realized_R   = Σ w_i · R_exit_i
MAE_R        = (entry − lowest_low) / (entry − stop)      (long)
MFE_R        = (highest_high − entry) / (entry − stop)    (long)
Brier        = mean( (p_i − outcome_i)² ),  p as 0..1      (0 = perfect, 0.25 = always saying 50%)
```

**Worked example** (**hypothetical**, to show the math with the real BCH plan prices; this is not a real trade): long 6.0747 BCH at 309.80, stop 293.64 (risk 16.16/unit). 50% exits at T1 322.60, then the stop moves to break-even and the other 50% exits at 309.80.
- R at T1 = (322.60 − 309.80) / 16.16 = 0.792R. R at break-even = 0
- Realized R = 0.5 × 0.792 + 0.5 × 0 = **0.40R**
- Net $ = T1 half +$37.92 (after fees) + break-even half −$0.94 (fees only) = **+$36.98**
- Labeled: thesis "held to T1, T2 untested". Moving the stop to break-even was part of the plan → "good trade".

## Output format
```
## Post-mortem T-001: BCH/USDT long (closed <date>)
Result: +0.40R, +$36.98 net [CALC]; MAE -0.xxR, MFE +x.xxR [CALC]
Thesis: structure held, PDH reached, PWH untested [JUDGMENT]
Execution: followed plan [JUDGMENT]
Label: good trade
Lesson candidates:
MEMORY_UPDATE
file:     journal.md
action:   add
content:  <testable observation>
evidence: trade T-001, closed <date>; report <file>
date:     <YYYY-MM-DD>
```

## Common failure modes
- Judging the trade by P&L alone. A losing trade that followed a sound plan is a good loss.
- Rewriting the thesis after the fact. Use the version written before entry, or mark the thesis `[MISSING]`.
- Lessons that can't be tested ("be more patient"). Rephrase them as rules with conditions.
- Proposing playbook entries from a single trade.
- Saving price levels as lessons. The protocol forbids it.

## Missing inputs (AGENT.md fallback)
- Any required field missing (F1): ask once, listing all missing fields together.
- Thesis missing: do the R math and execution review, set the thesis to `[MISSING]`, and propose **no** lesson candidates. A lesson needs a thesis to test against.
- No price data covering the trade window (F2/F4): MAE/MFE `n/a`.
- Leverage or fees missing: net $ `n/a`, R still computed.
