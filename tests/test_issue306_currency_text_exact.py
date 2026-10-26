"""#306 (#248): the text of a Fixed Decimal (Currency) value --
`&`, FORMAT "General Number", CONVERT(..., STRING) and custom formats --
prints its exact decimal digits.

What was wrong (0.9.147): a Currency printed as a Double, at 15 significant
digits: CURRENCY(123456789012.3456) & "" was 123456789012.346.

Expected values: Power BI Desktop 2.152's answers over ADOMD: build_b254.py
m_fmt (eleven texts joined by "|") and m_concat; build_b254b.py g_text (texts
t(x) = IFERROR((x) & "", "E") joined by "|", builder verbatim).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def t(x):
    return f'IFERROR(({x}) & "", "E")'


def texts(*xs):
    return ' & "|" & '.join(t(x) for x in xs)


def _ev(expr, tables=None):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(tables or {}, {}, None, None, None, []))


M_FMT = ' & "|" & '.join([
    'FORMAT(CURRENCY(123456789012.3456), "General Number")', 'FORMAT(CURRENCY(123456789012.3456), "0.0000")',
    'CURRENCY(123456789012.3456) & ""', 'FORMAT(123456789012.3456, "General Number")',
    '123456789012.3456 & ""', 'FORMAT(CURRENCY(1234.5), "Currency")',
    'FORMAT(CURRENCY(0.0001), "General Number")', 'CURRENCY(0.0001) & ""', 'CURRENCY(-1.5) & ""',
    'FORMAT(CURRENCY(1) / 3, "0.0000000000")', 'CURRENCY(2) & ""'])
G_TEXT = texts("CURRENCY(5E14) + CURRENCY(5E14)", 'CURRENCY("922337203685477.5807")',
               'CURRENCY("-922337203685477.5808")', 'FORMAT(CURRENCY("922337203685477.5807"), "#,##0.00")',
               'FORMAT(CURRENCY("922337203685477.5807"), "General Number")', "CURRENCY(-0.0001)",
               'FORMAT(CURRENCY(-0.00005), "0.0000")', 'FORMAT(CURRENCY(1) / 3, "General Number")')


def test_m_fmt():
    assert _ev(M_FMT) == ("123456789012.3456|123456789012.3456|123456789012.3456|123456789012.346|"
                          "123456789012.346|$1,234.50|0.0001|0.0001|-1.5|0.3333000000|2")


def test_g_text():
    assert _ev(G_TEXT) == ("-844674407370955.1616|922337203685477.5807|-922337203685477.5808|"
                           "922,337,203,685,477.58|922337203685477.5807|-0.0001|-0.0001|0.3333")


def test_g_text2():
    # build_b248c.py g_text2 (texts joined by "|"): the Int64's General Number
    # is its exact digits (#315), CONVERT(<Currency>,
    # STRING) the exact decimal, and a de-DE General Number writes the
    # Currency with the culture's decimal comma (it was 1234.5678)
    expr = texts('FORMAT(123456789012345678, "General Number")', "CONVERT(CURRENCY(123456789012.3456), STRING)",
                 'FORMAT(CURRENCY(1234.5678), "General Number", "de-DE")',
                 'FORMAT(CURRENCY(1234.5678), "#,##0.00", "de-DE")', "CURRENCY(-0.00005)", "ROUND(5, -1)",
                 "TRUNC(5)", "ROUNDUP(5, 0)")
    assert _ev(expr) == "123456789012345678|123456789012.3456|1234,5678|1.234,57|-0.0001|10|5|5"


def test_m_concat():
    tables = {"TR": {"columns": ["id", "v"], "rows": [[i, (i - 8) / 10000] for i in range(1, 16)]}}
    de.mark_currency_columns(tables, {"TR": ["v"]})
    assert _ev('CONCATENATEX(TR, TR[v], ",", TR[id], ASC)', tables) == (
        "-0.0007,-0.0006,-0.0005,-0.0004,-0.0003,-0.0002,-0.0001,0,0.0001,0.0002,0.0003,0.0004,0.0005,"
        "0.0006,0.0007")
