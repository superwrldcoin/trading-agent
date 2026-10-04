import math

import pytest

from tools import options_math as om


def test_textbook_values():
    # Hull, S=42 K=40 r=10% sigma=20% T=0.5: call 4.76, put 0.81
    assert om.price("call", 42, 40, 0.5, 0.10, 0.20) == pytest.approx(4.7594, abs=1e-3)
    assert om.price("put", 42, 40, 0.5, 0.10, 0.20) == pytest.approx(0.8086, abs=1e-3)


def test_put_call_parity():
    S, K, T, r, s, q = 100, 105, 0.75, 0.04, 0.3, 0.01
    c, p = om.price("call", S, K, T, r, s, q), om.price("put", S, K, T, r, s, q)
    assert c - p == pytest.approx(S * math.exp(-q * T) - K * math.exp(-r * T), abs=1e-9)


def test_greeks_signs_and_known_values():
    g = om.greeks("call", 42, 40, 0.5, 0.10, 0.20)
    assert g["delta"] == pytest.approx(0.7791, abs=1e-3)
    assert g["gamma"] > 0 and g["vega"] > 0 and g["theta"] < 0
    gp = om.greeks("put", 42, 40, 0.5, 0.10, 0.20)
    assert gp["delta"] == pytest.approx(g["delta"] - 1, abs=1e-9)
    assert gp["gamma"] == pytest.approx(g["gamma"]) and gp["vega"] == pytest.approx(g["vega"])


def test_greeks_match_finite_differences():
    S, K, T, r, s = 100, 100, 0.25, 0.03, 0.25
    g = om.greeks("call", S, K, T, r, s)
    h = 0.01
    fd_delta = (om.price("call", S + h, K, T, r, s) - om.price("call", S - h, K, T, r, s)) / (2 * h)
    fd_vega = (om.price("call", S, K, T, r, s + 0.0001) - om.price("call", S, K, T, r, s - 0.0001)) / 0.0002 / 100
    fd_theta = (om.price("call", S, K, T - 1 / 365, r, s) - om.price("call", S, K, T, r, s))
    assert g["delta"] == pytest.approx(fd_delta, abs=1e-5)
    assert g["vega"] == pytest.approx(fd_vega, abs=1e-5)
    assert g["theta"] == pytest.approx(fd_theta, abs=2e-3)


def test_implied_vol_roundtrip_and_bounds():
    p = om.price("put", 100, 95, 0.4, 0.04, 0.37)
    assert om.implied_vol("put", p, 100, 95, 0.4, 0.04) == pytest.approx(0.37, abs=1e-5)
    assert om.implied_vol("call", 0.0001, 100, 200, 0.1, 0.04) is None or om.implied_vol("call", 0.0001, 100, 200, 0.1, 0.04) < 1
    assert om.implied_vol("call", 150, 100, 95, 0.4, 0.04) is None  # above max possible value


def test_expiry_and_expected_move():
    assert om.price("call", 110, 100, 0, 0.04, 0.3) == 10 and om.price("put", 110, 100, 0, 0.04, 0.3) == 0
    assert om.greeks("put", 90, 100, 0, 0.04, 0.3)["delta"] == -1.0
    assert om.expected_move(100, 0.32, 0.25) == pytest.approx(16.0)
