"""ALLSELECTED restores the query's own filters, passed as selected_filter_context.

With ``group_by`` the grouping keys are known, and ALLSELECTED removes them and
keeps the rest of filter_context. But a slicer ON the grouped column is
overwritten by the group key when they are merged (``Area IN {A, B}`` grouped
by Area becomes ``Area = [A]``), so "the rest" has lost it, and ALLSELECTED(Area)
returned the total over every area instead of over the selected ones.

Measured against Power BI Desktop 2.157 on Microsoft's MIT Corporate Spend
sample: 8 ALLSELECTED shapes (ALLSELECTED(), on a table, on the grouped column,
on another column / table, a percent-of-selected measure) x 7 query shapes.
With group_by alone all matched except the slicer-on-the-axis query (2 rows x 4
shapes); with selected_filter_context every cell matches.
"""
from __future__ import annotations

from pbix_mcp.dax import engine as de

TABLES = {"Area": {"columns": ["Area", "v"], "rows": [["A", 1], ["B", 2], ["C", 4]]}}
SLICER = {"Area.Area": ["A", "B"]}


def _ev(expr, filters, group_by=None, selected=None):
    return de.evaluate_measures_smart(["M"], TABLES, {"M": expr}, filters, simulate_row_context=False,
                                      group_by=group_by, selected_filters=selected)["M"]


ROW_A = {"Area.Area": ["A"]}   # the group key has replaced the slicer on the same column


def test_allselected_column_restores_the_slicer_on_the_grouped_column():
    expr = "CALCULATE(SUM('Area'[v]), ALLSELECTED('Area'[Area]))"
    assert _ev(expr, ROW_A, {"Area.Area"}, SLICER) == 3          # A + B, not A + B + C


def test_percent_of_selected():
    expr = "DIVIDE(SUM('Area'[v]), CALCULATE(SUM('Area'[v]), ALLSELECTED('Area'[Area])))"
    assert _ev(expr, ROW_A, {"Area.Area"}, SLICER) == 1 / 3


def test_without_selected_filters_the_slicer_is_unknowable():
    """The previous behaviour, kept when the caller does not say: the slicer is gone."""
    expr = "CALCULATE(SUM('Area'[v]), ALLSELECTED('Area'[Area]))"
    assert _ev(expr, ROW_A, {"Area.Area"}) == 7


def test_grouping_without_a_slicer_is_unchanged():
    expr = "CALCULATE(SUM('Area'[v]), ALLSELECTED('Area'[Area]))"
    assert _ev(expr, ROW_A, {"Area.Area"}, {}) == 7
    assert _ev("SUM('Area'[v])", ROW_A, {"Area.Area"}, SLICER) == 1
