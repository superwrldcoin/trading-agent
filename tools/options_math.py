"""Black-Scholes pricing, greeks, and implied volatility (European options, continuous dividend yield q).

Used by tools/options.py and tools/portfolio.py. American options (US equity/ETF options) are priced as
European: a fair approximation for calls without dividends and for short-dated puts; flagged where it matters.
"""
from __future__ import annotations

import math

SQRT2 = math.sqrt(2.0)


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / SQRT2))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1d2(S: float, K: float, T: float, r: float, sigma: float, q: float) -> tuple[float, float]:
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vt
    return d1, d1 - vt


def price(kind: str, S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """kind: 'call' or 'put'. T in years. At or after expiry, returns intrinsic value."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if kind == "call" else max(0.0, K - S)
    d1, d2 = _d1d2(S, K, T, r, sigma, q)
    if kind == "call":
        return S * math.exp(-q * T) * _cdf(d1) - K * math.exp(-r * T) * _cdf(d2)
    return K * math.exp(-r * T) * _cdf(-d2) - S * math.exp(-q * T) * _cdf(-d1)


def greeks(kind: str, S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> dict:
    """delta (per 1 underlying), gamma (per 1 underlying), theta (per calendar day), vega (per 1 vol point = 0.01),
    rho (per 1% rate)."""
    if T <= 0 or sigma <= 0:
        itm = (S > K) if kind == "call" else (S < K)
        return {"delta": (1.0 if itm else 0.0) * (1 if kind == "call" else -1), "gamma": 0.0, "theta": 0.0,
                "vega": 0.0, "rho": 0.0}
    d1, d2 = _d1d2(S, K, T, r, sigma, q)
    eq, er = math.exp(-q * T), math.exp(-r * T)
    gamma = eq * _pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * eq * _pdf(d1) * math.sqrt(T) / 100
    if kind == "call":
        delta = eq * _cdf(d1)
        theta = (-S * eq * _pdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * er * _cdf(d2) + q * S * eq * _cdf(d1)) / 365
        rho = K * T * er * _cdf(d2) / 100
    else:
        delta = -eq * _cdf(-d1)
        theta = (-S * eq * _pdf(d1) * sigma / (2 * math.sqrt(T)) + r * K * er * _cdf(-d2) - q * S * eq * _cdf(-d1)) / 365
        rho = -K * T * er * _cdf(-d2) / 100
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


def implied_vol(kind: str, premium: float, S: float, K: float, T: float, r: float, q: float = 0.0,
                lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-6) -> float | None:
    """Bisection IV. None if the premium is outside the no-arbitrage range."""
    if T <= 0 or premium <= 0:
        return None
    if not price(kind, S, K, T, r, lo, q) - tol <= premium <= price(kind, S, K, T, r, hi, q) + tol:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if price(kind, S, K, T, r, mid, q) > premium:
            hi = mid
        else:
            lo = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def expected_move(S: float, sigma: float, T: float) -> float:
    """One-standard-deviation move by expiry implied by sigma: S * sigma * sqrt(T)."""
    return S * sigma * math.sqrt(max(T, 0.0))
