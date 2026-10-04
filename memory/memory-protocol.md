# Memory Protocol (DRAFT)

Rules for what the agent may write to `memory/` and how. The agent never edits memory files freely. It proposes changes as `MEMORY_UPDATE` blocks, and only changes that follow this protocol get applied.

## Files

| File | Holds |
|---|---|
| `core.md` | Stable facts about how this agent operates and the user's setup. Rarely changes. |
| `user-preferences.md` | User-stated preferences: risk tolerance, timeframes, instruments, reporting style. |
| `journal.md` | Lessons from individual trades and analyses, not yet proven. The waiting room before the playbook. |
| `playbook.md` | Proven, dated rules the agent applies in every analysis. |
| `markets/<SYMBOL>.md` | Durable structural notes about each instrument (e.g. session behavior, typical volatility regime, earnings seasonality). |

## What qualifies for memory

An entry must be all three:

1. **Durable:** still likely to be true weeks or months from now.
2. **Verified:** checked, not assumed.
3. **Sourced:** it comes from our own data (a backtest, a computed statistic, a fetched dataset) **or** is confirmed by a closed trade.

User preferences qualify when the user states them directly. The user is the source.

## What does not qualify

- **Single-trade conclusions:** one win or one loss proves nothing. Log it in `journal.md` as an observation, not as a rule.
- **Price levels:** support/resistance, targets, entries. They go stale too fast.
- **News:** headlines, earnings results, macro events.
- **Opinions:** the agent's or anyone else's, including "X looks bullish."
- Anything unverified, recalled from training data, or that you can't trace to evidence.

## MEMORY_UPDATE block

Every proposed change uses this format, one block per change:

```
MEMORY_UPDATE
file:     <path under memory/, e.g. playbook.md>
action:   add | revise | flag-stale
content:  <the exact entry text, or for revise: the new text plus which entry it replaces>
evidence: <backtest/output file, dataset + date range, or closed trade IDs>
date:     <YYYY-MM-DD>
```

- **add:** new entry. `evidence` is required.
- **revise:** changes an existing entry. Quote the old entry and give the evidence for the change.
- **flag-stale:** marks an entry for review. Do not delete it. `evidence` states why (age, contradicting data, failed trades).

Blocks with no evidence are rejected.

### Example

```
MEMORY_UPDATE
file:     journal.md
action:   add
content:  Breakout entries taken in the first 15 min after the open stopped out before reaching target.
evidence: trades T-014, closed 2026-10-02
date:     2026-10-04
```

## Promotion rule: journal → playbook

A lesson always lands in `journal.md` first. It moves to `playbook.md` only when one of these is true:

- it has shown up in **at least 3 closed trades** (list the trade IDs), **or**
- it was **verified against data** (a backtest or statistical check; cite the output file in `tools/output/`).

Promotion is a `MEMORY_UPDATE` with `action: add` on `playbook.md` that cites the journal entries. The journal entry is then marked `promoted: <date>`. It is not deleted.

## Staleness rule

- Every `playbook.md` entry carries a date: `added:` and, if changed, `revised:`.
- Any entry whose latest date is **more than 90 days old** gets flagged for review with `action: flag-stale`.
- Check for stale entries at the start of every analysis session.
- A flagged entry stays in use but is marked `[STALE]` until someone re-verifies it (new date) or retires it.

### Playbook entry format

```
- <rule> (evidence: <source>; added: YYYY-MM-DD; revised: YYYY-MM-DD)
```

## Open questions

- Who applies MEMORY_UPDATE blocks: the agent automatically, or the user after review?
- Trade ID scheme and where closed trades are recorded (e.g. `memory/trades.md` or a CSV in `tools/output/`).
- Should retired playbook entries move to an archive file?
