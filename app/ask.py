"""Terminal interface.

  python app/ask.py "BTC long, 20x, entry 98,400, how does it look?"          quick check (~30s first run, cached 3 min; no AI)
  python app/ask.py --agent "BTC long, 20x, entry 98,400, how does it look?"  full agent (minutes, Claude Code)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent  # noqa: E402


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Ask the trading agent.")
    ap.add_argument("text", nargs="+")
    ap.add_argument("--agent", action="store_true", help="run the full Claude Code agent instead of the quick check")
    ap.add_argument("--equity", type=float)
    ap.add_argument("--risk-pct", type=float)
    a = ap.parse_args(argv)
    text = " ".join(a.text)
    res = agent.run_agent(text) if a.agent else agent.run_quick(text, a.equity, a.risk_pct)
    print(res["markdown"])
    print(f"\n({'agent' if a.agent else 'quick check'}, {res['seconds']}s)")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
