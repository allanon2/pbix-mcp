"""Issue #103 (PR #99 by @allanon2): TREATAS onto several columns filters
their COMBINATIONS.

A row constructor ("N", "A") evaluated as BLANK and TREATAS kept only its first
target column, so CALCULATE(m, TREATAS({("N", "A"), ("W", "B")},
Region[Region], Prod[Cat])) answered BLANK, silently -- the filter Desktop
writes for a visual's two-column Include filter. Each table sees the
combination's columns in its expanded table (its projection); plain TREATAS
replaces the filters on its columns, KEEPFILTERS intersects. Expected values:
Power BI Desktop 2.152 over ADOMD on the same model.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "Region": {"columns": ["Region"], "rows": [["N"], ["S"], ["W"]]},
    "Prod": {"columns": ["pk", "Cat"], "rows": [[1, "A"], [2, "B"]]},
    "Sales": {"columns": ["Region", "pk", "v"], "rows": [
        ["N", 1, 1], ["N", 2, 2], ["S", 1, 4], ["W", 2, 8], ["W", 1, 16]]},
}
RELS = [{"FromTable": "Sales", "FromColumn": "Region", "ToTable": "Region", "ToColumn": "Region",
         "IsActive": True, "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1},
        {"FromTable": "Sales", "FromColumn": "pk", "ToTable": "Prod", "ToColumn": "pk",
         "IsActive": True, "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
PAIRS = '{("N", "A"), ("W", "B")}'
MEASURES = {
    "s3": "SUM(Sales[v])",
    "tt": f"CALCULATE([s3], TREATAS({PAIRS}, Region[Region], Prod[Cat]))",
    "tk": f"CALCULATE([s3], KEEPFILTERS(TREATAS({PAIRS}, Region[Region], Prod[Cat])))",
    "t1": 'CALCULATE([s3], TREATAS({(1, "A")}, Prod[pk], Prod[Cat]))',
    "cR": f"CALCULATE(COUNTROWS(Region), TREATAS({PAIRS}, Region[Region], Prod[Cat]))",
    "cP": f"CALCULATE(COUNTROWS(Prod), TREATAS({PAIRS}, Region[Region], Prod[Cat]))",
    "tcase": 'CALCULATE([s3], TREATAS({("n", "a")}, Region[Region], Prod[Cat]))',
    "tvar": 'VAR t = {("S", "A"), ("W", "A")} RETURN CALCULATE([s3], TREATAS(t, Region[Region], Prod[Cat]))',
    "tsum": "CALCULATE([s3], TREATAS(SUMMARIZE(Sales, Region[Region], Prod[Cat]), Region[Region], Prod[Cat]))",
    "twrong": 'CALCULATE([s3], TREATAS({("N", "A", 1)}, Region[Region], Prod[Cat]))',
    "inner": f'CALCULATE(CALCULATE([s3], Region[Region] = "S"), TREATAS({PAIRS}, Region[Region], Prod[Cat]))',
}


def cell(measure, group=None, slicers=None):
    group, slicers = group or {}, slicers or {}
    fc = {**slicers, **{k: [v] for k, v in group.items()}}
    return de.evaluate_measures_smart(
        [measure], TABLES, MEASURES, fc, relationships=RELS, simulate_row_context=False,
        group_by=set(group) or None, selected_filters=slicers if group else None)[measure]


R, C = "Region.Region", "Prod.Cat"
# (measure, group, slicers, Desktop) -- 0.9.117 answered BLANK on every row
# marked *; the others it answered right, mostly by accident (its first
# column alone happened to select the same rows).
CASES = [
    ("tt", {}, {}, 9), ("tk", {}, {}, 9), ("t1", {}, {}, 21), ("cR", {}, {}, 2),   # *
    ("tcase", {}, {}, 1), ("tvar", {}, {}, 20),                                    # *
    ("tt", {R: "S"}, {}, 9),                     # * plain TREATAS replaces the grouping
    ("tk", {R: "N"}, {}, 1),                     # * KEEPFILTERS intersects
    ("tk", {R: "W"}, {}, 8),                     # *
    ("t1", {R: "W"}, {}, 16),                    # * the Region grouping stays
    ("t1", {C: "B"}, {}, 21),                    # * the Prod grouping is replaced
    ("tk", {R: "W", C: "B"}, {}, 8),             # *
    ("tt", {}, {R: ["S"]}, 9),                   # * a slicer is replaced too
    ("cR", {C: "A"}, {R: ["S"]}, 2),             # *
    ("cP", {}, {}, 2),
    ("tk", {R: "S"}, {}, None),
    ("tk", {R: "W", C: "A"}, {}, None),
    ("tk", {}, {R: ["S"]}, None),
    ("tsum", {R: "N"}, {}, 3),                   # SUMMARIZE runs in the outer context
    ("tsum", {C: "B"}, {}, 10),
]


@pytest.mark.parametrize("measure,group,slicers,want", CASES,
                         ids=[f"{m}|{g}|{s}" for m, g, s, _w in CASES])
def test_treatas_combinations_match_desktop(measure, group, slicers, want):
    got = cell(measure, group, slicers)
    assert got == want, (measure, group, slicers, got, want)


def test_a_row_of_the_wrong_width_is_an_error_not_a_blank():
    de._engine.eval_errors.clear()
    assert cell("twrong") is None
    assert "TREATAS" in de._engine.eval_errors.get("twrong", "")   # Desktop: an error


def test_an_inner_filter_overwrites_its_column_of_the_combinations():
    """CALCULATE(CALCULATE(m, Region = "S"), TREATAS(<pairs>, Region, Cat)):
    the inner filter replaces the Region column of the outer combinations, which
    leave their Cat projection {A, B} -- S-A, 4 (DAX's overwrite of a filter on
    several columns)."""
    assert cell("inner") == 4


def test_a_row_constructor_is_one_row_of_values():
    m = {"n": 'COUNTROWS({("N", "A"), ("W", "B")})',
         "v2": 'MAXX({("N", "A"), ("W", "B")}, [Value2])'}
    got = de.evaluate_measures_smart(list(m), TABLES, m, {}, simulate_row_context=False)
    assert got == {"n": 2, "v2": "B"}
