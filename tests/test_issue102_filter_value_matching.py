"""Issue #102 (PR #95 by @allanon2): a filter on a column matches values as
DAX compares them.

The In-set index and matcher keyed values by str() with float and calendar-day
aliases, so '01' also selected '1', a date-time selected every time of its
day, and text compared case-sensitively ("north" selected nothing). DAX: text
is case-insensitive and stays text, numbers compare by value across storage
(issue #39), date-times are moments and a date is midnight. Expected values:
Power BI Desktop 2.152 over ADOMD on the same rows.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

ROWS = [
    ["01", datetime(2026, 1, 5, 9, 0), "North", 2024.0, 1],
    ["1", datetime(2026, 1, 5, 17, 0), "South", 2025.0, 2],
    ["2", datetime(2026, 1, 6, 0, 0), "North", 2024.5, 4],
    ["10", datetime(2026, 1, 6, 0, 0), "East", 2024.0, 8],
    ["A1", datetime(2026, 1, 7, 12, 30), "West", 7.0, 16],
]
TABLES = {"F2": {"columns": ["Code", "At", "Region", "Num", "v"], "rows": ROWS}}
MEASURES = {
    "s2": "SUM(F2[v])",
    "eq1": 'CALCULATE([s2], F2[Code] = "1")',
    "eq01": 'CALCULATE([s2], F2[Code] = "01")',
    "north": 'CALCULATE([s2], F2[Region] = "north")',
    "tnorth": 'CALCULATE([s2], TREATAS({"north"}, F2[Region]))',
    "inNE": 'CALCULATE([s2], F2[Region] IN {"NORTH", "east"})',
    "notNorth": 'CALCULATE([s2], NOT(F2[Region] IN {"north"}))',
    "d6": "CALCULATE([s2], F2[At] = DATE(2026, 1, 6))",
    "d5": "CALCULATE([s2], F2[At] = DATE(2026, 1, 5))",
    "t9": "CALCULATE([s2], F2[At] = DATE(2026, 1, 5) + TIME(9, 0, 0))",
    "numEq": "CALCULATE([s2], F2[Num] = 2024)",
}


def ev(measure, fc=None):
    return de.evaluate_measures_smart([measure], TABLES, MEASURES, fc or {},
                                      simulate_row_context=False)[measure]


# (filter_context, Desktop's SUM(F2[v]) under that slicer / group) -- 0.9.117
# differed on every row marked *
FILTERS = [
    ({"F2.Code": ["01"]}, 1),                            # * 3: '1' too
    ({"F2.Code": ["1"]}, 2),
    ({"F2.Code": ["10"]}, 8),
    ({"F2.At": ["2026-01-05T09:00:00"]}, 1),             # * 3: 17:00 too
    ({"F2.At": [datetime(2026, 1, 5, 17, 0)]}, 2),       # * 3
    ({"F2.At": ["2026-01-06"]}, 12),                     # a date is midnight
    ({"F2.At": ["2026-01-05"]}, None),                   # * 3: no row at midnight
    ({"F2.Region": ["north"]}, 5),                       # * BLANK
    ({"F2.Region": ["NORTH", "west"]}, 21),              # * BLANK
    ({"F2.Num": [2024]}, 9),                             # int filter, Double column (#39)
]

# The engine's leniency for tool callers, who spell a value either way: a
# numeric string selects the number, a number its text spelling -- never
# '01' for 1, nor another time of the day.
LENIENT = [
    ({"F2.Num": ["2024"]}, 9),
    ({"F2.Code": [10]}, 8),
    ({"F2.Code": [1]}, 2),
]


@pytest.mark.parametrize("fc,want", FILTERS, ids=[json.dumps(f, default=str) for f, _w in FILTERS])
def test_filter_context_matches_desktop(fc, want):
    assert ev("s2", fc) == want


@pytest.mark.parametrize("fc,want", LENIENT, ids=[json.dumps(f, default=str) for f, _w in LENIENT])
def test_caller_spellings_still_match(fc, want):
    assert ev("s2", fc) == want


# (measure, Desktop at the grand total)
PREDICATES = [
    ("eq1", 2), ("eq01", 1),                     # * eq01 3
    ("north", 5), ("tnorth", 5), ("inNE", 13),   # * all BLANK
    ("notNorth", 26),                            # * 31
    ("d6", 12), ("d5", None), ("t9", 1),         # * d5 3, t9 3
    ("numEq", 9),
]


@pytest.mark.parametrize("measure,want", PREDICATES, ids=[m for m, _w in PREDICATES])
def test_predicates_match_desktop(measure, want):
    assert ev(measure) == want


NAMES = ["Apple", "apple", "Äpfel", "äpfel", "Øl", "øl", "Éa", "éa", "Σίγμα", "σίγμα",
         "Дом", "дом", "Straße", "Strasse"]
COLLATION = {"F5": {"columns": ["Name", "v"], "rows": [[n, 2 ** i] for i, n in enumerate(NAMES)]}}


@pytest.mark.parametrize("value,want", [
    ("APPLE", 3),          # ASCII letters fold: Apple + apple
    ("ÄPFEL", 4),          # only Äpfel: Ä and ä are two letters to the column store
    ("ØL", 16), ("ÉA", 64),
    ("ΣΊΓΜΑ", None), ("ДОМ", None),
    ("STRASSE", 8192),     # Strasse, not Straße
])
def test_column_values_fold_ascii_letters_only(value, want):
    """Desktop 2.152's own import of these 14 names keeps 13 distinct values
    (only Apple / apple fold), and a column filter matches by that rule.
    (An EXPRESSION compares differently: there "Ä" = "ä" and "ß" = "ss".)"""
    m = {"s": f'CALCULATE(SUM(F5[v]), F5[Name] = "{value}")'}
    got = de.evaluate_measures_smart(["s"], COLLATION, m, {}, simulate_row_context=False)["s"]
    assert got == want
    got = de.evaluate_measures_smart(["s"], COLLATION, {"s": "SUM(F5[v])"}, {"F5.Name": [value]},
                                     simulate_row_context=False)["s"]
    assert got == want


def test_matcher_and_index_agree():
    """make_value_matcher (used for the dimension side of a relationship) and
    the column index (the table's own filter) give the same rows."""
    for fc, _want in FILTERS + LENIENT:
        (key, values), = fc.items()
        col = key.split(".", 1)[1]
        idx = TABLES["F2"]["columns"].index(col)
        m = de.make_value_matcher(values)
        by_matcher = sum(r[4] for r in ROWS if m(r[idx])) or None
        assert by_matcher == ev("s2", fc), fc


def test_grouped_tool_keeps_text_codes_and_times_apart(tmp_path):
    """The user-facing path: pbix_evaluate_dax_grouped over a built file, one
    group per distinct value. A plain SUM takes the bucketing fast path; a
    CALCULATE measure is evaluated per group through the group's filter, which
    is where the old matching went wrong (Desktop 2.152's SUMMARIZECOLUMNS
    answers below)."""
    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    b = PBIXBuilder("i102")
    b.add_table("F2", [{"name": "Code", "data_type": "String"}, {"name": "At", "data_type": "DateTime"},
                       {"name": "Region", "data_type": "String"}, {"name": "v", "data_type": "Int64"}],
                rows=[{"Code": r[0], "At": r[1], "Region": r[2], "v": r[4]} for r in ROWS])
    b.add_measure("F2", "s2", "SUM(F2[v])")
    b.add_measure("F2", "north", MEASURES["north"])
    b.add_measure("F2", "eq1", MEASURES["eq1"])
    p = tmp_path / "i102.pbix"
    b.save(str(p))
    assert json.loads(S.pbix_open(str(p), "i102"))["success"]
    try:
        by_code = json.loads(S.pbix_evaluate_dax_grouped("i102", "s2,north", "F2.Code"))["data"]["groups"]
        by_at = json.loads(S.pbix_evaluate_dax_grouped("i102", "s2,eq1", "F2.At"))["data"]["groups"]
    finally:
        S.pbix_close("i102")
    assert {g["key"]["Code"]: g["values"]["s2"] for g in by_code} == {
        "01": 1, "1": 2, "2": 4, "10": 8, "A1": 16}
    assert {g["key"]["Code"]: g["values"]["north"] for g in by_code} == {
        "01": 1, "1": None, "2": 4, "10": None, "A1": None}    # 0.9.117: BLANK, BLANK, ...
    at = sorted(by_at, key=lambda g: g["key"]["At"])
    assert [g["values"]["s2"] for g in at] == [1, 2, 12, 16]
    assert [g["values"]["eq1"] for g in at] == [None, 2, None, None]   # 0.9.117: 2 at 09:00 too
