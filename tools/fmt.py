"""Number formatting shared by the report tools. No project imports, so any tool can use it.

Prices under 10 (XRP, MCL ticks in cents, small ATRs) need more than 2 decimals to be usable as levels, so the
default precision widens to 4 decimals below 10.
"""
from __future__ import annotations

import math


def num(x, nd: int = 2) -> str:
    """'n/a' for None/NaN; otherwise thousands separators and `nd` decimals (4 when nd is the default 2 and |x| < 10)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if nd == 2 and 0 < abs(x) < 10:
        nd = 4
    return f"{x:,.{nd}f}"
