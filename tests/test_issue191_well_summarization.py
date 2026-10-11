"""Issue #191: a bare column is summarized as Desktop's field well does.

Desktop decides by the kind of the well (its visuals' capabilities in
desktop.min.js) and the column's default summarization
(VisualAuthoringService.shouldPreferGroupToAggregate, createExprWithAggregate,
SQAggregationOperationsService.defaultAggregate):

- a grouping well keeps the column bare;
- a well that takes only measures always summarizes it: a number by its
  default summarization (Default is Sum), text or a date by Min where the
  well accepts them ("First" / "Earliest"), otherwise CountNonNull, a
  coordinate by Avg;
- a grouping-or-measure well summarizes only a number whose default
  summarization is not None.

The binder used a fixed list (Y / Values / Y2 / Size; Sum or CountNonNull),
so it left Tooltips, Gradient, a gauge's targets and a scatter's X bare --
a bare column there is grouped by, and Desktop draws one bar per value (a
pbix-mcp-built column chart with a numeric tooltip: 5 bars of 1 instead of
3 and 2) -- and counted text in a card where Desktop shows "First".
"""

import copy
import json
import os
import tempfile

import pytest

from pbix_mcp.report_binding import compile_visual_binding

pytestmark = pytest.mark.unit

_TYPES = {"T.Name": "String", "T.Amount": "Double", "T.When": "DateTime", "T.Flag": "Boolean",
          "T.Lat": "Double", "T.Year": "Int64", "T.City": "String"}


def _resolver(summarize=None, categories=None):
    def resolve(e, p, m):
        return None if m else _TYPES.get(f"{e}.{p}")
    resolve.summarize_by = lambda e, p: (summarize or {}).get(p, 1 if _TYPES.get(f"{e}.{p}") in (
        "Double", "Int64") else 2)
    resolve.data_category = lambda e, p: (categories or {}).get(p)
    return resolve


def _col(p):
    return {"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": p}, "Name": f"T.{p}"}


_MEAS = {"Measure": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": "M"}, "Name": "T.M"}


def _bound(vt, proj, sel, resolve=None):
    sv = {"visualType": vt,
          "projections": {r: [{"queryRef": q} for q in qs] for r, qs in proj.items()},
          "prototypeQuery": {"Version": 2, "From": [{"Name": "t", "Entity": "T", "Type": 0}], "Select": sel}}
    work = copy.deepcopy(sv)
    compile_visual_binding(work, resolve or _resolver())
    return {r: [x["queryRef"] for x in v] for r, v in work["projections"].items()}


class TestMeasureWells:
    @pytest.mark.parametrize("col, want", [("Amount", "Sum(T.Amount)"), ("When", "Min(T.When)"),
                                           ("City", "Min(T.City)"), ("Flag", "CountNonNull(T.Flag)")])
    def test_tooltips(self, col, want):
        got = _bound("columnChart", {"Category": ["T.Name"], "Y": ["T.M"], "Tooltips": [f"T.{col}"]},
                     [_col("Name"), _MEAS, _col(col)])
        assert got["Tooltips"] == [want]
        assert got["Category"] == ["T.Name"]

    def test_text_in_a_number_well_is_counted(self):
        # Y requires a number: text there is counted, not given its Min.
        got = _bound("columnChart", {"Category": ["T.Name"], "Y": ["T.When"]}, [_col("Name"), _col("When")])
        assert got["Y"] == ["CountNonNull(T.When)"]

    def test_card_text_is_first(self):
        assert _bound("card", {"Values": ["T.Name"]}, [_col("Name")]) == {"Values": ["Min(T.Name)"]}

    def test_matrix_values_text_is_first(self):
        got = _bound("matrix", {"Rows": ["T.When"], "Values": ["T.Name"]}, [_col("When"), _col("Name")])
        assert got == {"Rows": ["T.When"], "Values": ["Min(T.Name)"]}

    @pytest.mark.parametrize("vt, role", [("gauge", "TargetValue"), ("gauge", "MaxValue"), ("kpi", "Goal"),
                                          ("treemap", "Gradient"), ("columnChart", "Gradient"),
                                          ("cardVisual", "Data"), ("waterfallChart", "Y")])
    def test_numeric_measure_wells(self, vt, role):
        assert _bound(vt, {role: ["T.Amount"]}, [_col("Amount")])[role] == ["Sum(T.Amount)"]

    @pytest.mark.parametrize("sb, func", [(7, "Avg"), (6, "CountNonNull"), (8, "Count"), (4, "Min"), (5, "Max"),
                                          (3, "Sum"), (1, "Sum"), (2, "CountNonNull")])
    def test_default_summarization(self, sb, func):
        got = _bound("columnChart", {"Category": ["T.Name"], "Y": ["T.Amount"]}, [_col("Name"), _col("Amount")],
                     _resolver(summarize={"Amount": sb}))
        assert got["Y"] == [f"{func}(T.Amount)"]

    def test_coordinate_in_a_filled_map_averages(self):
        got = _bound("filledMap", {"Category": ["T.Name"], "Y": ["T.Lat"]}, [_col("Name"), _col("Lat")],
                     _resolver(categories={"Lat": "Latitude"}))
        assert got["Y"] == ["Avg(T.Lat)"]


class TestGroupingOrMeasureWells:
    def test_scatter_x_number_sums(self):
        got = _bound("scatterChart", {"Category": ["T.Name"], "X": ["T.Amount"], "Y": ["T.M"]},
                     [_col("Name"), _col("Amount"), _MEAS])
        assert got["X"] == ["Sum(T.Amount)"]

    def test_dont_summarize_stays_bare(self):
        got = _bound("scatterChart", {"Category": ["T.Name"], "X": ["T.Year"], "Y": ["T.M"]},
                     [_col("Name"), _col("Year"), _MEAS], _resolver(summarize={"Year": 2}))
        assert got["X"] == ["T.Year"]

    def test_text_stays_bare(self):
        got = _bound("multiRowCard", {"Values": ["T.Name", "T.M"]}, [_col("Name"), _MEAS])
        assert got["Values"] == ["T.Name", "T.M"]

    def test_map_coordinates_bare_without_a_location(self):
        got = _bound("azureMap", {"Y": ["T.Lat"], "Size": ["T.M"]}, [_col("Lat"), _MEAS],
                     _resolver(categories={"Lat": "Latitude"}))
        assert got["Y"] == ["T.Lat"]

    def test_map_coordinates_average_beside_a_location(self):
        got = _bound("map", {"Category": ["T.Name"], "Y": ["T.Lat"]}, [_col("Name"), _col("Lat")],
                     _resolver(categories={"Lat": "Latitude"}))
        assert got["Y"] == ["Avg(T.Lat)"]


class TestGroupingWellsAndTables:
    @pytest.mark.parametrize("vt, role", [("columnChart", "Category"), ("columnChart", "Series"),
                                          ("pieChart", "Category"), ("matrix", "Rows"), ("kpi", "TrendLine")])
    def test_grouping_wells_stay_bare(self, vt, role):
        assert _bound(vt, {role: ["T.Amount"]}, [_col("Amount")])[role] == ["T.Amount"]

    def test_table_keeps_raw_columns(self):
        # Deliberately unchanged: a flat table's bare column is "Don't summarize".
        got = _bound("tableEx", {"Values": ["T.Amount", "T.Name"]}, [_col("Amount"), _col("Name")])
        assert got == {"Values": ["T.Amount", "T.Name"]}


def test_calculated_column_typed_by_its_inferred_type(tmp_path):
    """server._report_type_resolver: a calculated column's ExplicitDataType is
    1 (automatic); its type is InferredDataType."""
    from pbix_mcp import server as S
    path = os.path.join(tempfile.mkdtemp(dir=tmp_path), "calc.pbix")
    S.pbix_create(file_path=path, alias="i191", tables_json=json.dumps(
        [{"name": "T", "columns": [{"name": "Amount", "data_type": "Double"}], "rows": [{"Amount": 1.5}]}]))
    try:
        S.pbix_datamodel_add_calculated_column(alias="i191", table_name="T", column_name="Twice",
                                               dax="T[Amount] * 2")
        resolve = S._report_type_resolver(S._open_files["i191"])
        assert resolve("T", "Twice", False) == "Double"
        assert resolve.summarize_by("T", "Amount") in (1, 3)
    finally:
        S.pbix_close(alias="i191")
