"""In-memory job runner for the web interface: each run gets an id and an event list that the server streams as SSE.

Events are dicts: {"type": "step"|"tick"|"done"|"cancelled", ...}. Quick checks report real steps through the
`progress` callback; the full agent and portfolio runs report start/finish plus elapsed ticks.
Cancelling marks the job cancelled and discards its result (a running `claude -p` call is not killed; it ends at its timeout).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from app import agent

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "tools" / "output" / "history"
MAX_JOBS = 50
MAX_HISTORY = 100
KINDS = ("quick", "agent", "portfolio")


class Job:
    def __init__(self, kind: str, text: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.text = kind, text
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.finished = False
        self.cancelled = False
        self.t0 = time.time()

    def emit(self, ev: dict) -> None:
        with self.cond:
            ev["t"] = round(time.time() - self.t0, 1)
            self.events.append(ev)
            if ev["type"] in ("done", "cancelled"):
                self.finished = True
            self.cond.notify_all()

    def wait_events(self, start: int, timeout: float = 15.0) -> tuple[list[dict], bool]:
        """Block until there are events past `start`, the job finishes, or timeout (heartbeat)."""
        with self.cond:
            if len(self.events) <= start and not self.finished:
                self.cond.wait(timeout)
            return self.events[start:], self.finished


class JobManager:
    def __init__(self, history_dir: Path | None = None):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.history_dir = history_dir or HISTORY_DIR

    def start(self, kind: str, text: str, equity=None, risk_pct=None) -> Job:
        if kind not in KINDS:
            raise ValueError("unknown job kind")
        job = Job(kind, text)
        with self.lock:
            if len(self.jobs) >= MAX_JOBS:
                for jid in [j for j, v in self.jobs.items() if v.finished][: MAX_JOBS // 2]:
                    del self.jobs[jid]
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, equity, risk_pct), daemon=True).start()
        return job

    def get(self, jid: str) -> Job | None:
        return self.jobs.get(jid)

    def cancel(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if not job or job.finished:
            return False
        job.cancelled = True
        job.emit({"type": "cancelled"})
        return True

    def _run(self, job: Job, equity, risk_pct) -> None:
        stop = threading.Event()

        def ticker():
            while not stop.wait(1.0):
                job.emit({"type": "tick"})
        threading.Thread(target=ticker, daemon=True).start()
        try:
            if job.kind == "quick":
                job.emit({"type": "step", "id": "start", "label": "Starting quick check"})
                res = agent.run_quick(job.text, equity, risk_pct,
                                      progress=lambda sid, label: job.emit({"type": "step", "id": sid, "label": label}))
            elif job.kind == "agent":
                job.emit({"type": "step", "id": "agent", "label": "Full agent running (tools, skills, event check)"})
                res = agent.run_agent(job.text)
            else:
                job.emit({"type": "step", "id": "portfolio", "label": "Valuing positions and stress testing"})
                res = agent.run_portfolio()
        except Exception as exc:  # surface any failure to the UI instead of a silent dead job
            res = {"ok": False, "markdown": f"**Run failed:** {exc}", "seconds": round(time.time() - job.t0, 1)}
        finally:
            stop.set()
        if job.cancelled:
            return
        self._save(job, res)
        job.emit({"type": "done", "result": res})

    def _save(self, job: Job, res: dict) -> None:
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
            rec = {"id": job.id, "kind": job.kind, "text": job.text, "utc": stamp, "ok": res.get("ok"),
                   "seconds": res.get("seconds"), "cost_usd": res.get("cost_usd"), "markdown": res.get("markdown", "")}
            (self.history_dir / f"{stamp}_{job.id}.json").write_text(json.dumps(rec), encoding="utf-8")
            files = sorted(self.history_dir.glob("*.json"))
            for old in files[:-MAX_HISTORY]:
                old.unlink()
        except OSError:
            pass  # history is a convenience; never fail a run because of it

    def history(self, limit: int = 30) -> list[dict]:
        if not self.history_dir.exists():
            return []
        out = []
        for f in sorted(self.history_dir.glob("*.json"), reverse=True)[:limit]:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({k: d.get(k) for k in ("id", "kind", "text", "utc", "ok", "seconds", "cost_usd")})
            except (OSError, ValueError):
                continue
        return out

    def history_item(self, jid: str) -> dict | None:
        if not jid.isalnum():
            return None
        for f in self.history_dir.glob(f"*_{jid}.json") if self.history_dir.exists() else []:
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return None
