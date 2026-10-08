"""ALLSELECTED(Table) works on the table's EXPANDED table.

The expanded table of a fact includes the one-side tables it reaches through
active many-to-one relationships. So a visual's grouping filter on a dimension
column is removed by ALLSELECTED(Fact) like one on the fact's own columns, while
an outer selection (a slicer, passed as selected_filter_context) stays.

Measured against Power BI Desktop 2.157 on Microsoft's MIT Performance Analyzer
sample: the Timeline table groups by RootActions, EventTypes and EventEdges
columns with no outer filter, and Desktop's own visual query answers
`MIN(EventEdges[timestampMs]) - CALCULATE(MIN(EventEdges[timestampMs]),
ALLSELECTED(EventEdges))` as 439.99951171875 on the second edge. The engine
kept the EventTypes grouping filter and answered 0.
"""
from __future__ import annotations

from pbix_mcp.dax import engine as de

TABLES = {
    "Kind": {"columns": ["kid", "Kind"], "rows": [[1, "a"], [2, "b"]]},
    "Edge": {"columns": ["kid", "ts"], "rows": [[1, 10], [2, 20], [1, 30], [2, 40]]},
}
RELS = [{"FromTable": "Edge", "FromColumn": "kid", "ToTable": "Kind", "ToColumn": "kid",
         "IsActive": True, "CrossFilteringBehavior": 1}]
FIRST = "CALCULATE(MIN('Edge'[ts]), ALLSELECTED(Edge))"
GROUP_B = {"Kind.Kind": ["b"]}


def _ev(expr, filters, group_by=None, selected=None):
    return de.evaluate_measures_smart(["M"], TABLES, {"M": expr}, filters, relationships=RELS,
                                      simulate_row_context=False, group_by=group_by,
                                      selected_filters=selected)["M"]


def test_a_grouping_filter_on_a_dimension_is_removed():
    assert _ev(FIRST, GROUP_B, {"Kind.Kind"}, {}) == 10            # over every edge, not 20


def test_an_outer_selection_on_a_dimension_stays():
    assert _ev(FIRST, GROUP_B, set(), GROUP_B) == 20


def test_a_grouping_filter_on_a_dimension_keeps_the_slicer_on_it():
    assert _ev(FIRST, {"Kind.Kind": ["b"]}, {"Kind.Kind"}, {"Kind.Kind": ["a", "b"]}) == 10


def test_allselected_on_the_dimension_leaves_the_fact_alone():
    expr = "CALCULATE(MIN('Edge'[ts]), ALLSELECTED(Kind))"
    assert _ev(expr, {"Edge.ts": [30, 40]}, {"Edge.ts"}, {}) == 30   # Edge isn't in Kind's expanded table
