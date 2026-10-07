"""Issue #80: KEEPFILTERS kept a column in scope for ISINSCOPE only for the
plain-predicate form on a grouped column.

KEEPFILTERS INTERSECTS the outer filter, and Power BI keeps the column in
scope whether the outer filter is the query's grouping or an iterated row
made a filter by a context transition (#77), and for KEEPFILTERS(FILTER(...))
as for a plain predicate. The intersection is a fresh predicate, which dropped
the scope tag in three of those four cases. Expected values are Power BI
Desktop's (2.152, over ADOMD).
"""
from __future__ import annotations

from pbix_mcp.dax import engine as de

TABLES = {"S": {"columns": ["Region", "V"],
                "rows": [["West", 30], ["East", 70], ["North", 50], ["South", 60]]}}
KF_PRED = 'KEEPFILTERS(S[Region] = "West")'
KF_FILTER = 'KEEPFILTERS(FILTER(ALL(S[Region]), S[Region] <> "x"))'


def _ev(expr, filters=None, group_by=None):
    return de.evaluate_measures_smart(["M"], TABLES, {"M": expr}, filters or {},
                                      simulate_row_context=False,
                                      group_by=group_by)["M"]


def test_keepfilters_after_a_context_transition_keeps_the_row_in_scope():
    # Desktop: 1 for every region, even where the intersection with West is empty
    expr = f"SUMX(VALUES(S[Region]), CALCULATE(IF(ISINSCOPE(S[Region]), 1, 0), {KF_PRED}))"
    assert _ev(expr) == 4
    assert _ev(expr, {"S.Region": ["East"]}, {"S.Region"}) == 1


def test_keepfilters_filter_form_after_a_transition():
    expr = f"SUMX(VALUES(S[Region]), CALCULATE(IF(ISINSCOPE(S[Region]), 1, 0), {KF_FILTER}))"
    assert _ev(expr) == 4
    assert _ev(expr, {"S.Region": ["West"]}, {"S.Region"}) == 1


def test_keepfilters_filter_form_on_a_grouped_column():
    expr = f"CALCULATE(ISINSCOPE(S[Region]), {KF_FILTER})"
    assert _ev(expr, {"S.Region": ["North"]}, {"S.Region"}) is True
    assert _ev(expr) is False             # grand total: nothing grouped


def test_a_replacing_filter_still_takes_the_column_out_of_scope():
    # control: only KEEPFILTERS intersects
    expr = 'SUMX(VALUES(S[Region]), CALCULATE(IF(ISINSCOPE(S[Region]), 1, 0), S[Region] = "West"))'
    assert _ev(expr) == 0
