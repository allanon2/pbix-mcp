"""Issue #104 (PR #98 by @allanon2): PBIXBuilder visual bindings.

1. Two tables with the same initial got the same query alias: _alias_for took
   the taken set AFTER assigning and minus the candidate, so it never saw a
   collision ('Region' and 'Range' were both "r").
2. A visual config may bind any roles ({"roles": {role: [field, ...]}}) with
   implicit aggregations named and coded as Desktop's own customVisualsHost.js
   has them (Sum=0 .. Variance=8; "Count" is code 2, the field well's Count
   (Distinct)), and carry formatting objects and visual filters; a page takes
   filters and interactions (its section config's relationships).
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from pbix_mcp.builder import PBIXBuilder

pytestmark = pytest.mark.unit

NORTH = {"name": "fNorth", "type": "Categorical", "howCreated": 1,
         "expression": {"Column": {"Expression": {"SourceRef": {"Entity": "Region"}}, "Property": "Region"}},
         "filter": {"Version": 2, "From": [{"Name": "r", "Entity": "Region", "Type": 0}],
                    "Where": [{"Condition": {"In": {
                        "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "r"}},
                                                    "Property": "Region"}}],
                        "Values": [[{"Literal": {"Value": "'North'"}}]]}}}]}}
SUBTOTALS_OFF = {"subTotals": [{"properties": {"rowSubtotals": {"expr": {"Literal": {"Value": "false"}}}}}]}


def _builder():
    b = PBIXBuilder("i104")
    b.add_table("Region", [{"name": "Region", "data_type": "String"}], rows=[{"Region": "North"}])
    b.add_table("Range", [{"name": "Band", "data_type": "String"}], rows=[{"Band": "lo"}])
    b.add_table("Sales", [{"name": "Region", "data_type": "String"}, {"name": "Band", "data_type": "String"},
                          {"name": "Amount", "data_type": "Double"}],
                rows=[{"Region": "North", "Band": "lo", "Amount": 1.0}])
    b.add_relationship("Sales", "Region", "Region", "Region")
    b.add_relationship("Sales", "Band", "Range", "Band")
    b.add_measure("Sales", "Total", "SUM(Sales[Amount])")
    return b


def _layout(b):
    return json.loads(zipfile.ZipFile(io.BytesIO(b.build())).read("Report/Layout").decode("utf-16-le"))


def _visuals(lay):
    return [(vc, json.loads(vc["config"])["singleVisual"]) for vc in lay["sections"][0]["visualContainers"]]


def test_two_tables_with_one_initial_get_their_own_aliases():
    b = _builder()
    b.add_page("P", [{"type": "tableEx", "config": {"columns": [
        {"table": "Region", "column": "Region"}, {"table": "Range", "column": "Band"}, {"measure": "Total"}]}}])
    (_vc, sv), = _visuals(_layout(b))
    frm = [(f["Name"], f["Entity"]) for f in sv["prototypeQuery"]["From"]]
    assert frm == [("r", "Region"), ("r1", "Range"), ("s", "Sales")]     # 0.9.117: r, r, s
    by_alias = dict(frm)
    for sel in sv["prototypeQuery"]["Select"]:
        node = sel.get("Column") or sel.get("Measure")
        assert by_alias[node["Expression"]["SourceRef"]["Source"]] == sel["Name"].split(".")[0]


def test_roles_bind_a_matrix_and_aggregations_use_desktops_names():
    b = _builder()
    aggs = ["sum", "average", "distinctCount", "min", "max", "countNonNull", "median", "stdDev", "variance"]
    b.add_page("P", [
        {"type": "pivotTable", "name": "m", "objects": SUBTOTALS_OFF, "filters": [NORTH],
         "config": {"roles": {"Rows": [{"table": "Region", "column": "Region"}],
                              "Columns": [{"table": "Range", "column": "Band"}],
                              "Values": [{"measure": "Total"}] + [
                                  {"table": "Sales", "column": "Amount", "aggregation": a} for a in aggs]}}},
        {"type": "card", "name": "c", "config": {"measure": "Total"}},
    ], filters=[NORTH], interactions=[{"source": "m", "target": "c", "type": 3}])
    lay = _layout(b)
    (vc, sv), _card = _visuals(lay)
    assert {r: [i["queryRef"] for i in items] for r, items in sv["projections"].items()} == {
        "Rows": ["Region.Region"], "Columns": ["Range.Band"],
        "Values": ["Sales.Total", "Sum(Sales.Amount)", "Avg(Sales.Amount)", "Count(Sales.Amount)",
                   "Min(Sales.Amount)", "Max(Sales.Amount)", "CountNonNull(Sales.Amount)",
                   "Median(Sales.Amount)", "StandardDeviation(Sales.Amount)", "Variance(Sales.Amount)"]}
    codes = [(s["Name"], s["Aggregation"]["Function"]) for s in sv["prototypeQuery"]["Select"]
             if "Aggregation" in s]
    assert [c for _n, c in codes] == [0, 1, 2, 3, 4, 5, 6, 7, 8]
    assert sv["objects"] == SUBTOTALS_OFF
    assert json.loads(vc["filters"]) == [NORTH]
    assert "query" in vc and "dataTransforms" in vc       # compiled like every data visual
    sec = lay["sections"][0]
    assert json.loads(sec["filters"]) == [NORTH]
    assert json.loads(sec["config"])["relationships"] == [{"source": "m", "target": "c", "type": 3}]


def test_count_alone_is_refused_as_ambiguous():
    b = _builder()
    b.add_page("P", [{"type": "tableEx", "config": {"roles": {"Values": [
        {"table": "Sales", "column": "Amount", "aggregation": "count"}]}}}])
    with pytest.raises(ValueError, match="ambiguous"):
        b.build()


def test_a_page_without_filters_writes_an_empty_list():
    b = _builder()
    b.add_page("P")
    sec = _layout(b)["sections"][0]
    assert sec["filters"] == "[]"        # on every Desktop-authored page
    assert "config" not in sec
