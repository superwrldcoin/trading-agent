import http.client
import json
import subprocess
import threading

import pytest

from app import agent, server


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setattr(agent, "run_quick", lambda text, e=None, r=None: {"ok": True, "markdown": f"QUICK {text} {e} {r}", "seconds": 0.1})
    monkeypatch.setattr(agent, "run_agent", lambda text: {"ok": True, "markdown": f"AGENT {text}", "seconds": 1.0})
    s = server.make_server(0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s.server_address[1]
    s.shutdown()
    s.server_close()


def request(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"127.0.0.1:{port}"}
    h.update(headers or {})
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


OK = {"Content-Type": "application/json", "X-Trading-Agent": "1"}


def test_index_and_health(srv):
    status, body = request(srv, "GET", "/")
    assert status == 200 and b"Trading Agent" in body
    status, body = request(srv, "GET", "/api/health")
    assert status == 200 and "claude_cli" in json.loads(body)


def test_quick_and_agent_endpoints(srv):
    status, body = request(srv, "POST", "/api/quick", {"text": "BTC long 20x", "equity": "10000", "risk_pct": "1"}, OK)
    assert status == 200 and json.loads(body)["markdown"] == "QUICK BTC long 20x 10000.0 1.0"
    status, body = request(srv, "POST", "/api/agent", {"text": "BTC long"}, OK)
    assert status == 200 and json.loads(body)["markdown"] == "AGENT BTC long"


@pytest.mark.parametrize("headers, body, code", [
    ({"Content-Type": "application/json"}, {"text": "x"}, 403),                       # missing custom header
    ({"X-Trading-Agent": "1", "Content-Type": "text/plain"}, {"text": "x"}, 403),      # not JSON
    (dict(OK, Origin="http://evil.example"), {"text": "x"}, 403),                     # foreign origin
    (dict(OK, Host="evil.example:8765"), {"text": "x"}, 403),                         # DNS-rebinding host
    (OK, {"text": "  "}, 400),
    (OK, {"text": "BTC", "equity": "lots"}, 400),
])
def test_post_guards(srv, headers, body, code):
    status, _ = request(srv, "POST", "/api/quick", body, headers)
    assert status == code


def test_preflight_rejected(srv):
    status, _ = request(srv, "OPTIONS", "/api/agent", None, {"Origin": "http://evil.example"})
    assert status == 403


def test_build_prompt_sanitizes_and_mentions_rules():
    p = agent.build_prompt('BTC long "20x"\n entry 98,400')
    assert "BTC long '20x' entry 98,400" in p
    assert "never ask" in p and "tools/quick_check.py" in p and "log_entry.py" in p


def test_run_agent_without_cli(monkeypatch):
    monkeypatch.setattr(agent, "claude_path", lambda: None)
    res = agent.run_agent("BTC long")
    assert not res["ok"] and "not found" in res["markdown"]


def test_run_agent_invokes_claude_headless(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw["cwd"]
        return subprocess.CompletedProcess(cmd, 0, stdout="REPORT", stderr="")
    monkeypatch.setattr(agent, "claude_path", lambda: "claude")
    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    res = agent.run_agent("BTC long 20x")
    assert res["ok"] and res["markdown"] == "REPORT"
    assert seen["cmd"][:2] == ["claude", "-p"] and "--allowedTools" in seen["cmd"]
    assert seen["cwd"] == agent.ROOT
    assert not any("WebFetch" in c or c == "Bash" for c in seen["cmd"])


def test_run_agent_reports_failure(monkeypatch):
    monkeypatch.setattr(agent, "claude_path", lambda: "claude")
    monkeypatch.setattr(agent.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in"))
    res = agent.run_agent("BTC long")
    assert not res["ok"] and "not logged in" in res["markdown"]


def test_run_quick_handles_parse_error():
    res = agent.run_quick("long 20x")
    assert not res["ok"] and "Couldn't read" in res["markdown"]


def test_run_agent_explains_untrusted_workspace(monkeypatch):
    monkeypatch.setattr(agent, "claude_path", lambda: "claude")
    monkeypatch.setattr(agent.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr="Ignoring 11 permissions.allow entries: this workspace has not been trusted."))
    res = agent.run_agent("BTC long")
    assert not res["ok"] and "One-time setup" in res["markdown"] and "claude" in res["markdown"]


def test_credit_error_is_reported_even_with_trust_warning(monkeypatch):
    monkeypatch.setattr(agent, "claude_path", lambda: "claude")
    monkeypatch.setattr(agent.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout="Credit balance is too low", stderr="Ignoring 11 permissions.allow entries: not been trusted"))
    res = agent.run_agent("BTC long")
    assert not res["ok"] and "no credit" in res["markdown"] and "One-time setup" not in res["markdown"]


def test_agent_env_drops_api_key_and_nested_session_vars(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("TRADING_AGENT_KEEP_API_KEY", raising=False)
    env = agent.agent_env()
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env
    monkeypatch.setenv("TRADING_AGENT_KEEP_API_KEY", "1")
    assert agent.agent_env()["ANTHROPIC_API_KEY"] == "sk-test"


def test_run_agent_passes_clean_env(monkeypatch):
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("TRADING_AGENT_KEEP_API_KEY", raising=False)
    monkeypatch.setattr(agent, "claude_path", lambda: "claude")

    def fake_run(cmd, **kw):
        seen["env"] = kw["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="REPORT", stderr="")
    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    assert agent.run_agent("BTC long")["ok"] and "ANTHROPIC_API_KEY" not in seen["env"]


def test_run_agent_parses_json_cost_and_denials(monkeypatch):
    payload = {"type": "result", "subtype": "success", "is_error": False, "result": "## REPORT",
               "total_cost_usd": 0.4321, "num_turns": 9,
               "permission_denials": [{"tool_name": "WebSearch"}, {"tool_name": "WebSearch"}]}
    monkeypatch.setattr(agent, "claude_path", lambda: "claude")
    monkeypatch.setattr(agent.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout=json.dumps(payload), stderr="Ignoring 11 permissions.allow entries: not been trusted"))
    res = agent.run_agent("BTC long")
    assert res["ok"] and res["markdown"].startswith("## REPORT")
    assert res["cost_usd"] == 0.4321 and res["denied"] == ["WebSearch"]
    assert "cost $0.43 in Claude credits" in res["markdown"] and "move WebSearch" in res["markdown"]


def test_run_agent_json_error_reports_credit(monkeypatch):
    payload = {"type": "result", "is_error": True, "result": "Credit balance is too low", "total_cost_usd": 0}
    monkeypatch.setattr(agent, "claude_path", lambda: "claude")
    monkeypatch.setattr(agent.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout=json.dumps(payload), stderr=""))
    res = agent.run_agent("BTC long")
    assert not res["ok"] and "no credit" in res["markdown"]


def test_prompt_includes_event_check_and_websearch_allowed():
    assert "WebSearch" in agent.ALLOWED_TOOLS
    assert "Event check" in agent.build_prompt("BTC long")


def test_portfolio_endpoint(srv, monkeypatch):
    monkeypatch.setattr(agent, "run_portfolio", lambda: {"ok": True, "markdown": "PORTFOLIO", "seconds": 0.1})
    status, body = request(srv, "POST", "/api/portfolio", {"text": "portfolio"}, OK)
    assert status == 200 and json.loads(body)["markdown"] == "PORTFOLIO"


def test_run_portfolio_empty(monkeypatch, tmp_path):
    from tools import positions
    monkeypatch.setattr(positions, "POSITIONS_PATH", tmp_path / "none.json")
    res = agent.run_portfolio()
    assert res["ok"] and "No open positions" in res["markdown"]

def test_static_and_config(srv):
    for p, ct in (("/static/app.js", b"EventSource"), ("/static/app.css", b"--accent"), ("/static/report.js", b"const Report"),
                  ("/static/home.js", b"window.Home = Home"), ("/static/fx.js", b"window.FX = FX")):
        status, body = request(srv, "GET", p)
        assert status == 200 and ct in body
    assert request(srv, "GET", "/static/../server.py")[0] == 404
    status, body = request(srv, "GET", "/manifest.webmanifest")
    assert status == 200 and json.loads(body)["display"] == "standalone"
    status, body = request(srv, "GET", "/sw.js")
    assert status == 200 and b"/api/" in body
    assert request(srv, "GET", "/static/pwa.js")[0] == 200 and request(srv, "GET", "/static/icon.svg")[0] == 200
    status, body = request(srv, "GET", "/api/config")
    assert status == 200 and "watchlist" in json.loads(body)


def test_job_stream_and_history(srv, monkeypatch, tmp_path):
    from app.jobs import JobManager
    monkeypatch.setattr(server, "JOBS", JobManager(tmp_path))

    def fake_quick(text, e=None, r=None, progress=None):
        progress("fetch", "Fetching")
        return {"ok": True, "markdown": f"JOB {text}", "seconds": 0.1}
    monkeypatch.setattr(agent, "run_quick", fake_quick)
    status, body = request(srv, "POST", "/api/run", {"kind": "quick", "text": "BTC long"}, OK)
    jid = json.loads(body)["id"]
    status, body = request(srv, "GET", f"/api/jobs/{jid}/events")
    events = [json.loads(l[6:]) for l in body.decode().splitlines() if l.startswith("data: ")]
    assert status == 200 and [e["type"] for e in events if e["type"] != "tick"][-1] == "done"
    assert any(e.get("id") == "fetch" for e in events)
    assert events[-1]["result"]["markdown"] == "JOB BTC long"
    status, body = request(srv, "GET", "/api/history")
    items = json.loads(body)["items"]
    assert items and items[0]["id"] == jid
    status, body = request(srv, "GET", f"/api/history/{jid}")
    assert json.loads(body)["markdown"] == "JOB BTC long"
    assert request(srv, "GET", "/api/history/..%2f..")[0] == 404


def test_history_delete_and_clear(srv, monkeypatch, tmp_path):
    from app.jobs import JobManager
    jm = JobManager(tmp_path)
    monkeypatch.setattr(server, "JOBS", jm)
    monkeypatch.setattr(agent, "run_quick", lambda text, e=None, r=None, progress=None:
                        {"ok": True, "markdown": "M", "seconds": 0.1})
    ids = []
    for t in ("a", "b"):
        jid = json.loads(request(srv, "POST", "/api/run", {"kind": "quick", "text": t}, OK)[1])["id"]
        request(srv, "GET", f"/api/jobs/{jid}/events")
        ids.append(jid)
    assert request(srv, "POST", f"/api/history/{ids[0]}/delete", {"x": 1}, OK)[0] == 200
    assert request(srv, "GET", f"/api/history/{ids[0]}")[0] == 404
    assert request(srv, "POST", "/api/history/clear", {"x": 1}, {"X-Trading-Agent": "1"})[0] == 403
    status, body = request(srv, "POST", "/api/history/clear", {"x": 1}, OK)
    assert status == 200 and "deleted" in json.loads(body)
    assert json.loads(request(srv, "GET", "/api/history")[1])["items"] == []
    assert not list(tmp_path.glob("*.json")) and not list(tmp_path.glob("*.meta"))


def test_privacy_route(srv):
    status, body = request(srv, "GET", "/privacy")
    assert status == 200 and b"Privacy" in body


def test_run_guards_and_cancel(srv, monkeypatch, tmp_path):
    from app.jobs import JobManager
    monkeypatch.setattr(server, "JOBS", JobManager(tmp_path))
    release = threading.Event()
    monkeypatch.setattr(agent, "run_portfolio", lambda: (release.wait(5), {"ok": True, "markdown": "late"})[1])
    assert request(srv, "POST", "/api/run", {"kind": "bogus", "text": "x"}, OK)[0] == 400
    assert request(srv, "POST", "/api/run", {"kind": "quick", "text": "x"}, {"X-Trading-Agent": "1"})[0] == 403
    jid = json.loads(request(srv, "POST", "/api/run", {"kind": "portfolio", "text": "portfolio"}, OK)[1])["id"]
    status, body = request(srv, "POST", f"/api/jobs/{jid}/cancel", {"text": "cancel"}, OK)
    assert json.loads(body)["ok"] is True
    status, body = request(srv, "GET", f"/api/jobs/{jid}/events")
    assert b'"cancelled"' in body and b'"done"' not in body
    release.set()
    assert request(srv, "GET", "/api/jobs/nope/events")[0] == 404


def test_paper_endpoints_use_a_private_store_and_need_the_header(srv, tmp_path, monkeypatch):
    import pandas as pd
    from tools import paper
    store = tmp_path / "paper.json"
    idx = pd.date_range("2026-11-05 09:00", periods=4, freq="15min", tz="UTC")
    frame = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}, index=idx)

    class B(paper.Broker):
        def bars(self, symbol, bars=700):
            return frame
    for name in ("api_status", "api_preview", "api_open", "api_close", "api_cancel", "api_reset"):
        fn = getattr(paper, name)
        if name == "api_status":
            monkeypatch.setattr(paper, name, lambda f=fn: f(store, B()))
        elif name in ("api_preview", "api_open", "api_close"):
            monkeypatch.setattr(paper, name, lambda a, f=fn: f(a, store, B()))
        else:
            monkeypatch.setattr(paper, name, lambda a, f=fn: f(a, store))
    monkeypatch.setattr(paper, "quote", lambda sym, broker=None, f=paper.quote: f(sym, B()))

    ticket = {"symbol": "btc/usdt", "side": "long", "stop": "95", "targets": ["110"], "risk_pct": "1", "leverage": "2"}
    assert request(srv, "POST", "/api/paper/open", ticket, {"Content-Type": "application/json"})[0] == 403
    status, body = request(srv, "POST", "/api/paper/preview", ticket, OK)
    assert status == 200 and json.loads(body)["preview"]["weighted_R"] == pytest.approx(2, abs=0.05)
    status, body = request(srv, "POST", "/api/paper/open", ticket, OK)
    t = json.loads(body)
    assert status == 200 and t["id"] == "PT-001" and t["status"] == "open"
    status, body = request(srv, "GET", "/api/paper")
    snap = json.loads(body)
    assert status == 200 and snap["account"]["start_equity"] == 10_000 and snap["trades"][0]["mark"] == 100
    status, body = request(srv, "POST", "/api/paper/close", {"id": "PT-001"}, OK)
    assert status == 200 and json.loads(body)["status"] == "closed"
    assert request(srv, "POST", "/api/paper/close", {"id": "PT-001"}, OK)[0] == 400       # already closed
    assert request(srv, "POST", "/api/paper/open", dict(ticket, stop="abc"), OK)[0] == 400
    assert request(srv, "POST", "/api/paper/reset", {"equity": "x"}, OK)[0] == 400
    assert json.loads(request(srv, "POST", "/api/paper/reset", {"equity": 5000}, OK)[1])["start_equity"] == 5000
    assert request(srv, "POST", "/api/paper/nope", {}, OK)[0] == 404
    status, body = request(srv, "GET", "/api/paper/quote?symbol=btc/usdt")
    assert status == 200 and json.loads(body)["price"] == 100
    status, body = request(srv, "GET", "/static/paper.js")
    # app.js switches views via window.Paper; a bare top-level const is invisible there (Analyze tab got stuck on Paper)
    assert status == 200 and b"window.Paper = Paper" in body
    assert "MES" in json.loads(request(srv, "GET", "/api/config")[1])["contracts"]


def test_quick_result_carries_a_paper_plan():
    res = {"symbol": "BTC/USDT", "side": "long", "entry": 100.0, "stop": 95.0,
           "targets": [{"price": 105.0, "source": "PDH"}, {"price": 110.0, "source": "PWH"}], "request": {"leverage": 5.0}}
    assert agent.paper_plan(res) == {"symbol": "BTC/USDT", "side": "long", "entry": 100.0, "stop": 95.0,
                                     "targets": [105.0, 110.0], "leverage": 5.0}
    assert agent.paper_plan(dict(res, side=None)) is None


def test_settings_rules_switch_export_import(srv):
    from tools import store
    status, body = request(srv, "GET", "/api/settings")
    assert status == 200 and json.loads(body)["settings"]["rules_enabled"] is True
    assert json.loads(request(srv, "GET", "/api/config")[1])["rules_enabled"] is True
    assert request(srv, "POST", "/api/settings", {"settings": {"rules_enabled": False}}, {"Content-Type": "application/json"})[0] == 403
    assert request(srv, "POST", "/api/settings", {"settings": "nope"}, OK)[0] == 400
    status, body = request(srv, "POST", "/api/settings", {"settings": {"rules_enabled": 0, "ui": {"theme": "dark"}}}, OK)
    assert status == 200 and json.loads(body)["settings"] == {"rules_enabled": False, "ui": {"theme": "dark"}}
    assert json.loads(request(srv, "GET", "/api/config")[1])["rules_enabled"] is False      # not cached
    store.put_doc("paper", {"next_id": 7})
    status, body = request(srv, "GET", "/api/export")
    dump = json.loads(body)
    assert status == 200 and dump["docs"]["paper"] == {"next_id": 7} and dump["settings"]["rules_enabled"] is False
    store.put_doc("paper", {"next_id": 99})
    status, body = request(srv, "POST", "/api/import", {"data": dump}, OK)
    assert status == 200 and json.loads(body)["docs"] == ["paper"] and store.get_doc("paper") == {"next_id": 7}
    assert request(srv, "POST", "/api/import", {"data": {"format": "other"}}, OK)[0] == 400


def test_remote_token_gate(srv, monkeypatch):
    monkeypatch.setattr(server, "TOKEN", "t" * 24)
    tok = "t" * 24
    assert request(srv, "GET", "/api/health")[0] == 401
    assert request(srv, "GET", "/")[0] == 401
    assert request(srv, "GET", "/manifest.webmanifest")[0] == 200       # install metadata stays open
    assert request(srv, "GET", "/?token=wrong")[0] == 403
    c = http.client.HTTPConnection("127.0.0.1", srv, timeout=5)
    c.request("GET", f"/?token={tok}", headers={"Host": "phone.lan:1"})
    r = c.getresponse()
    r.read()
    cookie = r.getheader("Set-Cookie")
    assert r.status == 302 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    c.close()
    sess = {"Cookie": f"ta_token={tok}", "Host": "phone.lan:1"}
    assert request(srv, "GET", "/api/health", None, sess)[0] == 200
    assert request(srv, "POST", "/api/quick", {"text": "BTC long"}, dict(OK, **sess))[0] == 200
    assert request(srv, "POST", "/api/quick", {"text": "x"}, dict(OK, **sess, Origin="http://evil.example"))[0] == 403
    assert request(srv, "POST", "/api/quick", {"text": "x"}, dict(OK, Host="phone.lan:1"))[0] == 401
