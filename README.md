# trading-agent

An **analysis-only** trading research agent built on Claude Code. It reads 4H market structure off
previous day/week/month liquidity levels (PDH/PDL, PWH/PWL, PMthH/PMthL), grades conviction with
**EMA + VWAP**, and checks position math (R:R, liquidation, sizing), all from live public data.
It never places trades and never asks for or stores API keys.

Watchlist: BTC/USDT, BCH/USDT (OKX), XAU/USD (OKX Tether Gold proxy, cross-checked against spot),
SI (COMEX silver futures), GLD, MSFT (yfinance).

> Not financial advice. Outputs are analysis; you make and execute every decision.

## Ask it something

```text
BTC long, 20x, entry 98,400, how does it look?
```

| Interface | How | What runs |
|---|---|---|
| Web page | double-click `start-ui.cmd` (or `python app/server.py`), then open http://127.0.0.1:8765 | **Quick check** (no AI, ~30s on first fetch) or **Full agent** (Claude Code with every skill and memory file, a few minutes) |
| Terminal | `.\ask.ps1 "BTC long 20x entry 98,400"` / `./ask.sh ...`, add `-Agent` / `--agent` for the full agent | same |
| Claude Code | `cd trading-agent && claude`, then type the question | the agent itself (interactive) |

The **quick check** parses the request, fetches 15M/1H/4H/1D data, and returns:
- a verdict
- EMA + VWAP conviction for the side
- structure
- a stop and targets derived from structure (or yours)
- a milestone table with R, P&L/ROE, and liquidation
- flags, such as liquidation hit before the stop, an entry far from the market, or R:R under 2

The **full agent** runs the quick check, then applies the skills, adds the event check and a
`P(T1 before stop)` judgment, and logs the session.

The full agent needs the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) logged in on the machine.
Run `claude` once inside this folder and accept the trust prompt. No API key is stored.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows (Git Bash: source .venv/Scripts/activate); macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pytest                        # all tests are offline
```

## Layout

| Path | What |
|---|---|
| `CLAUDE.md`, `AGENT.md` | Agent rules: role, write scope, tools and fallbacks, session workflow, report format |
| `skills/` | Procedures with worked examples: report-format, levels-and-entries, multi-timeframe-momentum (EMA + VWAP conviction), market-structure, risk-and-sizing, leveraged-position-math, data-ingest, macro-and-catalysts, post-mortem |
| `memory/` | Durable facts (`core.md`), preferences, playbook/journal (memory protocol), per-market notes. `trades.md` and `sessions.md` are private and gitignored |
| `tools/` | `quick_check.py`, `fetch_prices.py`, `levels.py`, `indicators.py`, `position_calc.py`, `verify.py`, `log_entry.py` |
| `app/` | Local web server, terminal entry point, headless agent runner |
| `tests/` | pytest suite (network mocked) |

## Data sources

All public, read-only, keyless: OKX market data, yfinance, and Swissquote's public quote feed (gold
spot cross-check). Binance and Bybit are blocked from the author's location. `tools/verify.py`
rebuilds 4H bars from 15M/5M data to check the pipeline.
