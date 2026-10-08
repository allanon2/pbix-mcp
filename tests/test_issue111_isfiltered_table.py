"""Issue #111: ISFILTERED(<table>) is TRUE when a column of the table carries a
direct filter.

A bare table name evaluated to the table's rows, so the table form never
matched and was FALSE even under a filter on the table's own column. Expected
values: Power BI Desktop 2.152 over ADOMD.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"Dim": {"columns": ["Region", "Zone"], "rows": [["N", "z1"], ["S", "z1"], ["W", "z2"]]},
          "Orders": {"columns": ["Region", "Revenue"], "rows": [["N", 50.0], ["S", 150.0], ["W", 250.0]]}}
RELS = [{"FromTable": "Orders", "FromColumn": "Region", "ToTable": "Dim", "ToColumn": "Region",
         "IsActive": True, "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]

DESKTOP = {   # expression -> Desktop 2.152; * = 0.9.119 differed
    "CALCULATE(ISFILTERED(Orders), Orders[Revenue] > 100)": True,                # *
    "CALCULATE(ISFILTERED('Orders'), Orders[Revenue] > 100)": True,              # *
    'CALCULATE(ISFILTERED(Dim), Dim[Zone] = "z1")': True,                        # *
    "CALCULATE(ISFILTERED(Orders), Orders[Revenue] > 100, ALL(Orders))": True,   # *
    "CALCULATE(ISFILTERED(Orders[Revenue]), Orders[Revenue] > 100)": True,
    "ISFILTERED(Orders)": False,
    'CALCULATE(ISFILTERED(Orders), Dim[Zone] = "z1")': False,   # a cross filter is not direct
}


@pytest.mark.parametrize("expr,want", list(DESKTOP.items()), ids=list(DESKTOP))
def test_isfiltered_matches_desktop(expr, want):
    got = de.evaluate_measures_smart(["m"], TABLES, {"m": expr}, {}, relationships=RELS,
                                     simulate_row_context=False)["m"]
    assert bool(got) is want


@pytest.mark.parametrize("zone", ["z1", "z2"])
def test_under_a_grouping_on_the_dimension(zone):
    """Desktop's SUMMARIZECOLUMNS(Dim[Zone], ...): ISFILTERED(Dim) TRUE,
    ISFILTERED(Orders) FALSE on every row."""
    m = {"d": "ISFILTERED(Dim)", "o": "ISFILTERED(Orders)"}
    got = de.evaluate_measures_smart(["d", "o"], TABLES, m, {"Dim.Zone": [zone]}, relationships=RELS,
                                     simulate_row_context=False, group_by={"Dim.Zone"},
                                     selected_filters={})
    assert got["d"] is True and not got["o"]                  # 0.9.119: d False
