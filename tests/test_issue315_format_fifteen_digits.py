"""#315: FORMAT of a Double with a custom number format shows
its 15 significant digits; an Int64 and a Currency show their exact digits.

What was wrong (0.9.147): the digits came from the Double's repr (up to 17):
FORMAT(0.1 + 0.2, "0.00000000000000000000") was 0.30000000000000004000,
FORMAT(-214891469123651.7205, "0.000000000") -214891469123651.720000000, and
an Int64 was converted to a Double first (FORMAT(123456789012345678, "0")
123456789012345680).

Expected values: Power BI Desktop 2.152's texts over ADOMD: build_b254b.py
h_fmt_big cells 2, 3, 5, 6, 7 (t(x) = IFERROR((x) & "", "E")); build_b254.py
o_scalar cell 2 (CURRENCY(5E14) + CURRENCY(5E14) through
FORMAT(CONVERT(x, DOUBLE), "0.000000000"): the Double -844674407370955.125
shows -844674407370955.000000000); build_b248c.py g_text2 cell 0.

Desktop's b248g answer (test_desktop_b248g_p_fmt, build_b248g.py p_fmt
verbatim): 1234,5678|1234567890123456|-123456789012345678|
1.23456789012346E+17|5 -- an Int64 past 15 digits in full, a Double past
15 digits as its 15-digit text, de-DE's comma.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext({}, {}, None, None, None, []))


CASES = [
    ('FORMAT(123456789012345678, "0")', "123456789012345678"),
    ('FORMAT(0.1 + 0.2, "0.00000000000000000000")', "0.30000000000000000000"),
    ('FORMAT(1 / 3, "0.00000000000000000000")', "0.33333333333333300000"),
    ('FORMAT(-214891469123651.7205, "0.000000000")', "-214891469123652.000000000"),
    ('FORMAT(2 / 3, "0.0000000000000000")', "0.6666666666666670"),
    ('FORMAT(CONVERT(CURRENCY(5E14) + CURRENCY(5E14), DOUBLE), "0.000000000")', "-844674407370955.000000000"),
    ('FORMAT(1E19, "General Number")', "1E+19"),
    # build_b248c.py g_text2 cell 0: an Int64's General Number is its exact
    # digits too (it was the Double's 1.23456789012346E+17)
    ('FORMAT(123456789012345678, "General Number")', "123456789012345678"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_texts(expr, desktop):
    assert _ev(expr) == desktop


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


def test_desktop_b248g_p_fmt():
    # build_b248g.py p_fmt, Power BI Desktop 2.152 (desk_b248g.json)
    expr = ' & "|" & '.join([_t248('FORMAT(1234.5678, "General Number", "de-DE")'),
                             _t248('FORMAT(1234567890123456, "General Number")'),
                             _t248('FORMAT(-123456789012345678, "General Number")'),
                             _t248('FORMAT(123456789012345678.0, "General Number")'),
                             _t248('FORMAT(5, "General Number", "de-DE")')])
    assert _ev(expr) == "1234,5678|1234567890123456|-123456789012345678|1.23456789012346E+17|5"
