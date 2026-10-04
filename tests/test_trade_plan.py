import pytest

from tools import trade_plan as tp


def bch(**kw):
    stages = tp.build_stages("long", (296.30, 298.96), None, 24.0815)
    base = dict(side="long", stages=stages, stop=293.64, targets=[308.60, 322.60, 366.10], leverage=10,
                equity=10000)
    base.update(kw)
    return tp.plan(**base)


def test_stage_rows_avg_liq_and_r():
    p = bch()
    s1, s2, s3 = p["stages"]
    assert s1["avg_entry"] == pytest.approx(298.96) and s1["cum_size"] == pytest.approx(0.3 * 24.0815)
    assert s2["avg_entry"] == pytest.approx((298.96 + 297.63) / 2)
    assert s3["avg_entry"] == pytest.approx(297.497, abs=1e-3)
    assert s3["liquidation"] == pytest.approx(s3["avg_entry"] * (1 - 0.1 + 0.005))
    assert s3["risk_to_stop"] == pytest.approx(100.0, abs=0.05)     # matches position_calc sizing at 1%
    assert [round(r, 2) for r in s3["R"]] == [2.88, 6.51, 17.79] and s3["weighted_R"] == pytest.approx(7.31, abs=0.01)
    assert s1["weighted_R"] < s3["weighted_R"]                      # deeper fills improve R
    assert p["flags"] == []


def test_size_from_risk_includes_fees():
    stages = [(px, w) for px, w in tp.pc.tranches("long", 296.30, 298.96)]
    size = tp.size_from_risk("long", stages, 293.64, 10000, 1, 0.0005)
    assert size == pytest.approx(24.0815, abs=1e-3)


def test_ladder_breakeven_and_trailing():
    p = bch(atr=5.32, trail_atr=1.5)
    t1, t2, t3 = p["ladder"]
    assert t1["stop_after"] == pytest.approx(p["stages"][-1]["avg_entry"]) and t1["remaining_risk"] == 0
    assert t2["stop_after"] == pytest.approx(322.60 - 1.5 * 5.32) and t2["locked_in"] > 0
    assert t3["remaining"] == pytest.approx(0) and t3["realized"] > t2["realized"] > t1["realized"] > 0
    no_be = bch(breakeven_after=None)
    assert no_be["ladder"][0]["stop_after"] == 293.64 and no_be["ladder"][0]["remaining_risk"] > 0


def test_pyramid_trap_add_beyond_earlier_liquidation():
    p = tp.plan("long", [(100.0, 1), (93.0, 1)], stop=90.0, targets=[110.0], leverage=20)   # liq after stage 1: 95.5
    assert any(l == "fail" and "stage 2" in m and "liquidated before this add" in m for l, m in p["flags"])
    assert any("hit before the stop" in m for _, m in p["flags"])


def test_short_plan_and_addon_checks():
    stages = tp.build_stages("short", (4207.05, 4221.60), None, 3.0)
    p = tp.plan("short", stages, 4236.16, [4143.70, 4117.50], leverage=5, addon=(4150.0, 2.5))
    assert p["stages"][-1]["avg_entry"] == pytest.approx(4215.05, abs=0.01)
    assert p["addon"]["stop"] == pytest.approx(p["stages"][-1]["avg_entry"])   # breakeven after T1
    msgs = " ".join(m for _, m in p["flags"])
    assert "more than 50%" in msgs
    chase = tp.plan("short", stages, 4236.16, [4143.70, 4117.50], addon=(4110.0, 1.0))
    assert "chasing" in " ".join(m for _, m in chase["flags"])


@pytest.mark.parametrize("kw, msg", [
    (dict(stop=297.0), "beyond every entry stage"),
    (dict(targets=[297.0]), "targets must be beyond"),
    (dict(weights=[0.5, 0.5]), "weights must match"),
])
def test_invalid(kw, msg):
    with pytest.raises(ValueError, match=msg):
        bch(**kw)


def test_cli_render(capsys):
    args = ["--asset", "BCH/USDT", "--side", "long", "--stages", "298.96:7.2", "297.63:7.2", "296.30:9.6",
            "--stop", "293.64", "--targets", "308.60", "322.60", "--leverage", "10"]
    assert tp.main(args) == 0
    out = capsys.readouterr().out
    assert "| After stage |" in out and "Take-profit ladder" in out
    assert tp.main(["--asset", "X", "--side", "long", "--zone", "1", "2", "--stop", "0.5", "--targets", "3"]) == 2
