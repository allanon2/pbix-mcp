"""Issues #187, #188, #190: compile_visual_binding on visuals Desktop authors.

- #187: a List slicer (``listSlicer``) keeps the bare column in Values, as the
  classic and Button slicers do (all 9 in the community files).
- #188: a multi-row card keeps a bare column in Values (Root Cause Analysis'
  8 cards), and an Azure map keeps bare coordinate columns in its Y / X wells
  (Map with Pie Chart Bubbles).
- #190: a hierarchy level (``HierarchyLevel`` select) compiled with
  ``KeyError: 'Column'``. It now compiles as Desktop saves it: the level's
  expr with its SourceRef rewritten to the entity, NativeReferenceName
  "<hierarchy> <level>" (an auto date/time hierarchy: "<column> <level>"),
  displayName / Restatement the level. The expected values below are the
  query / dataTransforms Desktop saved for Cars Sales' slicer, Briqlab Pink's
  area chart and Dashboard Help Modal View's column chart.
"""

import copy
import json
import os
import tempfile
import zipfile

import pytest

from pbix_mcp.report_binding import compile_visual_binding

pytestmark = pytest.mark.unit


def _col(src, prop, name):
    return {"Column": {"Expression": {"SourceRef": {"Source": src}}, "Property": prop}, "Name": name}


def _meas(src, prop, name):
    return {"Measure": {"Expression": {"SourceRef": {"Source": src}}, "Property": prop}, "Name": name}


def _compile(sv, resolve=lambda e, p, m: None):
    work = copy.deepcopy(sv)
    q, dt = compile_visual_binding(work, resolve)
    return work, q, dt


def _query_selects(q):
    return q["Commands"][0]["SemanticQueryDataShapeCommand"]["Query"]["Select"]


# --- #187 ------------------------------------------------------------------

def _slicer_sv(vtype):
    return {"visualType": vtype,
            "projections": {"Values": [{"queryRef": "T.Grp", "active": True}]},
            "prototypeQuery": {"Version": 2, "From": [{"Name": "t", "Entity": "T", "Type": 0}],
                               "Select": [_col("t", "Grp", "T.Grp")]}}


class TestIssue187ListSlicer:
    @pytest.mark.parametrize("vtype", ["slicer", "advancedSlicerVisual", "listSlicer"])
    def test_values_column_stays_bare(self, vtype):
        sv = _slicer_sv(vtype)
        work, q, dt = _compile(sv, lambda e, p, m: "String")
        assert work["projections"] == sv["projections"]
        assert work["prototypeQuery"] == sv["prototypeQuery"]
        assert "Column" in _query_selects(q)[0]

    def test_list_slicer_compiles_as_a_slicer(self):
        _, q, dt = _compile(_slicer_sv("listSlicer"), lambda e, p, m: "String")
        binding = q["Commands"][0]["SemanticQueryDataShapeCommand"]["Binding"]
        assert binding["DataReduction"] == {"DataVolume": 3, "Primary": {"Window": {}}}
        assert binding["IncludeEmptyGroups"] is True
        assert dt["visualElements"][0]["DataRoles"] == [
            {"Name": "Values", "Projection": 0, "isActive": True}]

    def test_issue_repro_through_add_visual(self):
        from pbix_mcp import server as s
        path = os.path.join(tempfile.mkdtemp(), "ls.pbix")
        s.pbix_create(file_path=path, alias="i187", tables_json=json.dumps(
            [{"name": "T", "columns": [{"name": "Grp", "data_type": "String"}],
              "rows": [{"Grp": "a"}, {"Grp": "b"}]}]))
        try:
            for vtype in ("slicer", "advancedSlicerVisual", "listSlicer"):
                cfg = {"singleVisual": _slicer_sv(vtype)}
                s.pbix_add_visual(alias="i187", page_index=0, visual_type=vtype, x=20, y=20,
                                  width=150, height=120, config_json=json.dumps(cfg))
            s.pbix_save(alias="i187")
        finally:
            s.pbix_close(alias="i187")
        lay = json.loads(zipfile.ZipFile(path).read("Report/Layout").decode("utf-16-le"))
        got = {}
        for vc in lay["sections"][0]["visualContainers"]:
            sv = json.loads(vc["config"])["singleVisual"]
            got[sv["visualType"]] = sv["projections"]
        assert got["listSlicer"] == {"Values": [{"queryRef": "T.Grp", "active": True}]}
        assert got["slicer"] == got["listSlicer"] == got["advancedSlicerVisual"]


# --- #188 ------------------------------------------------------------------

_TYPES = {"T.Cat": "String", "S.Latitude": "Double", "S.Longitude": "Double", "T.Amount": "Double"}


def _resolve(e, p, m):
    return None if m else _TYPES.get(f"{e}.{p}")


_FROM = [{"Name": "t", "Entity": "T", "Type": 0}, {"Name": "s", "Entity": "S", "Type": 0}]


class TestIssue188BareColumnRoles:
    def test_multi_row_card_keeps_its_column(self):
        sv = {"visualType": "multiRowCard",
              "projections": {"Values": [{"queryRef": "T.Cat"}, {"queryRef": "T.M"}]},
              "prototypeQuery": {"Version": 2, "From": _FROM,
                                 "Select": [_col("t", "Cat", "T.Cat"), _meas("t", "M", "T.M")]}}
        work, q, dt = _compile(sv, _resolve)
        assert work["projections"] == sv["projections"]
        assert work["prototypeQuery"] == sv["prototypeQuery"]
        assert [s["Name"] for s in _query_selects(q)] == ["T.Cat", "T.M"]

    def test_multi_row_card_numeric_column_by_its_summarization(self):
        # Values takes a grouping or a measure (issue #191): a number set to
        # "Don't summarize" stays bare, one with a default summarization is
        # summarized as Desktop's field well does.
        sv = {"visualType": "multiRowCard",
              "projections": {"Values": [{"queryRef": "T.Amount"}]},
              "prototypeQuery": {"Version": 2, "From": _FROM, "Select": [_col("t", "Amount", "T.Amount")]}}

        def resolve(sb):
            def r(e, p, m):
                return _resolve(e, p, m)
            r.summarize_by = lambda e, p: sb
            return r
        work, _, _ = _compile(sv, resolve(2))
        assert work["projections"] == sv["projections"]
        work, _, _ = _compile(sv, resolve(1))
        assert work["projections"] == {"Values": [{"queryRef": "Sum(T.Amount)"}]}

    def test_azure_map_keeps_its_coordinates(self):
        sv = {"visualType": "azureMap",
              "projections": {"Y": [{"queryRef": "S.Latitude"}], "X": [{"queryRef": "S.Longitude"}],
                              "Size": [{"queryRef": "T.M"}]},
              "prototypeQuery": {"Version": 2, "From": _FROM,
                                 "Select": [_col("s", "Latitude", "S.Latitude"),
                                            _col("s", "Longitude", "S.Longitude"),
                                            _meas("t", "M", "T.M")]}}
        work, q, dt = _compile(sv, _resolve)
        assert work["projections"] == sv["projections"]
        assert work["prototypeQuery"] == sv["prototypeQuery"]
        assert [s["queryName"] for s in dt["selects"]] == ["S.Latitude", "S.Longitude", "T.M"]

    def test_azure_map_size_column_still_aggregates(self):
        # Size is a measure well: a bare numeric column there is summed, as before.
        sv = {"visualType": "azureMap",
              "projections": {"Size": [{"queryRef": "T.Amount"}]},
              "prototypeQuery": {"Version": 2, "From": _FROM, "Select": [_col("t", "Amount", "T.Amount")]}}
        work, _, _ = _compile(sv, _resolve)
        assert work["projections"] == {"Size": [{"queryRef": "Sum(T.Amount)"}]}

    def test_chart_values_column_still_aggregates(self):
        sv = {"visualType": "clusteredColumnChart",
              "projections": {"Category": [{"queryRef": "T.Cat"}], "Y": [{"queryRef": "T.Amount"}]},
              "prototypeQuery": {"Version": 2, "From": _FROM,
                                 "Select": [_col("t", "Cat", "T.Cat"), _col("t", "Amount", "T.Amount")]}}
        work, _, _ = _compile(sv, _resolve)
        assert work["projections"]["Y"] == [{"queryRef": "Sum(T.Amount)"}]


# --- #190 ------------------------------------------------------------------

def _level(src, hierarchy, level, name):
    return {"HierarchyLevel": {"Expression": {"Hierarchy": {"Expression": {"SourceRef": {"Source": src}},
                                                            "Hierarchy": hierarchy}}, "Level": level},
            "Name": name}


def _variation_level(src, prop, level, name):
    return {"HierarchyLevel": {"Expression": {"Hierarchy": {
        "Expression": {"PropertyVariationSource": {"Expression": {"SourceRef": {"Source": src}},
                                                   "Name": "Variation", "Property": prop}},
        "Hierarchy": "Date Hierarchy"}}, "Level": level}, "Name": name}


def _entity_level(entity, hierarchy, level):
    return {"HierarchyLevel": {"Expression": {"Hierarchy": {"Expression": {"SourceRef": {"Entity": entity}},
                                                            "Hierarchy": hierarchy}}, "Level": level}}


class TestIssue190HierarchyLevels:
    def test_issue_repro_compiles(self):
        sv = {"visualType": "slicer",
              "projections": {"Values": [{"queryRef": "Date.Calendar.Year", "active": True},
                                         {"queryRef": "Date.Calendar.Month", "active": True}]},
              "prototypeQuery": {"Version": 2, "From": [{"Name": "d", "Entity": "Date", "Type": 0}],
                                 "Select": [_level("d", "Calendar", "Year", "Date.Calendar.Year"),
                                            _level("d", "Calendar", "Month", "Date.Calendar.Month")]}}
        work, q, dt = _compile(sv)
        assert work["prototypeQuery"] == sv["prototypeQuery"]
        assert work["projections"] == sv["projections"]
        # Cars Sales' slicer, as Desktop saved it.
        assert _query_selects(q) == [
            {**_level("d", "Calendar", "Year", "Date.Calendar.Year"), "NativeReferenceName": "Calendar Year"},
            {**_level("d", "Calendar", "Month", "Date.Calendar.Month"), "NativeReferenceName": "Calendar Month"},
        ]
        assert [{k: s[k] for k in ("displayName", "queryName", "roles", "expr")} for s in dt["selects"]] == [
            {"displayName": "Year", "queryName": "Date.Calendar.Year", "roles": {"Values": True},
             "expr": _entity_level("Date", "Calendar", "Year")},
            {"displayName": "Month", "queryName": "Date.Calendar.Month", "roles": {"Values": True},
             "expr": _entity_level("Date", "Calendar", "Month")},
        ]
        assert [s["Restatement"] for s in dt["queryMetadata"]["Select"]] == ["Year", "Month"]

    def test_chart_axis_levels_are_not_aggregated(self):
        sv = {"visualType": "columnChart",
              "projections": {"Category": [{"queryRef": "Date.Year-Quarter.Year", "active": True},
                                           {"queryRef": "Date.Year-Quarter.Quarter", "active": True}],
                              "Y": [{"queryRef": "#Measures.Sales Won"}]},
              "prototypeQuery": {"Version": 2,
                                 "From": [{"Name": "d", "Entity": "Date", "Type": 0},
                                          {"Name": "#", "Entity": "#Measures", "Type": 0}],
                                 "Select": [_level("d", "Year-Quarter", "Year", "Date.Year-Quarter.Year"),
                                            _level("d", "Year-Quarter", "Quarter", "Date.Year-Quarter.Quarter"),
                                            _meas("#", "Sales Won", "#Measures.Sales Won")]}}
        work, q, dt = _compile(sv, lambda e, p, m: "String")
        assert work["prototypeQuery"] == sv["prototypeQuery"]
        assert work["projections"] == sv["projections"]
        # Dashboard Help Modal View's column chart, as Desktop saved it.
        assert [s.get("NativeReferenceName") for s in _query_selects(q)[:2]] == [
            "Year-Quarter Year", "Year-Quarter Quarter"]
        assert [s["expr"] for s in dt["selects"][:2]] == [
            _entity_level("Date", "Year-Quarter", "Year"), _entity_level("Date", "Year-Quarter", "Quarter")]
        assert [s["roles"] for s in dt["selects"]] == [{"Category": True}, {"Category": True}, {"Y": True}]
        assert dt["projectionOrdering"] == {"Category": [0, 1], "Y": [2]}

    def test_auto_date_time_level(self):
        ref = "Airport Operations.Scheduled_Dep.Variation.Date Hierarchy.Month"
        sv = {"visualType": "areaChart",
              "projections": {"Category": [{"queryRef": ref, "active": True}],
                              "Y": [{"queryRef": "Airport Operations.Total Flights"}]},
              "prototypeQuery": {"Version": 2, "From": [{"Name": "a", "Entity": "Airport Operations", "Type": 0}],
                                 "Select": [_variation_level("a", "Scheduled_Dep", "Month", ref),
                                            _meas("a", "Total Flights", "Airport Operations.Total Flights")]}}
        work, q, dt = _compile(sv)
        assert work["prototypeQuery"] == sv["prototypeQuery"]
        # Briqlab Pink's area chart, as Desktop saved it.
        assert [s.get("NativeReferenceName") for s in _query_selects(q)] == ["Scheduled_Dep Month", "Total Flights"]
        expected_expr = _variation_level("a", "Scheduled_Dep", "Month", ref)
        expected_expr.pop("Name")
        pvs = expected_expr["HierarchyLevel"]["Expression"]["Hierarchy"]["Expression"]["PropertyVariationSource"]
        pvs["Expression"]["SourceRef"] = {"Entity": "Airport Operations"}
        assert dt["selects"][0]["expr"] == expected_expr
        assert dt["selects"][0]["displayName"] == "Month"
        assert dt["queryMetadata"]["Select"][0]["Restatement"] == "Month"

    def test_resolver_sees_the_level_on_its_table(self):
        seen = []

        def resolve(e, p, m):
            seen.append((e, p, m))
            return "Int64" if (e, p) == ("Date", "Year") else None

        sv = {"visualType": "slicer",
              "projections": {"Values": [{"queryRef": "Date.Calendar.Year", "active": True}]},
              "prototypeQuery": {"Version": 2, "From": [{"Name": "d", "Entity": "Date", "Type": 0}],
                                 "Select": [_level("d", "Calendar", "Year", "Date.Calendar.Year")]}}
        _, _, dt = _compile(sv, resolve)
        assert ("Date", "Year", False) in seen
        assert dt["selects"][0]["type"]["underlyingType"] == 260
