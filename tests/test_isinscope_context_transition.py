"""ISINSCOPE through a context transition (an iterator's row made a filter).

Measured against Power BI Desktop 2.157 on Microsoft's MIT Corporate Spend
sample ('IT Area' with IT Area / IT Sub Area), at the grand total:

    SUMX(VALUES(T[c]), IF(CALCULATE(ISINSCOPE(T[c])), 1, 0))   5   (each row in scope)
    SUMX(VALUES(T[c]), [ISINSCOPE(T[c]) measure] + 0)          5   (a measure reference transitions too)
    SUMX(T, IF(CALCULATE(ISINSCOPE(T[c])), 1, 0))              40  (every row of the table)
    SUMX(VALUES(T[c]), IF(ISINSCOPE(T[c]), 1, 0))              0   (a row context alone is no filter)
    SUMX(VALUES(T[other]), IF(CALCULATE(ISINSCOPE(T[c])), 1, 0)) 0

and the same shapes under 6 other groupings, 15 ISINSCOPE shapes in all: every cell now matches.
"""
from __future__ import annotations

from pbix_mcp.dax import engine as de

TABLES = {"Area": {"columns": ["Area", "Sub", "v"],
                   "rows": [["A", "a1", 1], ["A", "a2", 2], ["B", "b1", 4]]}}
MEASURES = {"IS": "ISINSCOPE('Area'[Area])"}


def _ev(expr, filters=None, group_by=None):
    return de.evaluate_measures_smart(["M"], TABLES, {**MEASURES, "M": expr}, filters or {},
                                      simulate_row_context=False, group_by=group_by)["M"]


def test_calculate_inside_an_iteration_puts_the_column_in_scope():
    assert _ev("SUMX(VALUES('Area'[Area]), IF(CALCULATE(ISINSCOPE('Area'[Area])), 1, 0))") == 2


def test_a_measure_reference_is_a_transition_too():
    assert _ev("SUMX(VALUES('Area'[Area]), [IS] + 0)") == 2


def test_iterating_the_table_puts_every_column_in_scope():
    assert _ev("SUMX('Area', IF(CALCULATE(ISINSCOPE('Area'[Area])), 1, 0))") == 3


def test_a_row_context_alone_answers_from_the_enclosing_context():
    expr = "SUMX(VALUES('Area'[Area]), IF(ISINSCOPE('Area'[Area]), 1, 0))"
    assert _ev(expr) == 0
    assert _ev(expr, {"Area.Area": ["A"]}, {"Area.Area"}) == 1      # grouped outside: in scope


def test_iterating_another_column_does_not_put_this_one_in_scope():
    assert _ev("SUMX(VALUES('Area'[Sub]), IF(CALCULATE(ISINSCOPE('Area'[Area])), 1, 0))") == 0
