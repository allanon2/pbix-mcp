"""#305 (#248): ROUND, ROUNDUP, ROUNDDOWN, INT, TRUNC, FLOOR,
CEILING, MROUND, ABS and MOD of a Fixed Decimal (Currency) are a Currency;
ROUNDUP / ROUNDDOWN of a Double a Double.

What was wrong (0.9.147): these functions worked on the float and returned a
Double (an Int64 for INT, FLOOR(x, 1), CEILING(x, 1)); ROUND rounded the
binary value, not the exact count; ROUNDUP(1.5, 0) was the Int64 2.

Expected values: Power BI Desktop 2.152's cells over ADOMD (build_b254.py
f_round, f_math, r_round; build_b254b.py f_fns; build_b248c.py h_fn_edges,
h_abs_min), each a g() cell = type letter + FORMAT(CONVERT(x, DOUBLE),
"0.000000000"), builder verbatim.

Desktop's b248g answer (test_desktop_b248g_p_cur_sig, build_b248g.py
p_cur_sig verbatim): C1.2345 C0.0003 E C0 I10 I-10 C1.5 C1.2345 --
MROUND / FLOOR / CEILING with a significance of no whole count, CEILING
past the range an error, MROUND(CURRENCY(0), 0.05) C0, TRUNC of an Int64
an Int64, MOD of two Currencies C1.5, as the engine answered.
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


def _ev(expr, tables=None):
    ctx = de.DAXContext(tables or {}, {}, None, None, None, [])
    return de.DAXEngine()._eval_expr(expr, ctx)


CASES = [
    # build_b254.py f_round
    ("ROUND(CURRENCY(1.2345), 2)", "C1.230000000"),
    ("ROUNDUP(CURRENCY(1.2341), 2)", "C1.240000000"),
    ("ROUNDDOWN(CURRENCY(-1.2349), 2)", "C-1.230000000"),
    ("INT(CURRENCY(-1.5))", "C-2.000000000"),
    ("TRUNC(CURRENCY(-1.5))", "C-1.000000000"),
    ("FLOOR(CURRENCY(1.2345), 0.01)", "C1.230000000"),
    ("CEILING(CURRENCY(1.2345), 0.01)", "C1.240000000"),
    ("MROUND(CURRENCY(1.2345), 0.05)", "C1.250000000"),
    # build_b254.py f_math
    ("ABS(CURRENCY(-1.5))", "C1.500000000"),
    ("SIGN(CURRENCY(-1.5))", "I-1.000000000"),
    ("MOD(CURRENCY(5.5), 2)", "C1.500000000"),
    ("POWER(CURRENCY(1.1), 2)", "D1.210000000"),
    ("SQRT(CURRENCY(2.25))", "D1.500000000"),
    # build_b254.py r_round: the exact count, half away from zero
    ("ROUND(CURRENCY(1.005), 2)", "C1.010000000"),
    ("ROUND(1.005, 2)", "D1.010000000"),
    ("ROUND(CURRENCY(2.675), 2)", "C2.680000000"),
    ("ROUND(2.675, 2)", "D2.680000000"),
    ("ROUND(CURRENCY(-2.5), 0)", "C-3.000000000"),
    ("ROUND(CURRENCY(0.0025), 3)", "C0.003000000"),
    ("ROUND(CURRENCY(1.2345), 2)", "C1.230000000"),
    # build_b254b.py f_fns
    ("FLOOR(CURRENCY(1.2345), 1)", "C1.000000000"),
    ("MOD(CURRENCY(5.5), 0.3)", "D0.100000000"),
    ("ROUND(CURRENCY(1234.5), -1)", "C1230.000000000"),
    ("TRUNC(CURRENCY(1.23456), 2)", "C1.230000000"),
    ("INT(2.5)", "I2.000000000"),
    ("TRUNC(-1.5)", "D-1.000000000"),
    ("ROUNDUP(1.5, 0)", "D2.000000000"),
    ("ABS(-2)", "I2.000000000"),
    ("CEILING(CURRENCY(1.2345), 1)", "C2.000000000"),
    ("ROUND(2.5, 0)", "D3.000000000"),
    # build_b248c.py h_fn_edges: MOD of an Int64 by a Currency is a Currency,
    # of a Double a Double; a significance of no whole count or past the
    # range rounds the Double and is a Currency (it was a Double, an error)
    ("MOD(5, CURRENCY(2))", "C1.000000000"),
    ("MOD(5.5, CURRENCY(2))", "D1.500000000"),
    ("MROUND(CURRENCY(1.2345), 0.00001)", "C1.234500000"),
    ("MROUND(CURRENCY(1.2345), 0.00005)", "C1.234500000"),
    ("CEILING(CURRENCY(1.2345), 0.00001)", "C1.234500000"),
    ("FLOOR(CURRENCY(1.2345), 1E16)", "C0.000000000"),
]


def test_abs_of_the_least_currency_is_an_error():
    # build_b248c.py h_abs_min: IFERROR((ABS(CURRENCY("-922337203685477.5808")))
    # & "", "E") is "E" -- no positive counterpart (its negation wraps,
    # build_b254b.py g_ovf); it wrapped to itself
    assert _ev('IFERROR((ABS(CURRENCY("-922337203685477.5808"))) & "", "E")') == "E"


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_cells(expr, desktop):
    assert _ev(g(expr)) == desktop


def test_product_of_a_currency_column_is_a_double():
    # build_b254b.py f_fns cell 8: PRODUCT(TCur3[v]) over 0.1, 0.1, 0.2 is D0.002
    tables = {"TCur3": {"columns": ["v"], "rows": [[0.1], [0.1], [0.2]]}}
    de.mark_currency_columns(tables, {"TCur3": ["v"]})
    assert _ev(g("PRODUCT(TCur3[v])"), tables) == "D0.002000000"


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


def test_desktop_b248g_p_cur_sig():
    # build_b248g.py p_cur_sig, Power BI Desktop 2.152 (desk_b248g.json)
    expr = _cells248(_g248("MROUND(CURRENCY(1.2345), 0.00015)"), _g248("FLOOR(CURRENCY(0.0003), 0.0001)"),
                     _g248("CEILING(CURRENCY(1), 1E16)"), _g248("MROUND(CURRENCY(0), 0.05)"), _g248("TRUNC(15, -1)"),
                     _g248("TRUNC(-15, -1)"), _g248("MOD(CURRENCY(5.5), CURRENCY(2))"),
                     _g248("CEILING(CURRENCY(1.2345), 0.00015)"))
    assert _ev(expr) == ("C1.234500000 C0.000300000 E C0.000000000 I10.000000000 I-10.000000000 "
                         "C1.500000000 C1.234500000")
