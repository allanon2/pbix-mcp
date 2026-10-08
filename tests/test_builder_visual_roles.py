"""PBIXBuilder: explicit visual roles, aggregations, objects, filters and interactions.

What a report needs beyond the built-in config shapes: a matrix's Rows and
Columns, implicit aggregations of any function, formatting objects (subTotals,
a slicer's selection), visual and page filters, and visual interactions. Also:
two tables starting with the same letter get distinct query aliases.
"""
from __future__ import annotations

import io
import json
import zipfile

from pbix_mcp.builder import PBIXBuilder

PAGE_FILTER = {"name": "f", "type": "Categorical", "howCreated": 1,
               "expression": {"Column": {"Expression": {"SourceRef": {"Entity": "Region"}}, "Property": "Region"}},
               "filter": {"Version": 2, "From": [{"Name": "r", "Entity": "Region", "Type": 0}],
                          "Where": [{"Condition": {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "r"}}, "Property": "Region"}}],
                                                          "Values": [[{"Literal": {"Value": "'North'"}}]]}}}]}}
SUBTOTALS_OFF = {"subTotals": [{"properties": {"rowSubtotals": {"expr": {"Literal": {"Value": "false"}}}}}]}


def _layout():
    b = PBIXBuilder("Roles")
    b.add_table("Region", [{"name": "Region", "data_type": "String"}], rows=[{"Region": "North"}, {"Region": "South"}])
    b.add_table("Range", [{"name": "Band", "data_type": "String"}], rows=[{"Band": "A"}])
    b.add_table("Sales", [{"name": "Region", "data_type": "String"}, {"name": "Amount", "data_type": "Double"}],
                rows=[{"Region": "North", "Amount": 1.0}, {"Region": "South", "Amount": 2.0}])
    b.add_relationship("Sales", "Region", "Region", "Region")
    b.add_measure("Sales", "Total", "SUM(Sales[Amount])")
    b.add_page("P", [
        {"type": "pivotTable", "name": "m", "objects": SUBTOTALS_OFF, "filters": [PAGE_FILTER],
         "config": {"roles": {"Rows": [{"table": "Region", "column": "Region"}],
                              "Columns": [{"table": "Range", "column": "Band"}],
                              "Values": [{"measure": "Total"},
                                         {"table": "Sales", "column": "Amount", "aggregation": "median"}]}}},
        {"type": "card", "name": "c", "config": {"measure": "Total"}},
    ], filters=[PAGE_FILTER], interactions=[{"source": "m", "target": "c", "type": 3}])
    data = b.build()
    return json.loads(zipfile.ZipFile(io.BytesIO(data)).read("Report/Layout").decode("utf-16-le"))


def test_roles_objects_filters_and_interactions():
    sec = _layout()["sections"][0]
    assert json.loads(sec["filters"]) == [PAGE_FILTER]
    assert json.loads(sec["config"])["relationships"] == [{"source": "m", "target": "c", "type": 3}]
    m = sec["visualContainers"][0]
    sv = json.loads(m["config"])["singleVisual"]
    assert set(sv["projections"]) == {"Rows", "Columns", "Values"}
    assert sv["objects"] == SUBTOTALS_OFF
    assert json.loads(m["filters"]) == [PAGE_FILTER]
    aggs = [x for x in sv["prototypeQuery"]["Select"] if "Aggregation" in x]
    assert [(a["Aggregation"]["Function"], a["Name"]) for a in aggs] == [(6, "Median(Sales.Amount)")]
    assert "query" in m and "dataTransforms" in m


def test_tables_with_the_same_initial_get_distinct_aliases():
    sv = json.loads(_layout()["sections"][0]["visualContainers"][0]["config"])["singleVisual"]
    aliases = [f["Name"] for f in sv["prototypeQuery"]["From"]]
    assert len(aliases) == len(set(aliases)), aliases
