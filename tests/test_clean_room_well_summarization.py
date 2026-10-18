"""A bare column dropped into a field well is summarized as Desktop saves it
-- re-derived clean-room on 2026-10-19 (docs/reverse-engineering/experiments/
2026-10-19-clean-room-remediation.md), replacing 0.9.140's rules (#191),
which cited Desktop's application script.

Evidence: the 276 column bindings of the 28 community Desktop-saved files in
test_samples (well, column type, default summarization, data category, and
what Desktop saved), Microsoft's published semantic query schema (the
aggregate codes) and Microsoft Learn's "Work with aggregates" (defaults the
files do not show). Each case names its source.
"""

import copy
import json
import os
import tempfile

import pytest

from pbix_mcp.report_binding import QUERY_AGGREGATES, compile_visual_binding

pytestmark = pytest.mark.unit

_TYPES = {"T.Name": "String", "T.Amount": "Double", "T.When": "DateTime", "T.Flag": "Boolean",
          "T.Lat": "Double", "T.Year": "Int64", "T.City": "String"}


def _resolver(summarize=None, categories=None):
    """Column types; default summarization as the model stores it -- a number
    Default (1), anything else None (2) -- unless given."""
    def resolve(e, p, m):
        return None if m else _TYPES.get(f"{e}.{p}")
    resolve.summarize_by = lambda e, p: (summarize or {}).get(p, 1 if _TYPES.get(f"{e}.{p}") in (
        "Double", "Int64") else 2)
    resolve.data_category = lambda e, p: (categories or {}).get(p)
    return resolve


def _col(p):
    return {"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": p}, "Name": f"T.{p}"}


def _bound(vt, proj, sel, resolve=None):
    sv = {"visualType": vt,
          "projections": {r: [{"queryRef": q} for q in qs] for r, qs in proj.items()},
          "prototypeQuery": {"Version": 2, "From": [{"Name": "t", "Entity": "T", "Type": 0}], "Select": sel}}
    work = copy.deepcopy(sv)
    compile_visual_binding(work, resolve or _resolver())
    return {r: [x["queryRef"] for x in v] for r, v in work["projections"].items()}


def test_aggregate_codes_and_names():
    """The public QueryAggregateFunction codes 0-8; Sum / Avg / Min / Max as
    the community files name them."""
    assert QUERY_AGGREGATES == {0: "Sum", 1: "Avg", 2: "DistinctCount", 3: "Min", 4: "Max",
                                5: "CountNonNull", 6: "Median", 7: "StandardDeviation", 8: "Variance"}


class TestGroupingWells:
    """Category, Rows, Columns, Series, Target: bare, even a summing number
    (over 200 bindings in the files, e.g. barChart Category Int64 x10)."""

    @pytest.mark.parametrize("vt, role", [("columnChart", "Category"), ("columnChart", "Series"),
                                          ("pivotTable", "Rows"), ("pivotTable", "Columns"),
                                          ("keyDriversVisual", "Target"), ("cardVisual", "Rows")])
    def test_stay_bare(self, vt, role):
        assert _bound(vt, {role: ["T.Year"]}, [_col("Year")]) == {role: ["T.Year"]}

    def test_text_in_explain_by_stays_bare(self):
        """keyDriversVisual / decompositionTreeVisual ExplainBy text: 17 of 17 bare."""
        assert _bound("keyDriversVisual", {"ExplainBy": ["T.City"]}, [_col("City")]) == \
            {"ExplainBy": ["T.City"]}


class TestTablesSlicersAndCards:
    def test_table_and_slicers_keep_raw_columns(self):
        """tableEx Values 12 of 12 bare; slicers 80 of 80 bare, numbers too."""
        for vt in ("tableEx", "slicer", "listSlicer", "advancedSlicerVisual"):
            assert _bound(vt, {"Values": ["T.Amount", "T.Name"]}, [_col("Amount"), _col("Name")]) == \
                {"Values": ["T.Amount", "T.Name"]}, vt

    def test_multi_row_card_lists_text_and_sums_a_number(self):
        """multiRowCard Values text 8 of 8 bare; a number: Learn's default."""
        assert _bound("multiRowCard", {"Values": ["T.City", "T.Amount"]}, [_col("City"), _col("Amount")]) == \
            {"Values": ["T.City", "Sum(T.Amount)"]}

    def test_card_text_is_first(self):
        """card Values text -> Min, 2 of 2 (2 files)."""
        assert _bound("card", {"Values": ["T.City"]}, [_col("City")]) == {"Values": ["Min(T.City)"]}

    def test_matrix_values_text_is_first(self):
        """pivotTable Values text -> Min, 2 of 2."""
        assert _bound("pivotTable", {"Rows": ["T.Year"], "Values": ["T.City"]}, [_col("Year"), _col("City")]) \
            == {"Rows": ["T.Year"], "Values": ["Min(T.City)"]}


class TestValueWells:
    def test_number_by_its_default_summarization(self):
        """Y / Analyze / ExplainBy numbers: Sum -> Sum (6), Average -> Avg (1);
        the other codes matched by name to the public QueryAggregateFunction."""
        for sb, name in [(1, "Sum"), (3, "Sum"), (7, "Avg"), (4, "Min"), (5, "Max"), (6, "CountNonNull"),
                         (8, "DistinctCount")]:
            got = _bound("columnChart", {"Category": ["T.Name"], "Y": ["T.Amount"]},
                         [_col("Name"), _col("Amount")], _resolver(summarize={"Amount": sb}))
            assert got["Y"] == [f"{name}(T.Amount)"], sb
        assert _bound("decompositionTreeVisual", {"Analyze": ["T.Amount"]}, [_col("Amount")]) == \
            {"Analyze": ["Sum(T.Amount)"]}
        assert _bound("keyDriversVisual", {"ExplainBy": ["T.Amount"]}, [_col("Amount")],
                      _resolver(summarize={"Amount": 7})) == {"ExplainBy": ["Avg(T.Amount)"]}

    def test_dont_summarize_keeps_a_number_bare(self):
        """Learn: "Do Not Summarize ... treats each value separately"."""
        assert _bound("columnChart", {"Category": ["T.Name"], "Y": ["T.Amount"]}, [_col("Name"), _col("Amount")],
                      _resolver(summarize={"Amount": 2}))["Y"] == ["T.Amount"]

    @pytest.mark.parametrize("col, want", [("Amount", "Sum(T.Amount)"), ("City", "Min(T.City)"),
                                           ("When", "Min(T.When)"), ("Flag", "Min(T.Flag)")])
    def test_tooltips(self, col, want):
        """Tooltips text -> Min, 3 of 3 (2 files); a number by its summarization."""
        got = _bound("columnChart", {"Category": ["T.Name"], "Tooltips": [f"T.{col}"]}, [_col("Name"), _col(col)])
        assert got["Tooltips"] == [want]

    def test_text_in_a_chart_value_well_is_counted(self):
        """No file has one; Learn: a categorical field in a numbers-only bucket is counted."""
        assert _bound("columnChart", {"Category": ["T.Year"], "Y": ["T.City"]}, [_col("Year"), _col("City")])["Y"] \
            == ["CountNonNull(T.City)"]

    def test_new_card_data_and_scatter_x(self):
        """cardVisual Data holds measures (74 in 14 files); scatter X does (7 of 7)
        and Learn: scatter charts require aggregated X and Y."""
        assert _bound("cardVisual", {"Data": ["T.Amount"]}, [_col("Amount")]) == {"Data": ["Sum(T.Amount)"]}
        assert _bound("scatterChart", {"Category": ["T.Name"], "X": ["T.Amount"]},
                      [_col("Name"), _col("Amount")])["X"] == ["Sum(T.Amount)"]

    @pytest.mark.xfail(reason="no community Desktop file uses these wells: settled by a Desktop save "
                              "(experiment 2026-10-19, Open)", strict=True)
    @pytest.mark.parametrize("vt, role", [("gauge", "TargetValue"), ("gauge", "MaxValue"), ("kpi", "Goal"),
                                          ("treemap", "Gradient"), ("columnChart", "Gradient")])
    def test_wells_awaiting_a_desktop_save(self, vt, role):
        assert _bound(vt, {role: ["T.Amount"]}, [_col("Amount")]) == {role: ["Sum(T.Amount)"]}


class TestCoordinates:
    def test_azure_map_places_points_by_bare_coordinates(self):
        """azureMap X / Y with Longitude / Latitude: 4 of 4 bare."""
        assert _bound("azureMap", {"X": ["T.Lat"], "Y": ["T.Lat"]}, [_col("Lat")]) == \
            {"X": ["T.Lat"], "Y": ["T.Lat"]}

    def test_a_coordinate_in_a_value_well_averages(self):
        """0.9.139's rule (ledger issues-5): coordinates average, never sum."""
        got = _bound("columnChart", {"Category": ["T.Name"], "Y": ["T.Lat"]}, [_col("Name"), _col("Lat")],
                     _resolver(categories={"Lat": "Latitude"}))
        assert got["Y"] == ["Avg(T.Lat)"]


def test_calculated_column_typed_by_its_inferred_type(tmp_path):
    """server._report_type_resolver: a calculated column's ExplicitDataType is
    1 (automatic); its type is InferredDataType (the Tabular model's public
    documentation of ExplicitDataType / InferredDataType)."""
    from pbix_mcp import server as S
    path = os.path.join(tempfile.mkdtemp(dir=tmp_path), "calc.pbix")
    S.pbix_create(file_path=path, alias="cr191", tables_json=json.dumps(
        [{"name": "T", "columns": [{"name": "Amount", "data_type": "Double"}], "rows": [{"Amount": 1.5}]}]))
    try:
        S.pbix_datamodel_add_calculated_column(alias="cr191", table_name="T", column_name="Twice",
                                               dax="T[Amount] * 2")
        resolve = S._report_type_resolver(S._open_files["cr191"])
        assert resolve("T", "Twice", False) == "Double"
        assert resolve.summarize_by("T", "Amount") in (1, 3)
    finally:
        S.pbix_close(alias="cr191")
