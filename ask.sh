#!/usr/bin/env bash
# Ask the trading agent from Git Bash / macOS / Linux.
#   ./ask.sh "BTC long, 20x, entry 98,400"          quick check
#   ./ask.sh --agent "BTC long, 20x, entry 98,400"  full Claude Code agent
cd "$(dirname "$0")"
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
exec "$PY" app/ask.py "$@"
