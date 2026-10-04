import pandas as pd
import pytest

from tools import log_entry as le

NOW = pd.Timestamp("2026-10-04 19:40", tz="UTC")


def setup_entry(**kw):
    e = dict(request="Full watchlist 4H analysis", symbol="BCH/USDT", side="long", grade="B",
             zone=[296.30, 298.96], stop=293.64, targets=[308.60, 322.60, 366.10], prob=35,
             tools=["levels.py", "indicators.py"], report="tools/output/2026-10-04_1856_session.md")
    e.update(kw)
    return e


def test_append_creates_file_with_header_and_entry(tmp_path):
    path = tmp_path / "sessions.md"
    e = le.append(setup_entry(), path=path, now=NOW)
    text = path.read_text(encoding="utf-8")
    assert e["id"] == "S-20261004-1940/BCHUSDT"
    assert text.startswith("# Sessions")
    assert "### S-20261004-1940/BCHUSDT | BCH/USDT long | grade B" in text
    assert "- Zone: 296.30-298.96 | Stop: 293.64 | Targets: 308.60, 322.60, 366.10" in text
    assert "- P(T1 before stop): 35% [JUDGMENT]" in text
    assert "- Logged: 2026-10-04 19:40 UTC" in text and "- Outcome: open" in text


def test_entries_group_by_session_and_append(tmp_path):
    path = tmp_path / "sessions.md"
    first = le.append(setup_entry(), path=path, now=NOW)
    le.append(setup_entry(symbol="MSFT", side="none", grade="none", zone=None, stop=None, targets=None,
                          prob=None, note="R:R 0.27 fails"), path=path, now=NOW, )
    text = path.read_text(encoding="utf-8")
    assert text.count("# Sessions") == 1 and text.count("### S-20261004-1940/") == 2
    assert "| MSFT | no valid setup" in text and "- Outcome: n/a" in text
    assert first["session"] == "S-20261004-1940"


def test_duplicate_entry_rejected(tmp_path):
    path = tmp_path / "sessions.md"
    le.append(setup_entry(), path=path, now=NOW)
    with pytest.raises(ValueError, match="already logged"):
        le.append(setup_entry(), path=path, now=NOW)


def test_short_setup_ordering(tmp_path):
    le.append(setup_entry(symbol="XAU/USD", side="short", zone=[4207.05, 4221.60], stop=4236.16,
                          targets=[4143.70, 4117.50], prob=40), path=tmp_path / "s.md", now=NOW)
    with pytest.raises(ValueError, match="stop .* beyond the zone"):
        le.append(setup_entry(symbol="SI", side="short", zone=[62.00, 62.46], stop=62.20, targets=[60.38]),
                  path=tmp_path / "s.md", now=NOW)


@pytest.mark.parametrize("kw, msg", [
    (dict(grade="D"), "grade must be"),
    (dict(prob=None), "needs --prob"),
    (dict(prob=120), "between 0 and 100"),
    (dict(stop=297.0), "beyond the zone"),
    (dict(targets=[297.0]), "targets must be beyond"),
    (dict(grade="none"), "needs a grade"),
    (dict(side="none"), "must have grade 'none'"),
    (dict(side="none", grade="none", note=None), "need --note"),
    (dict(request="  "), "request is required"),
    (dict(zone=None), "needs zone, stop and targets"),
])
def test_validation(tmp_path, kw, msg):
    with pytest.raises(ValueError, match=msg):
        le.append(setup_entry(**kw), path=tmp_path / "s.md", now=NOW)
    assert not (tmp_path / "s.md").exists()  # nothing written on error


def test_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(le, "SESSIONS_PATH", tmp_path / "memory" / "sessions.md")
    monkeypatch.setattr(le, "ROOT", tmp_path)
    args = ["--request", "test", "--symbol", "BTC/USDT", "--side", "long", "--grade", "A", "--zone", "84000", "84500",
            "--stop", "83000", "--targets", "86000", "87400", "--prob", "50", "--session", "S-TEST"]
    assert le.main(args) == 0
    assert "Logged S-TEST/BTCUSDT" in capsys.readouterr().out
    assert le.main(args) == 2  # duplicate
    assert "already logged" in capsys.readouterr().out
