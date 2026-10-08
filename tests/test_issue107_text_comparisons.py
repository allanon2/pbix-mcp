"""Issue #107: text comparisons in EXPRESSIONS are case-insensitive.

The formula engine compares text with the model's linguistic collation:
"North" = "north", "a" < "B", and even "Ä" = "ä", "ß" = "ss". The engine
compared with Python's case-sensitive operators everywhere -- =, <, ==, IN,
SWITCH, LOOKUPVALUE, CONTAINS, CONTAINSROW, DISTINCT over a constructed
table, MAXX over text. FIND, EXACT and SUBSTITUTE stay case-sensitive, and
accents still count. (A COLUMN's stored values fold ASCII letters only --
#102 / #109.) Expected values: Power BI Desktop 2.152 over ADOMD.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"F2": {"columns": ["Code", "Region", "v"], "rows": [
    ["01", "North", 1], ["1", "South", 2], ["2", "North", 4], ["10", "East", 8], ["A1", "West", 16]]}}
PROBES = {   # expression -> Desktop 2.152
    'IF("North" = "north", 1, 0)': 1,
    'IF("a" < "B", 1, 0)': 1,
    'IF("North" <> "NORTH", 1, 0)': 0,
    'IF("North" == "north", 1, 0)': 1,
    'COUNTROWS(FILTER(F2, F2[Region] = "north"))': 2,
    'COUNTROWS(FILTER(F2, F2[Region] IN {"NORTH"}))': 2,
    'CALCULATE(SUM(F2[v]), F2[Region] <> "north")': 26,
    'CALCULATE(SUM(F2[v]), F2[Region] > "m")': 23,
    'SWITCH("north", "North", 1, 0)': 1,
    'LOOKUPVALUE(F2[Code], F2[Region], "south")': "1",
    'IF("NORTH" IN VALUES(F2[Region]), 1, 0)': 1,
    'IF(CONTAINS(F2, F2[Region], "north"), 1, 0)': 1,
    'IF(CONTAINSROW(VALUES(F2[Region]), "north"), 1, 0)': 1,
    'COUNTROWS(DISTINCT({"a", "A", "b"}))': 2,
    'MAXX({"a", "B"}, [Value])': "B",
    'CONCATENATEX(TOPN(1, {"apple", "Banana"}, [Value], ASC), [Value])': "apple",
    # the formula engine folds fully (the column store folds ASCII only)
    'IF("Ä" = "ä", 1, 0)': 1,
    'IF("Σ" = "σ", 1, 0)': 1,
    'IF("Straße" = "STRASSE", 1, 0)': 1,
    # still case-sensitive / accent-sensitive in Desktop too
    'IFERROR(FIND("nor", "North"), -1)': -1,
    'SEARCH("nor", "North")': 1,
    'IF(EXACT("North", "north"), 1, 0)': 0,
    'SUBSTITUTE("North", "n", "x")': "North",
    'IF("é" = "e", 1, 0)': 0,
}


@pytest.mark.parametrize("expr,want", list(PROBES.items()), ids=list(PROBES))
def test_expression_text_comparison_matches_desktop(expr, want):
    got = de.evaluate_measures_smart(["m"], TABLES, {"m": expr}, {}, simulate_row_context=False)["m"]
    if isinstance(want, (int, float)) and not isinstance(got, str):
        assert got == want or (got is True and want == 1) or (got is False and want == 0)
    else:
        assert got == want
