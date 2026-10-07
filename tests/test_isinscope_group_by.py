"""ISINSCOPE answers from the query's GROUPING, passed as ``group_by``.

ISINSCOPE(c) asks whether c is a group-by axis of the current query row. A
single-cell evaluation has no axes, so before ``group_by`` existed it answered
FALSE everywhere, and a measure such as

    SWITCH(TRUE(), ISINSCOPE('IT Area'[IT Area]) && ISINSCOPE('IT Area'[IT Sub Area]), ...)

was BLANK on every row of a two-level matrix where Desktop shows a label.

Measured against Power BI Desktop 2.157 (engine 17.0.83.18) on Microsoft's MIT
Corporate Spend sample, 11 ad-hoc measures x 7 query shapes (102 rows):
ISINSCOPE is TRUE for a grouped column; FALSE for one the query only filters
(TREATAS), for an ungrouped column, and at the grand total; FALSE inside
CALCULATE after REMOVEFILTERS(c), ALL(table), ALLSELECTED() or a replacing
filter on c (``c = "BU Support"``, even on the BU Support row); TRUE after
KEEPFILTERS(c = ...) or REMOVEFILTERS on another column. All of these now match.
Not modelled (still FALSE here): Desktop also puts c in scope through a context
transition, CALCULATE(ISINSCOPE(c)) inside an iteration over c.
"""
from __future__ import annotations

from pbix_mcp.dax import engine as de

TABLES = {
    "Area": {
        "columns": ["Area", "Sub", "v"],
        "rows": [["A", "a1", 1], ["A", "a2", 2], ["B", "b1", 4]],
    },
    "Other": {"columns": ["k"], "rows": [["x"]]},
}


def _ev(expr, filters, group_by=None):
    out = de.evaluate_measures_smart(["M"], TABLES, {"M": expr}, filters,
                                     simulate_row_context=False, group_by=group_by)
    return out["M"]


ROW = {"Area.Area": ["A"], "Area.Sub": ["a1"]}


def test_grouped_columns_are_in_scope():
    assert _ev("ISINSCOPE('Area'[Area])", ROW, {"Area.Area", "Area.Sub"}) is True
    assert _ev("ISINSCOPE('Area'[Sub])", ROW, {"Area.Area", "Area.Sub"}) is True


def test_without_group_by_nothing_is_in_scope():
    """The grand total, and every caller that does not pass group_by."""
    assert _ev("ISINSCOPE('Area'[Area])", ROW) is False
    assert _ev("ISINSCOPE('Area'[Area])", {}) is False


def test_filtered_but_not_grouped_is_not_in_scope():
    assert _ev("ISINSCOPE('Area'[Area])", ROW, {"Area.Sub"}) is False
    assert _ev("ISFILTERED('Area'[Area])", ROW, {"Area.Sub"}) is True


def test_two_level_label_measure():
    label = ("SWITCH(TRUE(), ISINSCOPE('Area'[Area]) && ISINSCOPE('Area'[Sub]), \"both\", "
             "ISINSCOPE('Area'[Area]), \"area\", BLANK())")
    assert _ev(label, ROW, {"Area.Area", "Area.Sub"}) == "both"
    assert _ev(label, {"Area.Area": ["A"]}, {"Area.Area"}) == "area"
    assert _ev(label, ROW, {"Area.Sub"}) is None


def test_calculate_that_replaces_or_removes_the_filter_takes_it_out_of_scope():
    g = {"Area.Area"}
    row = {"Area.Area": ["A"]}
    assert _ev("CALCULATE(ISINSCOPE('Area'[Area]), 'Area'[Area] = \"A\")", row, g) is False
    assert _ev("CALCULATE(ISINSCOPE('Area'[Area]), REMOVEFILTERS('Area'[Area]))", row, g) is False
    assert _ev("CALCULATE(ISINSCOPE('Area'[Area]), ALL('Area'))", row, g) is False


def test_keepfilters_and_other_columns_keep_it_in_scope():
    g = {"Area.Area", "Area.Sub"}
    assert _ev("CALCULATE(ISINSCOPE('Area'[Area]), KEEPFILTERS('Area'[Area] = \"A\"))", ROW, g) is True
    assert _ev("CALCULATE(ISINSCOPE('Area'[Area]), KEEPFILTERS('Area'[Area] = \"B\"))", ROW, g) is True
    assert _ev("CALCULATE(ISINSCOPE('Area'[Area]), REMOVEFILTERS('Area'[Sub]))", ROW, g) is True


def test_grouping_does_not_change_values():
    assert _ev("SUM('Area'[v])", ROW, {"Area.Area", "Area.Sub"}) == _ev("SUM('Area'[v])", ROW) == 1


def test_blank_group_key_predicate_is_in_scope():
    assert _ev("ISINSCOPE('Other'[k])", {"Other.k": {"is_blank": True}}, {"Other.k"}) is True


def test_a_measure_cached_under_a_plain_filter_is_not_reused_for_the_grouped_row():
    """Same value, different provenance: the measure cache must tell them apart."""
    measures = {"IS": "ISINSCOPE('Area'[Area])",
                "M": "CALCULATE([IS], 'Area'[Area] = \"A\") + 0 * [IS]"}   # caches [IS] under a plain filter first
    out = de.evaluate_measures_smart(["M", "IS"], TABLES, measures, {"Area.Area": ["A"]},
                                     simulate_row_context=False, group_by={"Area.Area"})
    assert out["IS"] is True
