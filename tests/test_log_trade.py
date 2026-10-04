import pandas as pd
import pytest

from tools import log_trade as lt


def bars(rows, start="2026-10-01 12:00"):
    idx = pd.date_range(start, periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, columns=["high", "low"], index=idx).assign(open=0.0, close=0.0, volume=1.0)


def test_compute_linear_r_and_fees():
    r = lt.compute_linear("long", [(98400, 0.1), (98000, 0.1)], [(99500, 0.2)], stop=97000, fees=None,
                          fee_rate=0.0005, funding=1.0)
    assert r["entry"] == pytest.approx(98200) and r["exit"] == pytest.approx(99500)
    assert r["gross"] == pytest.approx(260.0)
    assert r["fees"] == pytest.approx((98200 + 99500) * 0.2 * 0.0005)
    assert r["net"] == pytest.approx(260 - r["fees"] - 1)
    assert r["R"] == pytest.approx(r["net"] / (1200 * 0.2)) and r["fees_estimated"]
    short = lt.compute_linear("short", [(100, 1)], [(104, 1)], stop=105, fees=0, fee_rate=0, funding=0)
    assert short["R"] == pytest.approx(-0.8)


def test_compute_linear_validation():
    with pytest.raises(ValueError, match="differ"):
        lt.compute_linear("long", [(100, 2)], [(110, 1)], 95, 0, 0, 0)
    with pytest.raises(ValueError, match="wrong side"):
        lt.compute_linear("long", [(100, 1)], [(110, 1)], 105, 0, 0, 0)
    assert lt.compute_linear("long", [(100, 1)], [(110, 1)], None, 0, 0, 0)["R"] is None


def test_compute_option():
    r = lt.compute_option("long", 2, 100, 23.20, 31.00, fees=2.6)
    assert r["gross"] == pytest.approx(1560) and r["risk"] == pytest.approx(4640) and r["R"] == pytest.approx((1560 - 2.6) / 4640)
    assert lt.compute_option("short", 1, 100, 5, 2, 0)["R"] is None


def test_excursions_mae_mfe_and_t1_order():
    b = bars([(100.5, 99.0), (101.0, 98.0), (103.0, 100.0), (99.0, 96.0)])
    o, c = b.index[0], b.index[-1]
    e = lt.excursions(b, "long", 100.0, 97.0, 102.0, o, c)
    assert e["mae_R"] == pytest.approx(-4 / 3) and e["mfe_R"] == pytest.approx(1.0) and e["t1_first"] == "yes"  # last bar low 96
    e2 = lt.excursions(b, "long", 100.0, 98.5, 102.0, o, c)
    assert e2["t1_first"] == "no"                       # bar 2 low 98.0 hits the 98.5 stop first
    e3 = lt.excursions(bars([(103.0, 96.0)]), "long", 100.0, 97.0, 102.0, *bars([(1, 1)]).index[[0, 0]])
    assert e3["t1_first"] == "same bar (ambiguous)"
    assert lt.excursions(b, "long", 100, 97, None, c + pd.Timedelta(days=1), c + pd.Timedelta(days=2))["bars"] == 0


def test_record_ids_and_format(tmp_path):
    p = tmp_path / "trades.md"
    p.write_text("# Trades\n\n## T-000: <ASSET> template\n\n_No trades yet._\n", encoding="utf-8")
    base = {"symbol": "BTC/USDT", "side": "long", "instrument": "perp", "opened": pd.Timestamp("2026-10-01 12:00", tz="UTC"),
            "closed": pd.Timestamp("2026-10-03 08:00", tz="UTC"), "thesis": "PWH retest", "thesis_recalled": True,
            "session": None, "prob": 55, "entries": [(98400, 0.2)], "exits": [(99500, 0.2)], "stop": 97000,
            "leverage": 20, "margin": "isolated", "mae_R": -0.4, "mfe_R": 1.1, "t1_first": "yes",
            **lt.compute_linear("long", [(98400, 0.2)], [(99500, 0.2)], 97000, 10.0, 0, 0)}
    assert lt.record(base, p) == "T-001"
    assert lt.record(base, p) == "T-002"
    text = p.read_text(encoding="utf-8")
    assert "_No trades yet._" not in text
    assert "## T-001: BTC/USDT long, opened 2026-10-01 12:00 UTC, closed 2026-10-03 08:00 UTC" in text
    assert "Thesis (recalled after the fact): \"PWH retest\"" in text
    assert "- Result: +0.75R, +210.00 net; MAE -0.40R, MFE +1.10R" in text
    # rules_check can read what log_trade writes
    from tools import rules_check
    hist = rules_check.trade_history(p)
    assert [h["id"] for h in hist] == ["T-001", "T-002"] and hist[0]["R"] == pytest.approx(0.75)


def test_update_session_outcome(tmp_path):
    p = tmp_path / "sessions.md"
    p.write_text("# Sessions\n\n### S-1/BTCUSDT | BTC/USDT long | grade B\n- Logged: x\n- Outcome: open\n\n"
                 "### S-1/SI | SI short | grade A\n- Outcome: open\n", encoding="utf-8")
    assert lt.update_session_outcome("S-1/BTCUSDT", "T-001, +0.75R, T1 before stop: yes", p)
    text = p.read_text(encoding="utf-8")
    assert "- Outcome: T-001, +0.75R, T1 before stop: yes" in text and text.count("- Outcome: open") == 1
    assert not lt.update_session_outcome("S-9/XX", "x", p)


def test_cli_validation(capsys):
    assert lt.main(["--symbol", "DOGE", "--side", "long", "--entry", "1", "--exit", "2", "--size", "1",
                    "--opened", "2026-10-01", "--closed", "2026-10-02"]) == 2
    assert "not on the watchlist" in capsys.readouterr().out
    assert lt.main(["--symbol", "MSFT", "--side", "long", "--instrument", "option", "--opened", "2026-10-01",
                    "--closed", "2026-10-02"]) == 2
    assert "options need" in capsys.readouterr().out
