"""Shared entry points for the interface: quick check (no AI) and full agent (Claude Code, headless).

The full agent runs `claude -p` inside this repo, so it loads CLAUDE.md -> AGENT.md, memory/, and skills/
exactly like an interactive session. It uses the Claude Code login already on this machine: no API key is
stored or requested (CLAUDE.md: never request or store API keys). It can only run the project's tools and
read files; it can't ask follow-up questions, so it applies the AGENT.md fallback rules instead.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import fetch_prices, quick_check  # noqa: E402

AGENT_TIMEOUT = 900  # seconds
ALLOWED_TOOLS = [
    "Read", "Glob", "Grep",
    "Bash(.venv/Scripts/python tools/*.py *)", "Bash(.venv/bin/python tools/*.py *)", "Bash(python tools/*.py *)",
    "Edit(/tools/output/**)",  # Edit rules cover every file-writing tool
]

AGENT_PROMPT = """The user typed this request into the local trading-agent interface:

"{text}"

Follow CLAUDE.md and AGENT.md exactly. The user cannot answer follow-up questions in this mode, so never ask:
apply the AGENT.md fallback rules (F1-F6) and state any assumption in one [ASSUMPTION] line.

1. Start by running the quick check on the request: `{python} tools/quick_check.py "{text}"`
2. Then run whatever else the request needs (levels.py, indicators.py, position_calc.py with the
   setup's numbers) and apply the skills: market-structure, multi-timeframe-momentum (EMA + VWAP
   conviction), levels-and-entries, risk-and-sizing, leveraged-position-math, report-format.
3. Reply with the complete report in the skills/report-format.md style: Data line, structure, conviction
   grade, milestone table (include liquidation if leverage was given), Notes, P(T1 before stop) with a
   one-line reason, "What would change this", MEMORY_UPDATE blocks or "Memory: none", and the
   disclaimer once at the end.
4. Log it with tools/log_entry.py (one entry; use --side none --grade none with a --note if there is
   no valid setup). Don't edit any other file.
"""


def venv_python() -> str:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        if (ROOT / rel).exists():
            return rel.replace(".exe", "")
    return "python"


def build_prompt(text: str) -> str:
    clean = " ".join(text.replace('"', "'").split())[:500]
    return AGENT_PROMPT.format(text=clean, python=venv_python())


def run_quick(text: str, equity: float | None = None, risk_pct: float | None = None) -> dict:
    """Returns {"ok", "markdown", "seconds"}; never raises for user-facing errors."""
    t0 = time.time()
    try:
        res = quick_check.run(text, equity=equity, risk_pct=risk_pct)
        md = quick_check.render(res)
        ok = True
    except quick_check.ParseError as exc:
        md, ok = f"**Couldn't read that request:** {exc}\n\nTry e.g. `BTC long, 20x, entry 98,400`.", False
    except fetch_prices.FetchError as exc:
        md, ok = f"**FETCH FAILED:** {exc}", False
    return {"ok": ok, "markdown": md, "seconds": round(time.time() - t0, 1)}


AUTH_ERRORS = [
    ("credit balance is too low", "**Claude account has no credit.** Claude Code is billing an API key with an empty "
     "balance. If `ANTHROPIC_API_KEY` is set on purpose, add credit to that key. Otherwise remove the variable "
     "(the interface already drops it unless TRADING_AGENT_KEEP_API_KEY=1) and log in with `claude` → `/login`."),
    ("invalid api key", "**Claude rejected the API key.** Log in with `claude` → `/login`, or fix the key."),
    ("please run /login", "**Claude Code isn't logged in.** Run `claude` once and use `/login`."),
]


def agent_env() -> dict:
    """Environment for the headless agent.

    ANTHROPIC_API_KEY takes priority over Claude Code's own login. A stale or empty-balance key in the
    environment would make every run fail with "Credit balance is too low", so it's dropped unless
    TRADING_AGENT_KEEP_API_KEY=1. Nested-session variables from a parent Claude Code session are dropped too.
    """
    env = dict(os.environ)
    if env.get("TRADING_AGENT_KEEP_API_KEY") != "1":
        env.pop("ANTHROPIC_API_KEY", None)
    for k in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET",
              "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_PID"):
        env.pop(k, None)
    return env


def claude_path() -> str | None:
    return shutil.which("claude")


def run_agent(text: str, timeout: int = AGENT_TIMEOUT) -> dict:
    """Run the full agent headlessly. Returns {"ok", "markdown", "seconds"}."""
    exe = claude_path()
    if not exe:
        return {"ok": False, "seconds": 0, "markdown": "**Claude Code CLI not found.** Install it (`npm i -g "
                "@anthropic-ai/claude-code` or the native installer) and log in once with `claude`, then retry."}
    cmd = [exe, "-p", build_prompt(text), "--output-format", "text", "--allowedTools", *ALLOWED_TOOLS]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=agent_env())
    except subprocess.TimeoutExpired:
        return {"ok": False, "seconds": timeout, "markdown": f"**Agent timed out after {timeout}s.** Try the quick check."}
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        err = (proc.stderr or "").strip()[-1500:]
        secs = round(time.time() - t0, 1)
        both = f"{out}\n{err}".lower()
        for needle, msg in AUTH_ERRORS:
            if needle in both:
                return {"ok": False, "seconds": secs, "markdown": msg + "\n\n(The quick check works without this.)"}
        if "not been trusted" in err:
            return {"ok": False, "seconds": secs, "markdown":
                    "**One-time setup needed:** Claude Code hasn't trusted this folder yet. Open a terminal, run\n\n"
                    f"```\ncd {ROOT}\nclaude\n```\n\naccept the trust prompt, type `/exit`, then try again. "
                    "(The quick check works without this.)"}
        return {"ok": False, "seconds": secs,
                "markdown": f"**Agent failed (exit {proc.returncode}).**\n\n```\n{(out + chr(10) + err).strip() or 'no output'}\n```"}
    return {"ok": True, "markdown": out, "seconds": round(time.time() - t0, 1)}
