"""Issue #224: NOT before a column reference without parentheses.

An unquoted table name may hold spaces in the engine's reference pattern, so
`NOT TBool[B]` and `NOT [B]` were read as columns of tables called "NOT
TBool" and "NOT": an unresolved reference, which is always true. FILTER(TBool,
NOT TBool[B]) kept every row and IF(NOT TBool[B], ...) took the first branch
on every row; NOT(TBool[B]) was right. NOT is a keyword, never the start of
an unquoted name.

Expected values: Power BI Desktop 2.152 over ADOMD (battery build_b210.py,
TBool[B] = TRUE, FALSE): COUNTROWS(FILTER(TBool, NOT TBool[B])) is 1 and
COUNTROWS(FILTER(TBool, NOT(TBool[B]))) is 1. The other shapes follow from
the same NOT per row, NOT BLANK being TRUE, and DAX's operator precedence as
the engine has it from Desktop (NOT binds looser than a comparison, tighter
than && and ||; see _analyze_expr).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TBOOL = {"TBool": {"columns": ["B", "A"], "rows": [[True, 10], [False, 5]]}}
# with a BLANK flag, and a table whose quoted name starts with NOT
TB3 = {"TB3": {"columns": ["B", "A"], "rows": [[True, 10], [False, 5], [None, 1]]},
       "NOT x": {"columns": ["c"], "rows": [[7]]}}


def _ev(tables, expr):
    return de.evaluate_measure("m", tables, {"m": expr})


DESKTOP = [
    ("e_not_bare", "COUNTROWS(FILTER(TBool, NOT TBool[B]))", 1),
    ("e_not_paren", "COUNTROWS(FILTER(TBool, NOT(TBool[B])))", 1),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(TBOOL, expr) == want


SHAPES = [
    ("bare_column", "COUNTROWS(FILTER(TBool, NOT [B]))", 1),
    ("lower_case", "COUNTROWS(FILTER(TBool, not TBool[B]))", 1),
    ("two_spaces", "COUNTROWS(FILTER(TBool, NOT  TBool[B]))", 1),
    ("quoted_table", "COUNTROWS(FILTER(TBool, NOT 'TBool'[B]))", 1),
    ("then_and", "COUNTROWS(FILTER(TBool, NOT TBool[B] && TBool[A] > 0))", 1),
    ("after_and", "COUNTROWS(FILTER(TBool, TBool[A] > 0 && NOT TBool[B]))", 1),
    ("then_or", "COUNTROWS(FILTER(TBool, NOT TBool[B] || TBool[A] = 10))", 2),
    ("over_comparison", "COUNTROWS(FILTER(TBool, NOT TBool[A] = 5))", 1),
    ("in_if", "SUMX(TBool, IF(NOT TBool[B], TBool[A], 0))", 5),
    ("in_calculate_filter", "CALCULATE(SUM(TBool[A]), FILTER(TBool, NOT TBool[B]))", 5),
]


@pytest.mark.parametrize("probe,expr,want", SHAPES, ids=[p for p, _e, _w in SHAPES])
def test_not_shapes(probe, expr, want):
    assert _ev(TBOOL, expr) == want


def test_not_blank_is_true():
    assert _ev(TB3, "COUNTROWS(FILTER(TB3, NOT TB3[B]))") == 2      # FALSE and BLANK
    assert _ev(TB3, "SUMX(FILTER(TB3, NOT [B]), TB3[A])") == 6


def test_quoted_name_starting_with_not_is_a_reference():
    assert _ev(TB3, "SUMX('NOT x', 'NOT x'[c])") == 7


@pytest.mark.parametrize("text,kind", [
    ("NOT T[B]", de._P_NOT),
    ("NOT [B]", de._P_NOT),
    ("not\tT[B]", de._P_NOT),
    ("'NOT T'[B]", de._P_TCOL),
    ("Notes[B]", de._P_TCOL),
    ("Sales Table[B]", de._P_TCOL),
])
def test_plan(text, kind):
    assert de._analyze_expr(text)[0][0] == kind
