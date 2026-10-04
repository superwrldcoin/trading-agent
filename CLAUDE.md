# Trading Agent

Python-based market research and trading agent. Pulls market data (yfinance, HTTP APIs), runs analysis with pandas/numpy, and keeps persistent notes per market.

## Layout

- `tools/` — Python modules the agent calls: data fetching, indicators, backtests, order helpers. Small functions you can import, no side effects when imported.
- `skills/` — Reusable workflows and playbooks (e.g. "screen a ticker", "run a backtest"), each in its own file or folder.
- `memory/` — Persistent agent memory. `memory/markets/<SYMBOL>.md` holds the notes, theses and past signals for each instrument.
- `tests/` — pytest suite mirroring `tools/` (`tools/foo.py` → `tests/test_foo.py`).
- `.claude/` — Claude Code project config (settings, commands, agents).

## Environment

Windows, Python 3.14, venv in `.venv`.

```bash
source .venv/Scripts/activate      # Git Bash
.venv\Scripts\Activate.ps1         # PowerShell
pip install -r requirements.txt    # once it exists
pytest                             # run tests
```

Dependencies: requests, pandas, numpy, yfinance, pytest. When you add a dependency, add it to `requirements.txt`.

## Rules

- **No live orders without explicit approval.** Default to research, paper trading, and backtests. Any code path that places real orders or moves money must be gated behind an explicit flag/config and confirmed by the user each time.
- **Never commit secrets.** API keys go in `.env` (gitignored) and are read from environment variables.
- **Tests must not hit the network.** Mock yfinance/requests in tests; use fixtures saved under `tests/fixtures/`.
- **Be explicit about time.** Store timestamps in UTC and keep timezone info on pandas indexes. Avoid look-ahead bias in backtests: signals at bar *t* may only use data available at *t*.
- **Don't invent numbers.** Prices, fundamentals, and backtest results must come from a fetched data source or a run, never from memory. Note the source and as-of date in `memory/markets/` entries.

## Conventions

- Type hints on public functions; return pandas objects with clear column names.
- Keep data fetching separate from analysis so analysis is testable on fixtures.
- Commit messages: short imperative summary (e.g. "add RSI indicator").
