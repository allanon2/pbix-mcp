"""Issue #233: pbix_evaluate_dax_batch, and IFERROR / ISERROR answers that
do not depend on the measure memo.

What was missing: a client querying a visual row by row called
pbix_evaluate_dax once per row. Every call resolved the file, the model and
the measure list anew and began with an empty measure memo, so values no row
changes (the totals a RANKX ranks, an ALL(...) denominator) were computed
again for each row. pbix_evaluate_dax_batch takes the rows' contexts in one
call and serves them from one memo.

Where the expected values come from: each batch element is compared, as
parsed JSON, with pbix_evaluate_dax's own response for that context
(apply_default_filters=False) -- values, statuses, messages, calculation-item
format strings, warnings. The single-call tool is the reference; the Desktop
batteries (b200, b210, b234) check it.

Two engine faults surfaced once a memo outlived one context. Both made an
answer depend on what had been memoized before, so the fresh evaluation and
the memo hit must now agree:

1. A memo hit did not report the nested measure errors its evaluation had
   degraded to BLANK, which IFERROR / ISERROR count (#135): IFERROR([Bumped],
   40) with Bumped = [Fails] + 2 answered 40 alone and 2 once Bumped was
   memoized, and a repeated context reported an erroring measure as blank.
   Desktop's IFERROR([Bad], 13) is 13 (build_b134.py).
2. An error an inner IFERROR had caught stayed counted: with Contained =
   IFERROR([Fails], 6), IFERROR([Contained], 66) was 66 where Contained was
   evaluated afresh and 6 where it came from the memo. Contained is the
   value 6, so the answers are 6, ISERROR FALSE, and 6 per row of a SUMX.
   No Desktop probe nests the IFERROR in a referenced measure; the nearest,
   VAR x = IFERROR(ERROR("boom"), 1) RETURN x, is 1 (build_b134.py): a caught
   error does not travel on.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta

import pytest

from pbix_mcp import server
from pbix_mcp.builder import PBIXBuilder
from pbix_mcp.dax import engine as dax

pytestmark = pytest.mark.unit

DAYS = [datetime(2025, 6, 2) + timedelta(days=i) for i in range(15)]
DAY = [d.isoformat() for d in DAYS]           # filter values as the tools take them
REGION = {1: "North", 2: "South", 3: "West", 4: "North", 5: "South"}
SHOP_MEASURES = {
    "Units": "SUM(Sales[Qty])",
    "Revenue": "SUMX(Sales, Sales[Qty] * Sales[Price])",
    "UnitsToDate": "VAR d = MAX(Days[Day]) RETURN CALCULATE([Units], ALL(Days), Days[Day] <= d)",
    "PctOfSelectedDays": "DIVIDE([Revenue], CALCULATE([Revenue], ALLSELECTED(Days)))",
    "PctOfSelectedRegions": "DIVIDE([Units], CALCULATE([Units], ALLSELECTED(Store[Region])))",
    "DayInScope": "ISINSCOPE(Days[Day])",
    "StoreLevel": 'IF(ISINSCOPE(Store[StoreId]), "store", "total")',
    "StoreRank": "RANKX(ALL(Store[StoreId]), [Revenue])",
    "WholeUnits": "[Units] * 1.0",                  # declared Int64: 403.0 is reported 403
    "UnitsDouble": "[Units] * 1.0",
    "BadColumn": "MAX(Sales[Missing])",             # status error
    "Unsupported": "MADEUPFUNC(Sales[Qty])",        # status unsupported
    "Nothing": 'CALCULATE([Units], Store[Region] = "Nowhere")',   # status blank
    "Fails": '"q" * 3',
    "Bumped": "[Fails] + 2",                        # degrades Fails' error, keeps it
    "Rescued": "IFERROR([Bumped], 40)",
    "Contained": "IFERROR([Fails], 6)",             # catches Fails' error itself
    "AroundContained": "IFERROR([Contained], 66)",
    "ContainedIsError": "ISERROR([Contained])",
    "ContainedPerRegion": "SUMX(VALUES(Store[Region]), IFERROR([Contained], -1))",
    # an error only under South, reached from another region's context
    "SouthOnly": 'IF(SELECTEDVALUE(Store[Region]) = "South", ERROR("south is closed"))',
    "AskSouth": 'CALCULATE([SouthOnly], Store[Region] = "South")',
    "AskSouthSafely": 'CALCULATE(IFERROR([SouthOnly], 0), Store[Region] = "South")',
    "PerDay": "SUMX(ALL(Days[Day]), [Units])",     # one [Units] per day per store filter
}
ALL_NAMES = ",".join(SHOP_MEASURES)


def _write_shop_model(path: str) -> None:
    """Days -< Sales >- Store; the sales follow a fixed arithmetic pattern
    that reaches every store and every day."""
    b = PBIXBuilder("Shops233")
    b.add_table("Days", [{"name": "Day", "data_type": "DateTime"}], rows=[{"Day": d} for d in DAYS])
    b.add_table("Store", [{"name": "StoreId", "data_type": "Int64"},
                          {"name": "Region", "data_type": "String"}],
                rows=[{"StoreId": s, "Region": r} for s, r in REGION.items()])
    sales = [{"StoreId": 1 + (i * 7) % 5, "Day": DAYS[(i * 4) % 15], "Qty": 1 + (i * 5) % 8,
              "Price": 1.5 + (i * 3) % 11} for i in range(90)]
    b.add_table("Sales", [{"name": "StoreId", "data_type": "Int64"},
                          {"name": "Day", "data_type": "DateTime"},
                          {"name": "Qty", "data_type": "Int64"},
                          {"name": "Price", "data_type": "Double"}], rows=sales)
    b.add_relationship("Sales", "Day", "Days", "Day")
    b.add_relationship("Sales", "StoreId", "Store", "StoreId")
    for name, expr in SHOP_MEASURES.items():
        b.add_measure("Sales", name, expr, data_type="Int64" if name == "WholeUnits" else None)
    b.add_page("Page 1")
    b.save(path)


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("shops233") / "shops233.pbix")
    _write_shop_model(path)
    alias = "shops233"
    assert json.loads(server.pbix_open(path, alias))["success"]
    yield alias
    server.pbix_close(alias, force=True)


def _one_call(alias: str, context, measures: str = ALL_NAMES) -> dict:
    """pbix_evaluate_dax's response for one batch context, the arguments
    spelled as that tool takes them (JSON text; absent or null: omitted)."""
    args = {name: json.dumps(context[name])
            for name in ("filter_context", "group_by", "selected_filter_context")
            if context.get(name) is not None}
    return json.loads(server.pbix_evaluate_dax(alias, measures, apply_default_filters=False, **args))


def _batch_call(alias: str, contexts, measures: str = ALL_NAMES, **kw) -> dict:
    return json.loads(server.pbix_evaluate_dax_batch(alias, measures, json.dumps(contexts), **kw))


def _visual_rows() -> list:
    """The contexts a client sends for a few visuals -- rows grouped by day
    (with and without a slicer on the day axis), rows grouped by store,
    plain, predicate and top_n filters, no filter at all -- and then some of
    them again, later and in another order."""
    rows: list = []
    for day in (DAY[0], DAY[5], DAY[9], DAY[14]):
        for regions in ({}, {"Store.Region": ["North"]}):
            row = {"filter_context": {**regions, "Days.Day": [day]}, "group_by": ["Days.Day"]}
            rows.append(row)                                           # selection: all but the axis
            rows.append({**row, "selected_filter_context": regions})   # the same, spelled out
            rows.append({**row, "selected_filter_context":              # a slicer on the axis
                         {**regions, "Days.Day": [day, DAY[2], DAY[11]]}})
    for store in REGION:
        rows.append({"filter_context": {"Store.StoreId": [store]}, "group_by": ["Store.StoreId"]})
    rows += [
        {"filter_context": {"Store.Region": ["South"], "Store.StoreId": [2]}, "group_by": ["Store.StoreId"]},
        {"filter_context": {"Days.Day": [DAY[3]]}},
        {"filter_context": {"Store.Region": ["West"]}},
        {"filter_context": {"Store.StoreId": {"op": ">=", "value": 3}}},
        {"filter_context": {"Days.Day": {"between": [DAY[4], DAY[8]]}}},
        {"filter_context": {"Store.StoreId": {"top_n": {"n": 3, "by": "Revenue"}}}},
        {"filter_context": {"Store.Region": ["North"],
                            "Store.StoreId": {"top_n": {"n": 1, "by": "Units", "direction": "asc"}}}},
        {}, {"filter_context": None}, {"filter_context": {}},
        {"filter_context": None, "group_by": None, "selected_filter_context": None},
    ]
    return rows + rows[::3] + rows[10::-1]


class TestEachElementIsTheSingleCallsAnswer:
    def test_every_visual_row(self, shop):
        rows = _visual_rows()
        expected = [_one_call(shop, row) for row in rows]
        answer = _batch_call(shop, rows)
        assert answer["success"] is True
        got = answer["results"]
        assert len(got) == len(rows)
        differing = [(i, rows[i]) for i, (g, w) in enumerate(zip(got, expected)) if g != w]
        assert differing == []
        seen = Counter(r["status"] for response in expected for r in response["results"])
        assert set(seen) == {"ok", "blank", "error", "unsupported"}

    def test_a_repeated_context_still_reports_the_uncaught_nested_error(self, shop):
        context = {"filter_context": {"Store.Region": ["North"]}}
        names = "AskSouth,SouthOnly,Bumped,Rescued"
        single = _one_call(shop, context, names)
        by_name = {r["name"]: r for r in single["results"]}
        assert by_name["SouthOnly"]["status"] == "error"
        assert "south is closed" in by_name["SouthOnly"]["error_message"]
        assert by_name["Rescued"]["value"] == 40
        assert _batch_call(shop, [context] * 3, names)["results"] == [single] * 3

    def test_int64_unsupported_blank_and_error(self, shop):
        context = {"filter_context": {"Store.Region": ["South"]}}
        names = "WholeUnits,UnitsDouble,Unsupported,Nothing,BadColumn"
        element = _batch_call(shop, [context], names)["results"][0]
        assert element == _one_call(shop, context, names)
        r = {x["name"]: x for x in element["results"]}
        assert r["WholeUnits"]["data_type"] == "Int64" and type(r["WholeUnits"]["value"]) is int
        assert r["UnitsDouble"]["data_type"] == "Double"
        assert r["WholeUnits"]["value"] == r["UnitsDouble"]["value"]
        assert r["Unsupported"]["status"] == "unsupported"
        assert r["Unsupported"]["unsupported_functions"] == ["MADEUPFUNC"]
        assert element["warnings"] == ["1 unsupported DAX function(s): MADEUPFUNC"]
        assert r["Nothing"]["status"] == "blank"
        assert r["BadColumn"]["status"] == "error"

    def test_allselected_keeps_a_slicer_on_the_axis_apart(self, shop):
        names = "PctOfSelectedDays,DayInScope"
        axis_only = {"filter_context": {"Days.Day": [DAY[6]]}, "group_by": ["Days.Day"]}
        slicer = {**axis_only, "selected_filter_context": {"Days.Day": [DAY[6], DAY[7], DAY[12]]}}
        want = [_one_call(shop, axis_only, names), _one_call(shop, slicer, names)]
        assert _batch_call(shop, [slicer, axis_only, slicer, axis_only], names)["results"] == want[::-1] * 2
        # the selections differ, so one memo must keep their answers apart
        assert want[0]["results"][0]["value"] != want[1]["results"][0]["value"]
        assert want[0]["results"][1]["value"] is True


class TestCaughtErrorsAreConsumed:
    """An error an IFERROR / ISERROR inside a measure caught is gone: the
    measure is its value, whether evaluated afresh or served from the memo,
    in a single call or a batch."""

    NAMES = ("Contained", "AroundContained", "ContainedIsError", "ContainedPerRegion")
    WANT = {"Contained": 6, "AroundContained": 6, "ContainedIsError": False}

    @pytest.mark.parametrize("order", [
        ("AroundContained",), ("ContainedIsError",), ("ContainedPerRegion",),
        NAMES, NAMES[::-1], ("Contained", "ContainedPerRegion", "AroundContained"),
    ])
    @pytest.mark.parametrize("regions, n_regions", [(None, 3), (["North"], 1), (["South", "West"], 2)])
    def test_in_any_order(self, shop, order, regions, n_regions):
        context = {"filter_context": {"Store.Region": regions} if regions else None}
        single = _one_call(shop, context, ",".join(order))
        want = dict(self.WANT, ContainedPerRegion=6 * n_regions)
        assert {r["name"]: r["value"] for r in single["results"]} == {n: want[n] for n in order}
        assert all(r["status"] == "ok" for r in single["results"])
        assert _batch_call(shop, [context] * 3, ",".join(order))["results"] == [single] * 3

    def test_a_caught_error_is_not_reported_for_the_measure(self, shop):
        # SouthOnly is BLANK under North; its error under South was caught
        context = {"filter_context": {"Store.Region": ["North"]}}
        single = _one_call(shop, context, "AskSouthSafely,SouthOnly")
        assert [(r["name"], r.get("value"), r["status"]) for r in single["results"]] == [
            ("AskSouthSafely", 0, "ok"), ("SouthOnly", None, "blank")]
        assert _batch_call(shop, [context] * 2, "AskSouthSafely,SouthOnly")["results"] == [single] * 2


class TestRefusals:
    REFUSED = [
        {"filter_context": {"Store.Region": ["West"]}, "group_by": ["Days.Day"]},   # key not filtered
        {"filter_context": {"Days.Day": [DAY[1]]}, "group_by": "Days.Day"},         # not an array
        {"filter_context": {"Days.Day": [DAY[1]]}, "group_by": [7]},                # not strings
        {"filter_context": {"Days.Day": [DAY[1]]}, "group_by": ["Days.Day"],
         "selected_filter_context": ["Days.Day"]},                                  # not an object
        {"filter_context": {"Store.StoreId": {"top_n": {"n": 0, "by": "Units"}}}},
        {"filter_context": {"Store.StoreId": {"top_n": {"n": 2, "by": "NoSuchThing"}}}},
        {"filter_context": {"Nowhere.Id": {"top_n": {"n": 2, "by": "Units"}}}},
    ]

    def test_a_refused_context_answers_in_its_own_element(self, shop):
        fine = {"filter_context": {"Store.Region": ["South"]}}
        contexts = [fine]
        for refused in self.REFUSED:
            contexts += [refused, fine]
        answer = _batch_call(shop, contexts, "Units,StoreRank")
        assert answer["success"] is True and len(answer["results"]) == len(contexts)
        for context, element in zip(contexts, answer["results"]):
            assert element == _one_call(shop, context, "Units,StoreRank"), context
            assert element["success"] is (context is fine), context

    def test_an_unreadable_element_answers_invalid_input(self, shop):
        fine = {"filter_context": {"Store.Region": ["West"]}}
        unreadable = [0, "row", None, [fine], {"filters": {"Store.Region": ["West"]}},
                      {"filter_context": [["Store.Region", "West"]]},
                      {"filter_context": '{"Store.Region": ["West"]}'}, {"filter_context": 2.5}]
        answer = _batch_call(shop, [fine, *unreadable, fine], "Units")
        assert answer["success"] is True and len(answer["results"]) == len(unreadable) + 2
        want = _one_call(shop, fine, "Units")
        assert answer["results"][0] == want == answer["results"][-1]
        middle = answer["results"][1:-1]
        assert all(e["success"] is False and e["error_code"] == "INVALID_INPUT" for e in middle)
        assert middle[0]["message"].endswith("this one is a number")
        assert middle[2]["message"].endswith("this one is null")
        assert "unknown context key(s) ['filters']" in middle[4]["message"]
        assert middle[5]["message"].startswith("filter_context is an object of filters")
        assert middle[6]["message"].endswith("this one is a string")

    @pytest.mark.parametrize("contexts", ["", "null", "12", "not json", '"[]"', '{"filter_context": {}}'])
    def test_contexts_must_be_a_json_array(self, shop, contexts):
        answer = json.loads(server.pbix_evaluate_dax_batch(shop, "Units", contexts))
        assert answer["success"] is False and answer["error_code"] == "INVALID_INPUT"
        assert answer["message"].startswith("contexts is not a JSON array")

    def test_max_contexts(self, shop):
        contexts = [{"filter_context": {"Store.StoreId": [s]}} for s in (2, 4, 5, 1)]
        answer = _batch_call(shop, contexts, "Units", max_contexts=3)
        assert answer["success"] is False and answer["error_code"] == "INVALID_INPUT"
        assert answer["message"].startswith("4 filter contexts exceed max_contexts=3")
        answer = _batch_call(shop, contexts, "Units", max_contexts=4)
        assert answer["success"] is True and len(answer["results"]) == 4
        for limit in (0, -5):
            answer = _batch_call(shop, [], "Units", max_contexts=limit)
            assert answer["success"] is False and answer["error_code"] == "INVALID_INPUT"
            assert answer["message"].endswith(f"at least 1; got {limit}")
        assert _batch_call(shop, [], "Units") == {
            "success": True, "message": "", "warnings": [], "results": []}

    def test_a_failing_shared_argument_fails_the_call_as_pbix_evaluate_dax(self, shop):
        contexts = [{"filter_context": {"Store.StoreId": [3]}}]
        answer = _batch_call(shop, contexts, "Units,NotAMeasure")
        assert answer == json.loads(server.pbix_evaluate_dax(shop, "Units,NotAMeasure",
                                                             apply_default_filters=False))
        assert answer["success"] is False and answer["error_code"] == "DAX_MEASURE_NOT_FOUND"
        answer = _batch_call("no_such_alias_233", contexts, "Units")
        assert answer == json.loads(server.pbix_evaluate_dax("no_such_alias_233", "Units"))
        assert answer["success"] is False


class TestOneMemoForAllContexts:
    def test_what_no_context_changes_is_evaluated_once(self, shop, monkeypatch):
        evaluated = Counter()
        real = dax._engine._eval_expr
        revenue = SHOP_MEASURES["Revenue"]

        def counting(expr, ctx, *a, **kw):
            if isinstance(expr, str) and expr.strip() == revenue:
                evaluated["Revenue"] += 1
            return real(expr, ctx, *a, **kw)

        monkeypatch.setattr(dax._engine, "_eval_expr", counting)
        per_store = [{"filter_context": {"Store.StoreId": [s]}, "group_by": ["Store.StoreId"]}
                     for s in REGION]
        singles = [_one_call(shop, row, "StoreRank") for row in per_store]
        one_by_one = evaluated.pop("Revenue")
        assert _batch_call(shop, per_store, "StoreRank")["results"] == singles
        batched = evaluated.pop("Revenue")
        # every single call ranks the five stores again; the batch ranks them once
        assert one_by_one >= 5 * 5
        assert batched <= 2 * 5

    def test_the_memo_starts_afresh_past_its_bound(self, shop, monkeypatch):
        seen: list = []
        real = dax.query_memo_entries

        def watching(cache):
            seen.append(real(cache))
            return seen[-1]

        monkeypatch.setattr(dax, "query_memo_entries", watching)
        # PerDay keeps 15 [Units] (one per day) and itself for each store
        stores = [{"filter_context": {"Store.StoreId": [s]}} for s in REGION] * 2
        want = [_one_call(shop, row, "PerDay") for row in stores]
        assert _batch_call(shop, stores, "PerDay")["results"] == want
        assert len(seen) == len(stores) and seen == sorted(seen)
        assert seen[4] >= 5 * 16                    # unbounded: every store's values
        seen.clear()
        monkeypatch.setattr(server, "_BATCH_MEMO_MAX_ENTRIES", 30)
        assert _batch_call(shop, stores, "PerDay")["results"] == want
        assert max(seen) <= 30 + 16                 # the bound and one context's values
        assert seen != sorted(seen)                 # it was restarted

    def test_only_memo_entries_are_counted(self):
        tables: dict = {}
        cache = {("model",): (tables, {"a": 1, "b": 2}, {}, {}),
                 ("other kind",): (tables, ("p", "q", "r"), "name"),
                 ("model 2",): (tables, {"c": 3}, {}, {})}
        assert dax.query_memo_entries(cache) == 3

    def test_the_docstring_states_the_bound(self):
        assert f"{server._BATCH_MEMO_MAX_ENTRIES:,}" in server.pbix_evaluate_dax_batch.__doc__

    def test_calculation_item_format_strings(self, tmp_path):
        b = PBIXBuilder("CG233")
        b.add_table("T", [{"name": "c", "data_type": "String"}, {"name": "v", "data_type": "Double"}],
                    rows=[{"c": "a", "v": 10.0}, {"c": "b", "v": 20.0}, {"c": "c", "v": 30.0}])
        b.add_measure("T", "S", "SUM(T[v])")
        b.add_page("Page 1")
        path = str(tmp_path / "cg233.pbix")
        b.save(path)
        a = "i233_cg"
        assert json.loads(server.pbix_open(path, a))["success"]
        try:
            r = json.loads(server.pbix_datamodel_add_calculation_group(a, "CG", json.dumps(
                [{"name": "Base", "expression": "SELECTEDMEASURE()"},
                 {"name": "Double", "expression": "SELECTEDMEASURE() * 2"}])))
            assert r["success"], r
            # the add tool writes no format-string expression; give "Double"
            # one, in the shape ModelReader.calculation_groups reads (#121)
            ctx = server._get_dax_context(a)
            groups = [dict(g, items=[dict(i, format_string='"x" & FORMAT(SELECTEDMEASURE(), "0.0")')
                                     if i["name"] == "Double" else i for i in g["items"]])
                      for g in ctx["calculation_groups"]]
            ctx["calculation_groups"] = groups
            dax.set_calculation_groups(ctx["measure_defs"], groups)
            contexts = [{"filter_context": {"CG.Name": ["Double"]}}, {},
                        {"filter_context": {"CG.Name": ["Double"], "T.c": ["a"]}},
                        {"filter_context": {"CG.Name": ["Base"]}}, {"filter_context": {"CG.Name": ["Double"]}}]
            got = _batch_call(a, contexts, "S")
            assert got["results"] == [_one_call(a, c, "S") for c in contexts]
            assert got["results"][0]["results"][0] == {
                "name": "S", "value": 120.0, "status": "ok", "data_type": "Double",
                "format_string": "x120.0"}
            assert "format_string" not in got["results"][1]["results"][0]
        finally:
            server.pbix_close(a, force=True)


def _slicer_selecting(entity: str, prop: str, value: str) -> dict:
    """A slicer visual container whose persisted selection is ``entity.prop = value``."""
    where = [{"Condition": {"In": {
        "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": prop}}],
        "Values": [[{"Literal": {"Value": f"'{value}'"}}]]}}}]
    config = {"singleVisual": {"visualType": "slicer", "objects": {"general": [{"properties": {
        "filter": {"filter": {"From": [{"Name": "s", "Entity": entity}], "Where": where}}}}]}}}
    return {"config": json.dumps(config)}


def test_default_slicer_selections_are_never_applied(tmp_path):
    path = str(tmp_path / "shops233d.pbix")
    _write_shop_model(path)
    a = "shops233_defaults"
    assert json.loads(server.pbix_open(path, a))["success"]
    try:
        info = server._ensure_open(a)
        layout = server._get_layout(info["work_dir"])
        layout["sections"][0]["visualContainers"] = [_slicer_selecting("Store", "Region", "North")]
        server._set_layout(info["work_dir"], layout)
        defaulted = json.loads(server.pbix_evaluate_dax(a, "Units"))
        whole = json.loads(server.pbix_evaluate_dax(a, "Units", apply_default_filters=False))
        assert defaulted["results"][0]["value"] != whole["results"][0]["value"]
        got = _batch_call(a, [{}, {"filter_context": {}}, {"filter_context": None}], "Units")
        assert got["results"] == [whole] * 3
    finally:
        server.pbix_close(a, force=True)


def test_the_tool_is_registered():
    tm = getattr(server.mcp, "_tool_manager", None)
    assert "pbix_evaluate_dax_batch" in set(getattr(tm, "_tools", {}))


class TestEngineMemoAgreesWithFreshEvaluation:
    """The engine half, on plain tables: an answer is the same whatever was
    memoized first and whether or not a memo is shared across contexts."""

    TABLES = {"T": {"columns": ["k", "s", "v"], "rows": [[1, "x", 10], [2, "y", 20], [3, "z", 30]]}}
    MS = {
        "Bad": 'ERROR("boom")',
        "Plus": "[Bad] + 1",                 # keeps Bad's error
        "Twice": "[Plus] * 2",
        "Guard": "IFERROR([Plus], 13)",
        "GuardTwice": "IFERROR([Twice], 7)",
        "Failed": "IF(ISERROR([Plus]), 1, 0)",
        "Safe": "IFERROR([Bad], 5)",         # catches Bad's error
        "OverSafe": "IFERROR([Safe], 99)",
        "SafeFailed": "IF(ISERROR([Safe]), 1, 0)",
        "RowsSafe": "SUMX(T, IFERROR([Bad], 1))",
        "OverRowsSafe": "IFERROR([RowsSafe], 97)",
        "BadThenSafe": "[Bad] + IFERROR([Bad], 5)",    # an uncaught error, then a caught one
        "SafeThenBad": "IFERROR([Bad], 5) + [Bad]",    # the other way round
        "OverBadThenSafe": "IFERROR([BadThenSafe], 50)",
        "OverSafeThenBad": "IFERROR([SafeThenBad], 51)",
        "N": 'IF(SELECTEDVALUE(T[s]) = "x", ERROR("bad x"), BLANK())',
        "B": 'CALCULATE([N], T[s] = "x")',
    }
    BOOM = "[DAX_EVAL_FAILED] DAX ERROR(): boom"

    def _run(self, names, fc=None, cache=None):
        e = dax._engine
        e.unsupported_functions.clear()
        e.unsupported_by_measure.clear()
        e.eval_errors.clear()
        e.timed_out.clear()
        r = dax.evaluate_measures_smart(list(names), self.TABLES, self.MS, fc or {},
                                        simulate_row_context=False, cache=cache)
        return r, dict(e.eval_errors)

    @pytest.mark.parametrize("names, measure, value", [
        (["Guard"], "Guard", 13), (["Plus", "Guard"], "Guard", 13),
        (["GuardTwice"], "GuardTwice", 7), (["Plus", "GuardTwice"], "GuardTwice", 7),
        (["Twice", "GuardTwice"], "GuardTwice", 7), (["Plus", "Twice", "GuardTwice"], "GuardTwice", 7),
        (["Failed"], "Failed", 1), (["Plus", "Failed"], "Failed", 1),
        (["OverSafe"], "OverSafe", 5), (["Safe", "OverSafe"], "OverSafe", 5),
        (["SafeFailed"], "SafeFailed", 0), (["Safe", "SafeFailed"], "SafeFailed", 0),
        (["OverRowsSafe"], "OverRowsSafe", 3), (["RowsSafe", "OverRowsSafe"], "OverRowsSafe", 3),
        (["OverBadThenSafe"], "OverBadThenSafe", 50),
        (["BadThenSafe", "OverBadThenSafe"], "OverBadThenSafe", 50),
        (["OverSafeThenBad"], "OverSafeThenBad", 51),
        (["SafeThenBad", "OverSafeThenBad"], "OverSafeThenBad", 51),
    ])
    def test_the_answer_does_not_depend_on_what_is_memoized(self, names, measure, value):
        r, errors = self._run(names)
        assert r[measure] == value
        # eval_errors keeps what a requested measure let through, not what
        # an IFERROR / ISERROR caught: Bad's error and every measure it
        # failed on the way -- a referenced measure's error is its caller's
        # (#266; Desktop 2.152, build_b134.py measure_used)
        fails = {"Plus": {"Bad", "Plus"}, "Twice": {"Bad", "Plus", "Twice"},
                 "BadThenSafe": {"Bad", "BadThenSafe"}, "SafeThenBad": {"Bad", "SafeThenBad"}}
        want = set().union(*(fails.get(n, set()) for n in names))
        assert errors == dict.fromkeys(want, self.BOOM)

    def test_a_shared_memo_keeps_the_values_and_the_errors(self):
        cache: dict = {}
        first = self._run(["Plus", "Twice", "Safe", "BadThenSafe"], cache=cache)
        for names in (["Guard"], ["GuardTwice"], ["Failed"], ["OverSafe"], ["SafeFailed"],
                      ["OverBadThenSafe"], ["Plus", "Twice"], ["Safe"]):
            assert self._run(names, cache=cache) == self._run(names), names
        assert self._run(["Plus", "Twice", "Safe", "BadThenSafe"], cache=cache) == first

    def test_a_shared_memo_reports_the_nested_error_of_a_repeated_context(self):
        cache: dict = {}
        # B fails with the N it references (#266)
        want = ({"B": None, "N": None}, {"N": "[DAX_EVAL_FAILED] DAX ERROR(): bad x",
                                         "B": "[DAX_EVAL_FAILED] DAX ERROR(): bad x"})
        assert self._run(["B", "N"]) == want
        assert self._run(["B", "N"], cache=cache) == want
        assert self._run(["B", "N"], cache=cache) == want
        assert self._run(["B", "N"], {"T.k": [2]}, cache=cache) == ({"B": None, "N": None}, {})
