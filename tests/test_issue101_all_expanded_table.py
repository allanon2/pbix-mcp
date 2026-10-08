"""Issue #101 (found verifying PR #94): ALL / REMOVEFILTERS / ALLEXCEPT on a
table act on its EXPANDED table, and leave every other filter in force.

The engine dropped the table's own column filters and then stopped EVERY
filter reaching the table through any relationship. DAX removes the filters
on the expanded table's columns (so a dimension's own filter goes too, and
ALLEXCEPT keeps a listed dimension column), while a filter on a table OUTSIDE
it still reaches the table through a many-to-many or a bidirectional
relationship. Expected values: Power BI Desktop 2.152 over ADOMD, on the
model of test_issue100.
"""
from __future__ import annotations

import pytest

from tests.test_issue100_allselected_expanded_table import cell, same

pytestmark = pytest.mark.unit

# (measure, group, slicers, Desktop) -- 0.9.117 differed on every row marked *
CASES = [
    ("cDim_allF", {"Dim.Name": "p"}, {}, 3),           # * 1
    ("cDim_rfF", {"Dim.Name": "q"}, {}, 3),            # * 1
    ("cCat_allF", {"Cat.CatName": "x"}, {}, 2),        # * 1
    ("sAexDim", {"Dim.Name": "p"}, {}, 16),            # * kept dimension column: 45
    ("sAexDim", {"Dim.Name": "q"}, {}, 20),            # * 45
    ("sAexDim", {"Dim.Name": "r"}, {}, 9),             # * 45
    ("sAexCat", {"Cat.CatName": "x"}, {}, 36),         # * kept snowflake column: 45
    ("sAexCat", {"Dim.Name": "q"}, {"Cat.CatName": ["x"]}, 36),   # * 45
    ("cDim_aexDay", {"Dim.Name": "p"}, {}, 3),         # * 1
    ("sAllF", {"Tags.TagName": "alpha"}, {}, 27),      # * many-to-many stays: 45
    ("sAllF", {"Tags.TagName": "beta"}, {}, 18),       # * 45
    ("sRfF", {"Tags.TagName": "beta"}, {}, 18),        # * 45
    ("sAexDim", {"Tags.TagName": "beta"}, {}, 18),     # * 45
    ("sAllF", {"Fact2.w": "w1"}, {}, 36),              # * both-ways hop stays: 45
    ("sAllF", {"Fact2.w": "w2"}, {}, 9),               # * 45
    ("cDim_allDim", {"Fact2.w": "w1"}, {}, 2),         # * 3
    ("sAllTwin", {"Dim.Name": "p"}, {}, 45),           # * Twin's expanded table holds Dim: 16
    ("sAllDim", {"Twin.Tw": "t1"}, {}, 45),            # * Dim's holds Twin: 16
    ("sAllDim", {"Cat.CatName": "x"}, {}, 45),         # * Dim's holds Cat: 36
    # What must NOT change
    ("sAllDim", {"Fact2.w": "w2"}, {}, 9),             # Fact2 is outside Dim's expanded table
    ("sAllCat", {"Dim.Name": "p"}, {}, 16),            # Cat's expanded table is Cat
    ("sAllTags", {"Dim.Name": "q"}, {}, 20),
    ("sAllTags", {"Tags.TagName": "beta"}, {}, 45),
    ("sAexDay", {"Fact.day": 1}, {}, 12),
    ("sAllF", {"Dim.Name": "p"}, {}, 45),
    ("sAllF", {"Fact.day": 3}, {}, 45),
]


@pytest.mark.parametrize("measure,group,slicers,want", CASES,
                         ids=[f"{m}|{g}|{s}" for m, g, s, _w in CASES])
def test_all_family_matches_desktop(measure, group, slicers, want):
    got = cell(measure, group, slicers)
    assert same(got, want), (measure, group, slicers, got, want)


def test_a_later_filter_still_reaches_the_table():
    """ALL stops only what was live when it ran (MS_AI_Sample's
    CALCULATE(CALCULATE(.., Owners[Manager] = ..), ALL(Cases))): a filter the
    nested CALCULATE adds afterwards applies normally."""
    from pbix_mcp.dax import engine as de
    from tests.test_issue100_allselected_expanded_table import MEASURES, RELS, TABLES

    m = {**MEASURES, "nested": 'CALCULATE(CALCULATE(SUM(Fact[v]), Dim[Name] = "r"), ALL(Fact))'}
    got = de.evaluate_measures_smart(["nested"], TABLES, m, {"Dim.Name": ["p"]}, relationships=RELS,
                                     simulate_row_context=False)["nested"]
    assert got == 9
