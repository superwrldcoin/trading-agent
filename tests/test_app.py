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
