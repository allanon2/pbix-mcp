"""#316: FORMAT of a number whose formatted digits pass 28
no longer raises decimal.InvalidOperation.

What was wrong (0.9.147): FORMAT's rounding ran in Python's default 28-digit
decimal context, so FORMAT(1E19, "0.000000000") and FORMAT(1E30, "0") raised
decimal.InvalidOperation and failed the whole measure.

Expected values: Power BI Desktop 2.152's texts over ADOMD, build_b254b.py
h_fmt_big cells 0 and 1.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext({}, {}, None, None, None, []))


@pytest.mark.parametrize("expr, desktop", [
    ('FORMAT(1E19, "0.000000000")', "10000000000000000000.000000000"),
    ('FORMAT(1E30, "0")', "1000000000000000000000000000000"),
], ids=["1E19", "1E30"])
def test_desktop_texts(expr, desktop):
    assert _ev(expr) == desktop
