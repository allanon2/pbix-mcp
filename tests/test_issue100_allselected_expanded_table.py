"""Issue #100 (PR #94 by @allanon2): ALLSELECTED(Table) acts on the table's
EXPANDED table.

The expanded table of a fact takes in the one-side tables it reaches through
active many-to-one relationships (transitively) and both ends of a
one-to-one; a many-to-many relationship expands nothing. So under a visual
grouped by a dimension column, ALLSELECTED(Fact) removes that grouping filter
too, while a slicer (an outer selection) stays. The engine removed only the
fact's own columns, and the share-of-total measure read 1 on every row.

Every expected value below is Power BI Desktop 2.152's answer over ADOMD
(SUMMARIZECOLUMNS = the visual query), on the same model.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

# Fact -> Dim -> Cat (snowflake), Dim <-> Twin (1:1, both ways),
# Fact <-> Tags (many-to-many), Fact2 -> Dim (filters both ways).
TABLES = {
    "Cat": {"columns": ["cid", "CatName"], "rows": [[1, "x"], [2, "y"]]},
    "Dim": {"columns": ["k", "Name", "cid"], "rows": [[1, "p", 1], [2, "q", 1], [3, "r", 2]]},
    "Twin": {"columns": ["k", "Tw"], "rows": [[1, "t1"], [2, "t2"], [3, "t3"]]},
    "Tags": {"columns": ["tag", "TagName"], "rows": [["a", "alpha"], ["a", "alpha2"], ["b", "beta"]]},
    "Fact": {"columns": ["k", "v", "day", "tag"], "rows": [
        [1, 5, 1, "a"], [2, 7, 1, "b"], [3, 9, 2, "a"], [1, 11, 2, "b"], [2, 13, 3, "a"]]},
    "Fact2": {"columns": ["k", "w"], "rows": [[1, "w1"], [2, "w1"], [3, "w2"]]},
}


def _rel(ft, fc, tt, tc, fcard=2, tcard=1, xf=1):
    return {"FromTable": ft, "FromColumn": fc, "ToTable": tt, "ToColumn": tc, "IsActive": True,
            "CrossFilteringBehavior": xf, "FromCardinality": fcard, "ToCardinality": tcard}


RELS = [_rel("Fact", "k", "Dim", "k"), _rel("Dim", "cid", "Cat", "cid"),
        _rel("Dim", "k", "Twin", "k", 1, 1, 2), _rel("Fact", "tag", "Tags", "tag", 2, 2),
        _rel("Fact2", "k", "Dim", "k", 2, 1, 2)]

MEASURES = {
    "asF": "CALCULATE(MIN(Fact[v]), ALLSELECTED(Fact))",
    "asDim": "CALCULATE(MIN(Fact[v]), ALLSELECTED(Dim))",
    "asTwin": "CALCULATE(MIN(Fact[v]), ALLSELECTED(Twin))",
    "asTags": "CALCULATE(MIN(Fact[v]), ALLSELECTED(Tags))",
    "cDim_asF": "CALCULATE(COUNTROWS(Dim), ALLSELECTED(Fact))",
    "cCat_asF": "CALCULATE(COUNTROWS(Cat), ALLSELECTED(Fact))",
    "cTwin_asDim": "CALCULATE(COUNTROWS(Twin), ALLSELECTED(Dim))",
    "pct": "DIVIDE(SUM(Fact[v]), CALCULATE(SUM(Fact[v]), ALLSELECTED(Fact)))",
    # ALL / REMOVEFILTERS / ALLEXCEPT (issue #101)
    "sAllF": "CALCULATE(SUM(Fact[v]), ALL(Fact))",
    "sRfF": "CALCULATE(SUM(Fact[v]), REMOVEFILTERS(Fact))",
    "cDim_allF": "CALCULATE(COUNTROWS(Dim), ALL(Fact))",
    "cDim_rfF": "CALCULATE(COUNTROWS(Dim), REMOVEFILTERS(Fact))",
    "cCat_allF": "CALCULATE(COUNTROWS(Cat), ALL(Fact))",
    "sAexDim": "CALCULATE(SUM(Fact[v]), ALLEXCEPT(Fact, Dim[Name]))",
    "sAexCat": "CALCULATE(SUM(Fact[v]), ALLEXCEPT(Fact, Cat[CatName]))",
    "sAexDay": "CALCULATE(SUM(Fact[v]), ALLEXCEPT(Fact, Fact[day]))",
    "cDim_aexDay": "CALCULATE(COUNTROWS(Dim), ALLEXCEPT(Fact, Fact[day]))",
    "cDim_allDim": "CALCULATE(COUNTROWS(Dim), ALL(Dim))",
    "sAllDim": "CALCULATE(SUM(Fact[v]), ALL(Dim))",
    "sAllCat": "CALCULATE(SUM(Fact[v]), ALL(Cat))",
    "sAllTwin": "CALCULATE(SUM(Fact[v]), ALL(Twin))",
    "sAllTags": "CALCULATE(SUM(Fact[v]), ALL(Tags))",
}


def cell(measure, group=None, slicers=None):
    """One visual cell: the group as grouping filters, the slicers as the
    outer selection, as pbix_evaluate_dax(group_by=..., selected_...) has it."""
    group, slicers = group or {}, slicers or {}
    fc = {**slicers, **{k: [v] for k, v in group.items()}}
    return de.evaluate_measures_smart(
        [measure], TABLES, MEASURES, fc, relationships=RELS, simulate_row_context=False,
        group_by=set(group) or None, selected_filters=slicers if group else None)[measure]


def same(got, want):
    if want is None or got is None:
        return got is want
    return abs(float(got) - float(want)) < 1e-9


# (measure, group, slicers, Desktop) -- 0.9.117 differed on every row marked *
CASES = [
    ("pct", {"Dim.Name": "p"}, {}, 16 / 45),                     # * 1
    ("pct", {"Dim.Name": "q"}, {}, 20 / 45),                     # * 1
    ("pct", {"Dim.Name": "r"}, {}, 9 / 45),                      # * 1
    ("asF", {"Dim.Name": "q"}, {}, 5),                           # * 7
    ("asF", {"Dim.Name": "r"}, {}, 5),                           # * 9
    ("asF", {"Cat.CatName": "y"}, {}, 5),                        # * snowflake: 9
    ("asTwin", {"Dim.Name": "q"}, {}, 5),                        # * 1:1: 7
    ("asTwin", {"Cat.CatName": "y"}, {}, 5),                     # * Twin -> Dim -> Cat: 9
    ("asDim", {"Twin.Tw": "t3"}, {}, 5),                         # * 1:1 the other way: 9
    ("cDim_asF", {"Dim.Name": "q"}, {}, 3),                      # * the dimension's own filter goes: 1
    ("cCat_asF", {"Cat.CatName": "y"}, {}, 2),                   # * 1
    ("asF", {"Dim.Name": "q"}, {"Cat.CatName": ["x"]}, 5),       # * the slicer stays: 7
    ("cDim_asF", {"Dim.Name": "q"}, {"Cat.CatName": ["x"]}, 2),  # * 1
    ("cDim_asF", {"Dim.Name": "p"}, {"Dim.Name": ["p", "q"]}, 2),    # * 1
    ("pct", {"Dim.Name": "p"}, {"Dim.Name": ["p", "q"]}, 16 / 36),   # * 1
    # What must NOT change
    ("cTwin_asDim", {"Dim.Name": "q"}, {}, 3),  # Dim's own grouping, through the 1:1
    ("asTags", {"Dim.Name": "q"}, {}, 7),       # Tags' expanded table is Tags
    ("asF", {"Tags.TagName": "beta"}, {}, 7),   # many-to-many expands nothing
    ("asDim", {"Fact.day": 2}, {}, 9),          # Fact is not in Dim's expanded table
    ("asF", {"Fact2.w": "w2"}, {}, 9),          # Fact2 reaches Fact through a both-ways hop
    ("asF", {}, {"Cat.CatName": ["x"]}, 5),     # no grouping: every filter is a selection
    ("cDim_asF", {}, {"Cat.CatName": ["x"]}, 2),
]


@pytest.mark.parametrize("measure,group,slicers,want", CASES,
                         ids=[f"{m}|{g}|{s}" for m, g, s, _w in CASES])
def test_allselected_matches_desktop(measure, group, slicers, want):
    got = cell(measure, group, slicers)
    assert same(got, want), (measure, group, slicers, got, want)


def test_grouped_tool_answers_the_share_of_total(tmp_path):
    """The user-facing path: pbix_evaluate_dax_grouped over a built file."""
    import json

    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    b = PBIXBuilder("i100")
    b.add_table("Dim", [{"name": "k", "data_type": "Int64"}, {"name": "Name", "data_type": "String"}],
                rows=[{"k": 1, "Name": "p"}, {"k": 2, "Name": "q"}, {"k": 3, "Name": "r"}])
    b.add_table("Fact", [{"name": "k", "data_type": "Int64"}, {"name": "v", "data_type": "Int64"}],
                rows=[{"k": k, "v": v} for k, v in ((1, 5), (2, 7), (3, 9), (1, 11), (2, 13))])
    b.add_relationship("Fact", "k", "Dim", "k")
    b.add_measure("Fact", "pct", MEASURES["pct"])
    p = tmp_path / "i100.pbix"
    b.save(str(p))
    assert json.loads(S.pbix_open(str(p), "i100"))["success"]
    try:
        r = json.loads(S.pbix_evaluate_dax_grouped("i100", "pct", "Dim.Name"))
    finally:
        S.pbix_close("i100")
    got = {g["key"]["Name"]: g["values"]["pct"] for g in r["data"]["groups"]}
    assert got == pytest.approx({"p": 16 / 45, "q": 20 / 45, "r": 9 / 45})   # 0.9.117: 1, 1, 1
