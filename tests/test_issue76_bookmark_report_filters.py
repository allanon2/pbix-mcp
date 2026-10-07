"""Issue #76 (carried over from #52): `report_filter_json` was written verbatim.

`pbix_add_bookmark`'s own documented example,
`[{"target": {"table", "column"}, "operator": "In", "values": [...]}]`, went
straight into `explorationState.filters.byExpr`. Microsoft's PBIR bookmark
schema makes each entry a FilterContainerState — `name` required, only ten
keys allowed — so every bookmark authored that way was schema-invalid, and
the call answered success.

The shorthand is now converted to the container Desktop writes (28 of 28
bookmark report filters in the corpus: {name, type: "Categorical", filter:
{Version 2, From, Where: In}, expression, howCreated: 1}), typed to the
column; a full container passes through; anything else is refused.

Desktop-verified: a bookmark filters the report only through a report-level
filter card on that column — with no card it does nothing at all — so a
missing card is added (unselected) and reported. With the card, "Only West"
and "Not West" both apply and switch correctly.
"""
import json

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit

SALES = [{"name": "Region", "data_type": "String"},
         {"name": "Year", "data_type": "Int64"},
         {"name": "Day", "data_type": "DateTime"},
         {"name": "Amount", "data_type": "Double"},
         {"name": "Flag", "data_type": "Boolean"}]
ROW = {"Region": "West", "Year": 2024, "Day": "2024-01-31", "Amount": 1.5,
       "Flag": True}
REGION = {"Column": {"Expression": {"SourceRef": {"Entity": "Sales"}},
                     "Property": "Region"}}


@pytest.fixture()
def report(tmp_path):
    alias = "i76_" + tmp_path.name[-6:]
    p = str(tmp_path / "b.pbix")
    assert json.loads(S.pbix_create(p, alias, json.dumps(
        [{"name": "Sales", "columns": SALES, "rows": [ROW]}])))["success"]
    yield alias
    S._open_files.pop(alias, None)
    S._dax_cache.pop(alias, None)


def _shorthand(column="Region", op="In", values=("West",)):
    return {"target": {"table": "Sales", "column": column}, "operator": op,
            "values": list(values)}


def _add(alias, name, entries):
    return json.loads(S.pbix_add_bookmark(
        alias, name, report_filter_json=json.dumps(entries)))


def _layout(alias):
    return S._get_layout(S._open_files[alias]["work_dir"])


def _bookmarks(alias):
    return json.loads(_layout(alias).get("config") or "{}").get("bookmarks", [])


def _cards(alias):
    raw = _layout(alias).get("filters") or "[]"
    return json.loads(raw) if isinstance(raw, str) else raw


def _containers(alias):
    return _bookmarks(alias)[-1]["explorationState"]["filters"]["byExpr"]


def _values(container):
    cond = container["filter"]["Where"][0]["Condition"]
    inner = cond["Not"]["Expression"]["In"] if "Not" in cond else cond["In"]
    return [v[0]["Literal"]["Value"] for v in inner["Values"]]


class TestDocumentedShorthand:

    def test_becomes_desktops_filter_container(self, report):
        r = _add(report, "B", [_shorthand()])
        assert r["success"], r
        (c,) = _containers(report)
        assert set(c) == {"name", "type", "filter", "expression", "howCreated"}
        assert set(c) <= S._BOOKMARK_FILTER_KEYS
        assert c["type"] == "Categorical" and c["howCreated"] == 1
        assert c["expression"] == REGION
        f = c["filter"]
        assert f["Version"] == 2
        assert f["From"] == [{"Name": "s", "Entity": "Sales", "Type": 0}]
        cond = f["Where"][0]["Condition"]["In"]
        assert cond["Expressions"] == [{"Column": {
            "Expression": {"SourceRef": {"Source": "s"}},
            "Property": "Region"}}]
        assert _values(c) == ["'West'"]

    def test_not_in_wraps_the_condition(self, report):
        assert _add(report, "B", [_shorthand(op="NotIn")])["success"]
        (c,) = _containers(report)
        assert "Not" in c["filter"]["Where"][0]["Condition"]
        assert _values(c) == ["'West'"]

    @pytest.mark.parametrize("column,values,expected", [
        ("Region", ["O'Brien", 2024], ["'O''Brien'", "'2024'"]),
        ("Year", [2023, "2022"], ["2023L", "2022L"]),
        ("Amount", [2, 2.5], ["2D", "2.5D"]),
        ("Day", ["2024-01-31"], ["datetime'2024-01-31T00:00:00'"]),
        ("Flag", [True], ["true"]),
    ])
    def test_values_are_typed_to_the_column(self, report, column, values,
                                            expected):
        assert _add(report, "B", [_shorthand(column, values=values)])["success"]
        assert _values(_containers(report)[0]) == expected


class TestReportFilterCard:

    def test_missing_card_is_added_and_reported(self, report):
        r = _add(report, "B", [_shorthand()])
        assert r["data"]["added_report_filter_cards"] == ["'Sales'[Region]"]
        assert "Added a report filter card on 'Sales'[Region]" in r["message"]
        (card,) = _cards(report)
        assert card == {"name": _containers(report)[0]["name"],
                        "expression": REGION, "type": "Categorical",
                        "howCreated": 1}
        assert "filter" not in card     # unselected: "Region is (All)"

    def test_existing_card_is_reused_by_name(self, report):
        lay = _layout(report)
        lay["filters"] = json.dumps([{"name": "Filter", "expression": REGION,
                                      "type": "Categorical", "howCreated": 1}])
        S._set_layout(S._open_files[report]["work_dir"], lay)
        r = _add(report, "B", [_shorthand()])
        assert r["data"]["added_report_filter_cards"] == []
        assert _containers(report)[0]["name"] == "Filter"
        assert len(_cards(report)) == 1

    def test_bookmarks_on_one_column_share_one_card(self, report):
        _add(report, "Only West", [_shorthand()])
        r = _add(report, "Not West", [_shorthand(op="NotIn")])
        assert r["data"]["added_report_filter_cards"] == []
        names = {b["explorationState"]["filters"]["byExpr"][0]["name"]
                 for b in _bookmarks(report)}
        assert len(names) == 1 and len(_cards(report)) == 1


def test_full_container_passes_through(report):
    container = {"name": "Filter", "type": "Categorical",
                 "expression": REGION, "howCreated": 1}
    r = _add(report, "B", [container])
    assert r["success"], r
    assert _containers(report) == [container]
    assert r["data"]["added_report_filter_cards"] == []


@pytest.mark.parametrize("entries,needle", [
    ([{"type": "Categorical"}], "no 'name'"),
    ([{"name": "F", "target": None, "operator": "In"}], "target needs"),
    ([{"name": "F", "values": ["W"]}], "may not carry"),
    ([_shorthand(op="Contains")], "'Contains' is not supported"),
    ([_shorthand(values=())], "non-empty array"),
    ([_shorthand(column="Nope")], "not in the model"),
    ([_shorthand(column="Year", values=[2024.5])], "not a whole number"),
    (["Region"], "not an object"),
])
def test_invalid_input_is_refused_and_nothing_is_written(report, entries,
                                                         needle):
    r = _add(report, "B", entries)
    assert not r["success"], r
    assert needle in r["message"], r["message"]
    assert _bookmarks(report) == []
    assert _cards(report) == []


def test_unparseable_json_is_refused(report):
    r = json.loads(S.pbix_add_bookmark(report, "B", report_filter_json="[{"))
    assert not r["success"] and "JSON" in r["message"]
    assert _bookmarks(report) == []


class TestThinPBIRReport:
    """A report-only file (no DataModel): the column cannot be checked, and
    the card must be written in PBIR's own FilterContainer shape."""

    @pytest.fixture()
    def thin(self, tmp_path):
        from tests.test_pbir_schema_conformance import _pbir_pbix
        alias = "i76p_" + tmp_path.name[-6:]
        assert json.loads(S.pbix_open(_pbir_pbix(tmp_path), alias))["success"]
        yield alias
        S._open_files.pop(alias, None)

    def test_unchecked_column_is_reported(self, thin):
        r = _add(thin, "B", [_shorthand()])
        assert r["success"], r
        assert any("carries no model" in w for w in r.get("warnings") or [])

    def test_card_uses_the_pbir_shape(self, thin):
        _add(thin, "B", [_shorthand()])
        card = [c for c in _cards(thin) if c.get("field") == REGION]
        assert card and card[0]["howCreated"] == "User"
        assert "expression" not in card[0]
