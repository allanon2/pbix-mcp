"""#323 (#248, #252): TRUNC of an Int64 is an Int64, as
ROUND, ROUNDUP, FLOOR, CEILING, MROUND, ABS and INT of one are.

What was wrong (0.9.147): TRUNC divided by 10 ** digits in floats, so
TRUNC(5) was the Double 5.

Expected values: Power BI Desktop 2.152's g() cells over ADOMD (type letter +
FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim): build_b248c.py
h_type_int; TRUNC(-1.5) is the Double -1 (build_b254b.py f_fns).
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
    # build_b248c.py h_type_int
    ("ROUND(5, -1)", "I10.000000000"),
    ("TRUNC(5)", "I5.000000000"),
    ("ROUNDUP(5, 0)", "I5.000000000"),
    ("FLOOR(5, 2)", "I4.000000000"),
    ("CEILING(5, 2)", "I6.000000000"),
    ("MROUND(5, 2)", "I6.000000000"),
    ("ABS(-5)", "I5.000000000"),
    ("INT(5)", "I5.000000000"),
    # build_b254b.py f_fns
    ("TRUNC(-1.5)", "D-1.000000000"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_cells(expr, desktop):
    assert _ev(g(expr)) == desktop
