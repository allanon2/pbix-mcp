"""#312: CONVERT(<date>, DOUBLE) is the date's serial, and
CONVERT(<date>, CURRENCY) its Currency.

What was wrong (0.9.147): CONVERT's DOUBLE branch took text and numbers only,
so CONVERT(DATE(2024, 1, 1) + 0.25, DOUBLE) was BLANK, and with it every
battery cell that prints a date-valued result as a number.

Expected values: Power BI Desktop 2.152's g() cells over ADOMD (type letter +
FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim): build_b254.py
t_mixed cells 4 and 5, build_b254b.py a_int cells 4, 8 and 9.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext({}, {}, None, None, None, []))


CASES = [
    ("CURRENCY(1.5) + DATE(2024, 1, 1)", "T45293.500000000"),
    ("DATE(2024, 1, 2) - CURRENCY(0.5)", "T45292.500000000"),
    ("CONVERT(DATE(2024, 1, 1), CURRENCY)", "C45292.000000000"),
    ("CONVERT(DATE(2024, 1, 1) + 0.25, DOUBLE)", "D45292.250000000"),
    ("CONVERT(TRUE(), CURRENCY)", "C1.000000000"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_cells(expr, desktop):
    assert _ev(g(expr)) == desktop
