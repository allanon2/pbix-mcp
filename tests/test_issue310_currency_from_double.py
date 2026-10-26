"""#310 (#248): CURRENCY(<Double>), CONVERT(<Double>, CURRENCY)
and a DATATABLE(..., CURRENCY, ...) literal round x * 10000 (in Double) half
away from zero.

What was wrong (0.9.147): CURRENCY() rounded the binary value half to even
(round(x, 4): 0.03125 gave 0.0312), CONVERT(..., CURRENCY) did not round at
all (C1.23456), and DATATABLE's CURRENCY column held unrounded Doubles.

Expected values: Power BI Desktop 2.152's cells over ADOMD, builders
verbatim: build_b254.py r_cur_lit, r_cur_col, r_conv_col (r() = type letter
+ the value in counts of 1/10,000), f_math cell 7, l_ctors cell 5;
build_b254b.py a_fe_conv. TD is the battery's table of those Doubles.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def r(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE) * 10000, "0.00"))'


TD_VALUES = [0.03125, 0.09375, 0.15625, -0.03125, -0.09375, 0.00005, 0.00015, 0.00025, 1.00005, -0.00005]
DESKTOP_COUNTS = "C313.00 C938.00 C1563.00 C-313.00 C-938.00 C1.00 C1.00 C3.00 C10001.00 C-1.00"


def _ev(expr):
    tables = {"TD": {"columns": ["id", "d"], "rows": [[i + 1, d] for i, d in enumerate(TD_VALUES)]}}
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(tables, {}, None, None, None, []))


def test_r_cur_lit():
    cells = ' & " " & '.join(r(f"CURRENCY({Decimal(repr(d)):f})") for d in TD_VALUES)
    assert _ev(cells) == DESKTOP_COUNTS


@pytest.mark.parametrize("fn", ["CURRENCY(TD[d])", "CONVERT(TD[d], CURRENCY)"], ids=["r_cur_col", "r_conv_col"])
def test_over_a_double_column(fn):
    assert _ev(f'CONCATENATEX(TD, {r(fn)}, ",", TD[id], ASC)') == DESKTOP_COUNTS.replace(" ", ",")


CASES = [
    (g("CONVERT(1.23456, CURRENCY)"), "C1.234600000"),                    # f_math cell 7
    (g('MAXX(DATATABLE("x", CURRENCY, {{0.00005}}), [x])'), "C0.000100000"),  # l_ctors cell 5
    # build_b254b.py a_fe_conv
    (r("CURRENCY(0.00035)"), "C4.00"),
    (r("CURRENCY(0.00045)"), "C5.00"),
    (r("CURRENCY(0.00065)"), "C7.00"),
    (r("CURRENCY(-0.00035)"), "C-4.00"),
    (r("CONVERT(0.00035, CURRENCY)"), "C4.00"),
    (r('MAXX(DATATABLE("x", CURRENCY, {{0.00035}}), [x])'), "C4.00"),
    (r('MAXX(DATATABLE("x", CURRENCY, {{0.00015}}), [x])'), "C1.00"),
    (r('MAXX(DATATABLE("x", CURRENCY, {{0.00025}}), [x])'), "C3.00"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[str(i) for i in range(len(CASES))])
def test_desktop_cells(expr, desktop):
    assert _ev(expr) == desktop


def test_a_calculated_datatable_holds_rounded_floats():
    # calc_tables' DATATABLE: the Currency column rounded as Desktop's
    # literal (0.00035 is 4 counts, a_fe_conv cell 5), stored as floats and
    # recorded as a Currency column
    from pbix_mcp.dax.calc_tables import _parse_datatable

    out = _parse_datatable('DATATABLE("x", CURRENCY, "y", DOUBLE, {{0.00035, 0.00035}})', {})
    assert out["rows"] == [[0.0004, 0.00035]]
    assert type(out["rows"][0][0]) is float
    assert out["currency_columns"] == frozenset({"x"})
