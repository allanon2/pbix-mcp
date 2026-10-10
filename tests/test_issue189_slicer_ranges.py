"""Issue #189: a slicer's saved range is a range, not the bound as a value.

Desktop's Between slicers save their bounds as Comparison conditions: IT
Support's Date slicers ``Date >= 2024-01-01`` (kind 2), the Artificial
Intelligence Sample's Anomaly Detection slicer ``Date >= 2020-06-01 AND
Date < 2021-06-01``. _extract_default_filters_dict read kinds 1-4 as the
one-value list ``[bound]`` -- an equality: one day -- and dropped the And.
Desktop shows IT Support's ``1- Tickets`` as 11,923 with the file opened as
saved; the defaults gave 28.
"""

import json
import os

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit

_COL = {"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": "Date"}}


def _cmp(kind, iso):
    return {"Comparison": {"ComparisonKind": kind, "Left": _COL,
                           "Right": {"Literal": {"Value": f"datetime'{iso}T00:00:00'"}}}}


def _layout(cond, vtype="slicer"):
    cfg = {"name": "s", "singleVisual": {"visualType": vtype, "objects": {"general": [{"properties": {"filter": {
        "filter": {"Version": 2, "From": [{"Name": "d", "Entity": "dim_Date", "Type": 0}],
                   "Where": [{"Condition": cond}]}}}}]}}}
    return {"sections": [{"visualContainers": [{"config": json.dumps(cfg)}]}]}


def _defaults(cond, vtype="slicer"):
    return S._extract_default_filters_dict("", 0, layout=_layout(cond, vtype))


class TestSavedRanges:
    def test_lower_bound_is_a_range(self):
        # IT Support's Date slicer, on each of its pages.
        assert _defaults(_cmp(2, "2024-01-01")) == {
            "dim_Date.Date": {"op": ">=", "value": "2024-01-01T00:00:00"}}

    @pytest.mark.parametrize("kind, op", [(1, ">"), (2, ">="), (3, "<"), (4, "<=")])
    def test_each_kind(self, kind, op):
        assert _defaults(_cmp(kind, "2024-01-01")) == {
            "dim_Date.Date": {"op": op, "value": "2024-01-01T00:00:00"}}

    def test_between_is_both_bounds(self):
        # The AI Sample's Anomaly Detection slicer: the upper bound is "<" the
        # day after its end date, so each side keeps its own kind.
        cond = {"And": {"Left": _cmp(2, "2020-06-01"), "Right": _cmp(3, "2021-06-01")}}
        assert _defaults(cond) == {"dim_Date.Date": {"all": [
            {"op": ">=", "value": "2020-06-01T00:00:00"},
            {"op": "<", "value": "2021-06-01T00:00:00"}]}}

    def test_equality_stays_a_value(self):
        # What-if parameter slicers (kind 0): SELECTEDVALUE reads one value.
        assert _defaults(_cmp(0, "2024-01-01")) == {"dim_Date.Date": ["2024-01-01T00:00:00"]}

    def test_list_slicer_range(self):
        assert _defaults(_cmp(2, "2024-01-01"), "listSlicer") == {
            "dim_Date.Date": {"op": ">=", "value": "2024-01-01T00:00:00"}}

    def test_in_list_unchanged(self):
        cond = {"In": {"Expressions": [_COL], "Values": [[{"Literal": {"Value": "'a'"}}],
                                                          [{"Literal": {"Value": "'b'"}}]]}}
        assert _defaults(cond) == {"dim_Date.Date": ["a", "b"]}


class TestRangeMatches:
    def test_predicates_select_the_range(self):
        from pbix_mcp.dax.engine import make_value_matcher
        lo = make_value_matcher(_defaults(_cmp(2, "2024-01-01"))["dim_Date.Date"])
        assert lo("2024-01-01T00:00:00") and lo("2025-06-04T00:00:00")
        assert not lo("2023-12-31T00:00:00")
        cond = {"And": {"Left": _cmp(2, "2020-06-01"), "Right": _cmp(3, "2021-06-01")}}
        both = make_value_matcher(_defaults(cond)["dim_Date.Date"])
        assert both("2020-06-01T00:00:00") and both("2021-05-31T00:00:00")
        assert not both("2021-06-01T00:00:00") and not both("2020-05-31T00:00:00")


_IT_SUPPORT = os.path.join(os.path.dirname(__file__), "..", "test_samples", "templates",
                           "IT_Support_Ticket_Desk.pbix")


@pytest.mark.skipif(not os.path.exists(_IT_SUPPORT), reason="IT_Support_Ticket_Desk.pbix not present")
class TestItSupport:
    def test_defaults_keep_every_ticket(self):
        S.pbix_open(file_path=_IT_SUPPORT, alias="i189")
        try:
            for page in (0, -1):
                r = json.loads(S.pbix_evaluate_dax(alias="i189", measures="1- Tickets",
                                                   apply_default_filters=True, page_index=page))
                assert r["results"][0]["value"] == 11923      # Desktop: 11,923
        finally:
            S.pbix_close(alias="i189")
