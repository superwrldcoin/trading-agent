"""Local web interface for the trading agent (Python standard library only).

  python app/server.py [--port 8765]     then open http://127.0.0.1:8765

Listens on 127.0.0.1 only. POST endpoints require a custom header and a JSON body, check Host and Origin,
and never answer CORS preflights, so other websites in your browser can't trigger them.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import socket
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
STATIC = {"/static/app.css": "text/css; charset=utf-8", "/static/app.js": "application/javascript; charset=utf-8",
          "/static/report.js": "application/javascript; charset=utf-8",
          "/static/paper.js": "application/javascript; charset=utf-8",
          "/static/home.js": "application/javascript; charset=utf-8",
          "/static/fx.js": "application/javascript; charset=utf-8",
          "/static/pwa.js": "application/javascript; charset=utf-8",
          "/static/icon.svg": "image/svg+xml", "/static/icon-maskable.svg": "image/svg+xml"}
# Served from the root so the service worker's scope covers the whole app: URL path -> file under app/static/
ROOT_FILES = {"/sw.js": ("static/sw.js", "application/javascript; charset=utf-8"),
              "/manifest.webmanifest": ("static/manifest.webmanifest", "application/manifest+json")}
JOBS = JobManager()
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
_FILES: dict[str, tuple[bytes, str]] = {}
_CONFIG: dict = {}


def _read(rel: str) -> bytes:
    p = (APP_DIR / rel).resolve()
    if APP_DIR not in p.parents:
        raise FileNotFoundError(rel)
    return p.read_bytes()


def _cached(rel: str) -> tuple[bytes, str]:
    """Static files are read once per mtime and served with an ETag so the browser can revalidate cheaply."""
    p = APP_DIR / rel
    mtime = p.stat().st_mtime_ns
    hit = _FILES.get(rel)
    if hit is None or hit[1].strip('"').split("-")[0] != str(mtime):
        body = _read(rel)
        hit = (body, f'"{mtime}-{len(body)}"')
        _FILES[rel] = hit
    return hit


def _clear_cache() -> int:
    d = ROOT / "tools" / "output" / "cache"
    n = 0
    for f in d.glob("*") if d.exists() else []:
        if f.is_file():
            f.unlink(missing_ok=True)
            n += 1
    return n


TOKEN: str | None = None      # set by --remote: every request except the PWA icons/manifest needs it
COOKIE = "ta_token"
OPEN_PATHS = {"/manifest.webmanifest", "/static/icon.svg", "/static/icon-maskable.svg"}
HEADER = "X-Trading-Agent"
MAX_BODY = 10_000
MAX_IMPORT = 20_000_000      # a backup file being restored (Settings → Import)


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
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel: str, ctype: str) -> None:
        body, etag = _cached(rel)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _host_ok(self) -> bool:
        if TOKEN:       # remote mode: the Host is whatever the phone typed; the token is the gate
            return bool(self.headers.get("Host"))
        return self.headers.get("Host", "") in self._allowed_hosts()

    def _token_ok(self) -> bool:
        if not TOKEN:
            return True
        cookies = dict(c.strip().partition("=")[::2] for c in self.headers.get("Cookie", "").split(";") if "=" in c)
        given = cookies.get(COOKIE, "") or self.headers.get("Authorization", "").removeprefix("Bearer ")
        return hmac.compare_digest(given.encode(), TOKEN.encode())

    def _login(self, path: str, query: str) -> bool:
        """GET /?token=... sets the session cookie and redirects to a clean URL. Returns True if it answered."""
        from urllib.parse import parse_qs
        given = (parse_qs(query).get("token") or [""])[0]
        if not TOKEN or not given:
            return False
        if not hmac.compare_digest(given.encode(), TOKEN.encode()):
            self._json(403, {"error": "bad token"})
            return True
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", f"{COOKIE}={TOKEN}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _sse(self, job) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
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
        if _CONFIG:
            return _CONFIG
        from tools import levels, rules_check, universe
        cfg = {"watchlist": sorted(levels.WATCHLIST), "claude_cli": bool(agent.claude_path()), "rules": None,
               "themes": universe.themes(),
               "contracts": {k: universe.contract(k) for k in levels.WATCHLIST if universe.contract(k)}}
        try:
            cfg["rules"] = rules_check.load_rules()
        except Exception:  # rules are informational in the UI
            pass
        _CONFIG.update(cfg)
        return cfg

    # ---- paper desk (simulated trades only: tools/paper.py; the user opens every trade) ----
    def _paper(self, fn) -> None:
        from tools import fetch_prices, paper
        try:
            return self._json(200, fn(paper))
        except paper.PaperError as exc:
            return self._json(400, {"error": str(exc)})
        except fetch_prices.FetchError as exc:
            return self._json(502, {"error": f"FETCH FAILED: {exc}"})

    def _paper_post(self, action: str, payload: dict) -> None:
        if action not in ("preview", "open", "close", "cancel", "reset"):
            return self._json(404, {"error": "not found"})
        if not isinstance(payload, dict):
            return self._json(400, {"error": "invalid JSON"})
        tid = str(payload.get("id", ""))

        def ticket():
            num = lambda v: None if v in (None, "") else float(v)
            try:
                return {"symbol": str(payload.get("symbol", "")).upper(), "side": payload.get("side"),
                        "stop": num(payload.get("stop")), "targets": [float(x) for x in payload.get("targets") or []],
                        "risk_pct": num(payload.get("risk_pct")), "size": num(payload.get("size")),
                        "contracts": num(payload.get("contracts")), "leverage": num(payload.get("leverage")) or 1.0,
                        "limit": num(payload.get("limit")), "p_t1": num(payload.get("p_t1")),
                        "note": str(payload.get("note") or ""), "source": str(payload.get("source") or "ticket")}
            except (TypeError, ValueError):
                from tools.paper import PaperError
                raise PaperError("ticket fields must be numbers")

        if action == "preview":
            return self._paper(lambda p: p.api_preview(ticket()))
        if action == "open":
            return self._paper(lambda p: p.api_open(ticket()))
        if action == "close":
            return self._paper(lambda p: p.api_close(tid))
        if action == "cancel":
            return self._paper(lambda p: p.api_cancel(tid))
        try:
            equity = float(payload.get("equity"))
        except (TypeError, ValueError):
            return self._json(400, {"error": "equity must be a number"})
        return self._paper(lambda p: p.api_reset(equity))

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"error": "bad host"})
        path, _, query = self.path.partition("?")
        if self._login(path, query):
            return
        if path not in OPEN_PATHS and not self._token_ok():
            return self._json(401, {"error": "access token required: open the URL printed by --remote"})
        if path in ("/", "/index.html"):
            return self._static("index.html", "text/html; charset=utf-8")
        if path in STATIC:
            return self._static(path.lstrip("/"), STATIC[path])
        if path in ROOT_FILES:
            return self._static(*ROOT_FILES[path])
        if path == "/privacy":
            try:
                body = (ROOT / "PRIVACY.md").read_bytes()
            except OSError:
                return self._json(404, {"error": "not found"})
            return self._send(200, body, "text/plain; charset=utf-8")
        if path == "/api/health":
            return self._json(200, {"ok": True, "claude_cli": bool(agent.claude_path())})
        if path == "/api/config":
            from tools import rules_check
            return self._json(200, {**self._config(), "rules_enabled": rules_check.enabled()})   # the switch is live
        if path == "/api/settings":
            from tools import store
            return self._json(200, {"settings": store.get_settings(), "store": {"path": str(store.path()),
                                                                                 "exists": store.path().exists()}})
        if path == "/api/export":
            from tools import store
            body = json.dumps(store.export(), indent=1).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", 'attachment; filename="trading-agent-export.json"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/history":
            return self._json(200, {"items": JOBS.history()})
        if path == "/api/paper":
            return self._paper(lambda p: p.api_status())
        if path == "/api/paper/quote":
            from urllib.parse import parse_qs
            sym = (parse_qs(self.path.partition("?")[2]).get("symbol") or [""])[0].upper()
            return self._paper(lambda p: p.quote(sym))
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
        if not self._token_ok():
            return self._json(401, {"error": "access token required"})
        origin = self.headers.get("Origin")
        allowed = {self.headers.get("Host", "")} if TOKEN else self._allowed_hosts()
        if origin and origin.split("://", 1)[-1] not in allowed:
            return self._json(403, {"error": "bad origin"})
        if self.headers.get(HEADER) != "1" or "application/json" not in self.headers.get("Content-Type", ""):
            return self._json(403, {"error": f"missing {HEADER} header or JSON body"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > (MAX_IMPORT if self.path == "/api/import" else MAX_BODY):
            return self._json(413, {"error": "request too large"})
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = str(payload.get("text", "")).strip()
        except (ValueError, AttributeError):
            return self._json(400, {"error": "invalid JSON"})
        parts = self.path.strip("/").split("/")
        if self.path == "/api/settings":       # the user's settings and switches (e.g. rules_enabled), kept in app.db
            from tools import store
            vals = payload.get("settings") if isinstance(payload, dict) else None
            if not isinstance(vals, dict) or len(vals) > 50:
                return self._json(400, {"error": "settings must be an object"})
            if "rules_enabled" in vals:
                vals["rules_enabled"] = bool(vals["rules_enabled"])
            try:
                return self._json(200, {"settings": store.set_settings(vals)})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
        if self.path == "/api/import":
            from tools import store
            try:
                return self._json(200, store.import_(payload.get("data")))
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
        if self.path == "/api/history/clear":
            return self._json(200, {"deleted": JOBS.clear_history(), "cache": _clear_cache()})
        if len(parts) == 4 and parts[:2] == ["api", "history"] and parts[3] == "delete":
            return self._json(200, {"ok": JOBS.delete_item(parts[2])})
        if len(parts) == 3 and parts[:2] == ["api", "paper"]:
            return self._paper_post(parts[2], payload)
        if not text:
            return self._json(400, {"error": "empty request"})
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


def make_server(port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))      # no packet is sent; picks the outgoing interface
            return sk.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Local web interface for the trading agent.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--remote", action="store_true",
                    help="listen on all interfaces for a phone/tablet; requires an access token (TA_TOKEN or generated)")
    a = ap.parse_args(argv)
    global TOKEN
    if a.remote:
        TOKEN = os.environ.get("TA_TOKEN") or secrets.token_urlsafe(24)
        if len(TOKEN) < 16:
            print("TA_TOKEN must be at least 16 characters.")
            return 2
    srv = make_server(a.port, "0.0.0.0" if a.remote else "127.0.0.1")
    url = f"http://127.0.0.1:{a.port}"
    print(f"Trading agent interface: {url}  (Ctrl+C to stop)")
    if a.remote:
        print(f"Phone (same network): http://{_lan_ip()}:{a.port}/?token={TOKEN}")
        print("Plain HTTP: anyone on this network can read it. For install-to-home-screen and safe use away from home,"
              " put HTTPS in front (e.g. `tailscale serve` or a Cloudflare tunnel) and open the same ?token= link.")
        a.no_browser = True
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
