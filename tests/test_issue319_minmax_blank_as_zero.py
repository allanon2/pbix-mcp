"""#319 (#248): two-argument MIN / MAX read a BLANK beside a
number as 0, and answer BLANK when that 0 is the result.

What was wrong (0.9.147): any BLANK argument made the result BLANK, so
MAX(1, BLANK()) was BLANK where Desktop answers 1.

Expected values: Power BI Desktop 2.152's g() cells over ADOMD (type letter +
FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim):
build_b254b.py c_minmax_blank and build_b254.py f_minmax cell 4; a tie of a
number 0 with the BLANK is the number, in either order (build_b248c.py
c_minmax_tie), where the first implementation let the first argument win
and MIN(BLANK(), 0) was BLANK.

Desktop's b248g answer (test_desktop_b248g_p_minmax, build_b248g.py
p_minmax verbatim): D0 C0 D0 -- the other order of the ties, as the rule
gives.
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
    ("MAX(CURRENCY(0.1), BLANK())", "C0.100000000"),
    ("MAX(1, BLANK())", "I1.000000000"),
    ("MIN(1, BLANK())", "B"),
    ("MIN(BLANK(), -1)", "I-1.000000000"),
    ("MAX(BLANK(), -1)", "B"),
    ("MAX(BLANK(), BLANK())", "B"),
    ("MIN(CURRENCY(0.1), BLANK())", "B"),
    # build_b248c.py c_minmax_tie
    ("MIN(0, BLANK())", "I0.000000000"),
    ("MIN(BLANK(), 0)", "I0.000000000"),
    ("MAX(0, BLANK())", "I0.000000000"),
    ("MAX(BLANK(), 0)", "I0.000000000"),
    ("MIN(0.0, BLANK())", "D0.000000000"),
    ("MIN(CURRENCY(0), BLANK())", "C0.000000000"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_cells(expr, desktop):
    assert _ev(g(expr)) == desktop


def _tc248(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def _g248(x):
    return f'IF(ISERROR({x}), "E", {_tc248(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def _t248(x):
    return f'IFERROR(({x}) & "", "E")'


def _cells248(*xs):
    return ' & " " & '.join(xs)


def test_desktop_b248g_p_minmax():
    # build_b248g.py p_minmax, Power BI Desktop 2.152 (desk_b248g.json)
    expr = _cells248(_g248("MIN(BLANK(), 0.0)"), _g248("MAX(BLANK(), CURRENCY(0))"), _g248("MAX(0.0, BLANK())"))
    assert _ev(expr) == "D0.000000000 C0.000000000 D0.000000000"
