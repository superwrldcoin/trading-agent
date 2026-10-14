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
    for p, ct in (("/static/app.js", b"EventSource"), ("/static/app.css", b"--accent")):
        status, body = request(srv, "GET", p)
        assert status == 200 and ct in body
    assert request(srv, "GET", "/static/../server.py")[0] == 404
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
