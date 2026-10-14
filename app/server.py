"""Local web interface for the trading agent (Python standard library only).

  python app/server.py [--port 8765]     then open http://127.0.0.1:8765

Listens on 127.0.0.1 only. POST endpoints require a custom header and a JSON body, check Host and Origin,
and never answer CORS preflights, so other websites in your browser can't trigger them.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import agent  # noqa: E402
from app.jobs import JobManager  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
STATIC = {"/static/app.css": "text/css; charset=utf-8", "/static/app.js": "application/javascript; charset=utf-8"}
JOBS = JobManager()


def _read(rel: str) -> bytes:
    p = (APP_DIR / rel).resolve()
    if APP_DIR not in p.parents:
        raise FileNotFoundError(rel)
    return p.read_bytes()


HEADER = "X-Trading-Agent"
MAX_BODY = 10_000


class Handler(BaseHTTPRequestHandler):
    server_version = "TradingAgent/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet except errors
        if not str(args[1] if len(args) > 1 else "").startswith(("2", "3")):
            super().log_message(fmt, *args)

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self._allowed_hosts()

    def _sse(self, job) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        i = 0
        try:
            while True:
                evs, done = job.wait_events(i)
                for ev in evs:
                    self.wfile.write(b"data: " + json.dumps(ev).encode("utf-8") + b"\n\n")
                i += len(evs)
                if not evs:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                if done:
                    return
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _config(self) -> dict:
        from tools import levels, rules_check
        cfg = {"watchlist": sorted(levels.WATCHLIST), "claude_cli": bool(agent.claude_path()), "rules": None}
        try:
            cfg["rules"] = rules_check.load_rules()
        except Exception:  # rules are informational in the UI
            pass
        return cfg

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"error": "bad host"})
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, _read("index.html"), "text/html; charset=utf-8")
        if path in STATIC:
            return self._send(200, _read(path.lstrip("/")), STATIC[path])
        if path == "/api/health":
            return self._json(200, {"ok": True, "claude_cli": bool(agent.claude_path())})
        if path == "/api/config":
            return self._json(200, self._config())
        if path == "/api/history":
            return self._json(200, {"items": JOBS.history()})
        if path.startswith("/api/history/"):
            item = JOBS.history_item(path.rsplit("/", 1)[1])
            return self._json(200, item) if item else self._json(404, {"error": "not found"})
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "events":
            job = JOBS.get(parts[2])
            return self._sse(job) if job else self._json(404, {"error": "no such job"})
        return self._json(404, {"error": "not found"})

    def do_OPTIONS(self):  # no CORS: cross-origin preflights fail
        self._json(403, {"error": "cross-origin requests are not allowed"})

    def do_POST(self):
        if not self._host_ok():
            return self._json(403, {"error": "bad host"})
        origin = self.headers.get("Origin")
        if origin and origin.replace("http://", "") not in self._allowed_hosts():
            return self._json(403, {"error": "bad origin"})
        if self.headers.get(HEADER) != "1" or "application/json" not in self.headers.get("Content-Type", ""):
            return self._json(403, {"error": f"missing {HEADER} header or JSON body"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": "request too large"})
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = str(payload.get("text", "")).strip()
        except (ValueError, AttributeError):
            return self._json(400, {"error": "invalid JSON"})
        if not text:
            return self._json(400, {"error": "empty request"})
        parts = self.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            return self._json(200, {"ok": JOBS.cancel(parts[2])})
        num = lambda k: float(payload[k]) if payload.get(k) not in (None, "") else None
        if self.path == "/api/run":
            kind = str(payload.get("kind", ""))
            if kind not in ("quick", "agent", "portfolio"):
                return self._json(400, {"error": "kind must be quick, agent or portfolio"})
            try:
                equity, risk = num("equity"), num("risk_pct")
            except (TypeError, ValueError):
                return self._json(400, {"error": "equity / risk_pct must be numbers"})
            return self._json(200, {"id": JOBS.start(kind, text, equity, risk).id})
        if self.path == "/api/quick":
            try:
                equity, risk = num("equity"), num("risk_pct")
            except (TypeError, ValueError):
                return self._json(400, {"error": "equity / risk_pct must be numbers"})
            return self._json(200, agent.run_quick(text, equity, risk))
        if self.path == "/api/agent":
            return self._json(200, agent.run_agent(text))
        if self.path == "/api/portfolio":
            return self._json(200, agent.run_portfolio())
        return self._json(404, {"error": "not found"})


def make_server(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Local web interface for the trading agent.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    srv = make_server(a.port)
    url = f"http://127.0.0.1:{a.port}"
    print(f"Trading agent interface: {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
