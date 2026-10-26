"""#322 (#248): DIVIDE's alternate result is of the
division's type -- an Int64 alternate beside a Double division (1 / 0, 1.5 /
0) is a Double; TRUE stays TRUE.

What was wrong (0.9.147, and the first 0.9.148 implementation): an Int64
alternate stayed an Int64 (DIVIDE(1, 0, 5) was I5), and a BLANK denominator
returned the alternate as it was.

Expected values: Power BI Desktop 2.152's g() cells over ADOMD (type letter +
FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim): build_b248c.py
e_divide_alt; build_b254b.py c_divide cells 1, 2 (DIVIDE(1, 0, CURRENCY(2.5))
D2.5, DIVIDE(4, 2, CURRENCY(1)) D2).

Desktop's b248g answer (test_desktop_b248g_p_divide, build_b248g.py
p_divide verbatim): D5 C5 S7 -- DIVIDE by BLANK takes the alternate as a
Double (a Currency beside a Currency numerator), and text stays text.
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
    # build_b248c.py e_divide_alt
    ("DIVIDE(1, 0, 5)", "D5.000000000"),
    ("DIVIDE(1.5, 0, 5)", "D5.000000000"),
    ("DIVIDE(1, 0, TRUE())", "L1.000000000"),
    ("DIVIDE(CURRENCY(1), 0, 5)", "C5.000000000"),
    # build_b254b.py c_divide
    ("DIVIDE(1, 0, CURRENCY(2.5))", "D2.500000000"),
    ("DIVIDE(4, 2, CURRENCY(1))", "D2.000000000"),
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


def test_desktop_b248g_p_divide():
    # build_b248g.py p_divide, Power BI Desktop 2.152 (desk_b248g.json)
    expr = _cells248(_g248("DIVIDE(1, BLANK(), 5)"), _g248("DIVIDE(CURRENCY(1), BLANK(), 5)"),
                     _g248('DIVIDE(1, 0, "7")'))
    assert _ev(expr) == "D5.000000000 C5.000000000 S7.000000000"
