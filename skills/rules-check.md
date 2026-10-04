# Skill: rules-check

## Purpose
Check every plan against the rules **you** wrote, before any setup analysis, and state plainly which ones it breaks. It's a checklist you configured, not a lecture. It keeps your discipline from depending on your mood during a fast move.

## Required inputs
- The **Trading rules** block in `memory/user-preferences.md` (a ```json block after `<!-- rules -->`). `null` = not set. You edit the numbers; the agent never sets or changes them.
- The plan: symbol, side, leverage; when known: risk % of equity, weighted R, whether there's a stop, notional, 5-day liquidation-touch odds (`vol_check.py`), option premium
- Context the tool reads itself: `memory/positions.json` (open positions, equity) and `memory/trades.md` (last loss, trades opened today)
- Tool: `python tools/rules_check.py --symbol .. --side .. --leverage .. [--risk-pct .. --weighted-r .. --stop .. | --no-stop --notional .. --liq-touch-5d .. --option-premium ..]`. `quick_check.py` runs it automatically.

## Rules available
| Key | Meaning |
|---|---|
| `max_leverage.{crypto, precious metals, equities}` | Max leverage per theme |
| `max_risk_pct_per_trade` | Loss at stop, % of equity |
| `max_open_risk_pct` | All open risk to stops plus this plan, % of equity |
| `max_theme_gross_pct` | Gross notional in this plan's theme, including the plan, % of equity |
| `max_positions` | Open positions including this one |
| `min_weighted_r` | Weighted R with every stage filled |
| `require_stop` | `true` = no plan without a stop |
| `max_liq_touch_5d_pct` | Max historical 5-day liquidation-touch odds (`leverage-volatility-check`) |
| `max_option_premium_pct` | Premium paid on one options trade, % of equity |
| `loss_cooldown_hours` | Minimum hours since the last losing trade closed |
| `max_trades_per_day` | Trades opened today, including this one |

## Procedure
1. **First step of every analysis:** run the check with whatever plan values are known.
2. Each rule comes back **PASS**, **BREAKS**, **not set**, or **unknown** (an input is missing, e.g. no equity, so no % figures).
3. Put the result at the top of the report, in one line: `Rules: breaks 2 of 7 set: max leverage (crypto) (20x vs 10x); cooldown (6h vs 24h)`, or `Rules: no rule broken`, or `Rules: none set yet`.
4. A broken rule is a FAIL flag. The analysis still runs and still reports. The user decides. Never soften a broken rule or suggest raising the limit.
5. If a rule is unknown because an input is missing, say which input (F1).

## Formulas
```
open_risk_pct = (Σ |entry − stop| × size of open linear positions + Σ long-option premiums) / equity × 100 + plan risk %
theme_gross   = (Σ |entry × size| of open positions in the theme + |plan notional|) / equity × 100
hours_since_loss = now − close time of the most recent trade with negative R in trades.md
```

**Worked example** (from the tests): rules max crypto leverage 10x, max risk 1%, max open risk 3%, min weighted R 2.0, cooldown 24h, 2 trades/day; one open BCH position risking 2.4% of $10,000; last loss closed 6 hours ago; 2 trades opened today. Plan: BTC long 20x, risk 1.5%, weighted R 1.8, no stop. Result: **breaks** leverage (20x vs 10x), risk per trade (1.5% vs 1%), open risk (3.9% vs 3%), crypto gross (232% vs 200%), weighted R (1.8 vs 2.0), stop required, liquidation odds (64% vs 10%), cooldown (6h vs 24h), and trades per day (3 vs 2). Positions pass (2 vs 3).

## Output format
The one-line summary at the top of the report. The full table goes in the report when any rule breaks.

## Common failure modes
- Inventing limits for unset rules. `null` means "not set" and is reported that way.
- Skipping the check because the setup grades A. Rules come first.
- Stale positions or trades files: the cooldown and open-risk checks are only as current as `positions.json` and `trades.md`.

## Missing inputs (AGENT.md fallback)
- No rules block, or everything `null`: "Rules: none set yet", and point to the block.
- No equity: the % rules show "unknown" (F1).
