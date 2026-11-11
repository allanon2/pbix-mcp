"""pbix_execute_query: a DAX query -- [DEFINE MEASURE ...] EVALUATE <table>
[ORDER BY ...] -- answered as tables of named, typed columns and positional
rows (pbix_mcp.dax.query).

The query shapes are the DAX query view's own quick queries (Show top 100
rows, Show data preview, Column statistics, Evaluate, Define and evaluate,
Define new measure), written as that view writes them. The values asserted
are computed by hand from the rows below; result column NAMES follow the
executeQueries REST API's documented form (Table[Column] for a model
column, [Name] for any other) and are not yet measured on Desktop.

Unknown is never silent: every refusal below is an error naming its cause,
never an empty or zero result.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from pbix_mcp import server
from pbix_mcp.dax import engine as de
from pbix_mcp.dax.query import execute_query, parse_query
from pbix_mcp.errors import DAXEvaluationError, DAXParseError, DAXUnsupportedError

pytestmark = pytest.mark.unit

TABLES = {
    "Item": {"columns": ["Name", "Qty", "When"],
             "rows": [["b", 1, dt.datetime(2024, 1, 2)],
                      ["a", None, dt.datetime(2024, 3, 1)],
                      ["c", 0, None],
                      ["d", 2, dt.datetime(2024, 1, 2)]]},
}
MEASURES = {"Rows": "COUNTROWS('Item')", "Total": "SUM('Item'[Qty])"}
HOMES = {"Rows": "Item", "Total": "Item"}


def run(text: str, measures: dict | None = None, **kw) -> list:
    m = dict(MEASURES) if measures is None else measures
    return execute_query(text, TABLES, m, measure_tables=dict(HOMES), **kw)


def names(table: dict) -> list:
    return [c["name"] for c in table["columns"]]


# ---------------------------------------------------------------------------
# The quick queries
# ---------------------------------------------------------------------------

TOP_ROWS = """EVALUATE
    SELECTCOLUMNS(
        TOPN(
            100,
            'Item',
            'Item'[Name],
            ASC
        ),
        'Item'[Name],
        'Item'[Qty],
        'Item'[When]
    )
    ORDER BY 'Item'[Name] ASC"""


def test_show_top_rows():
    """SELECTCOLUMNS' unnamed column references keep their names and
    lineage; ORDER BY sorts. The engine answered rows of one column named
    "('Item', 'Name')" holding Qty."""
    (t,) = run(TOP_ROWS)
    assert names(t) == ["Item[Name]", "Item[Qty]", "Item[When]"]
    assert t["rows"] == [["a", None, dt.datetime(2024, 3, 1)],
                         ["b", 1, dt.datetime(2024, 1, 2)],
                         ["c", 0, None],
                         ["d", 2, dt.datetime(2024, 1, 2)]]
    assert [c["type"] for c in t["columns"]] == ["Text", "Integer", "DateTime"]


def test_show_data_preview():
    (t,) = run("EVALUATE\n    DISTINCT('Item'[Qty])")
    assert names(t) == ["Item[Qty]"]
    assert sorted(t["rows"], key=lambda r: (r[0] is not None, r[0] or 0)) == [[None], [0], [1], [2]]


def test_column_statistics_row():
    q = """EVALUATE
    ROW (
        "Table", "Item",
        "Column", "Qty",
        "Count", COUNT ( 'Item'[Qty] ),
        "Distinct Values", DISTINCTCOUNTNOBLANK ( 'Item'[Qty] ),
        "Null Count", COUNTROWS ( 'Item' ) - COUNT ( 'Item'[Qty] ),
        "Min", MIN ( 'Item'[Qty] ),
        "Max", MAX ( 'Item'[Qty] ),
        "Zeros", COALESCE ( COUNTROWS ( FILTER ( 'Item', 'Item'[Qty] = 0 ) ), 0 )
    )"""
    (t,) = run(q)
    assert names(t) == ["[Table]", "[Column]", "[Count]", "[Distinct Values]", "[Null Count]",
                        "[Min]", "[Max]", "[Zeros]"]
    # Zeros counts the BLANK row too: in DAX, BLANK = 0 is TRUE
    assert t["rows"] == [["Item", "Qty", 3, 3, 1, 0, 2, 2]]


def test_evaluate_measure_no_group():
    """SUMMARIZECOLUMNS with no column to group by is one row; the engine
    answered no row. ''[M] names the measure with an empty table."""
    (t,) = run('EVALUATE\n    SUMMARIZECOLUMNS(\n        "Rows", \'\'[Rows]\n    )')
    assert names(t) == ["[Rows]"]
    assert t["rows"] == [[4]]


def test_summarizecolumns_no_group_all_blank_is_no_row():
    (t,) = run('EVALUATE SUMMARIZECOLUMNS("x", CALCULATE([Total], \'Item\'[Name] = "a"))')
    assert t["rows"] == []


def test_summarizecolumns_no_group_filter_table():
    (t,) = run('EVALUATE SUMMARIZECOLUMNS(TREATAS({"b", "c"}, \'Item\'[Name]), "t", [Total])')
    assert t["rows"] == [[1]]          # b and c; unfiltered it is 3


def test_define_and_evaluate():
    """A DEFINE MEASURE replaces the model's measure of its name for the
    query only, and may reference another query measure."""
    q = """DEFINE
    /// a description line
    MEASURE 'Item'[Rows] = COUNTROWS('Item') * 10
    MEASURE 'Item'[New] = VAR a = [Rows] VAR b = 2 RETURN a + b

EVALUATE
    SUMMARIZECOLUMNS(
        "Rows", ''[Rows],
        "New", ''[New]
    )"""
    measures = dict(MEASURES)
    (t,) = run(q, measures)
    assert t["rows"] == [[40, 42]]
    assert measures == MEASURES                        # the model's are untouched
    (again,) = run('EVALUATE ROW("r", [Rows])', measures)
    assert again["rows"] == [[4]]


def test_define_measure_name_case_insensitive():
    (t,) = run("DEFINE MEASURE Item[rows] = 7 EVALUATE ROW(\"r\", [ROWS])")
    assert t["rows"] == [[7]]


def test_define_new_measure_template():
    q = ("// Write your formula below, select 'Run' to try it out.\nDEFINE\n"
         "MEASURE 'Item'[Measure] = COUNTROWS('Item')\n\nEVALUATE\nSUMMARIZECOLUMNS(\n"
         "\"Measure\", ''[Measure]\n)")
    (t,) = run(q)
    assert t["rows"] == [[4]]


def test_several_evaluates():
    a, b = run('EVALUATE {1, 2}\nEVALUATE ROW("a", "x") -- two')
    assert names(a) == ["[Value]"] and a["rows"] == [[1], [2]]
    assert names(b) == ["[a]"] and b["rows"] == [["x"]]


def test_order_by_several_items_and_extension():
    (t,) = run("EVALUATE ADDCOLUMNS(VALUES('Item'[Name]), \"k\", IF('Item'[Name] IN {\"a\", \"c\"}, 1, 2))"
               " ORDER BY [k] DESC, 'Item'[Name] ASC")
    assert names(t) == ["Item[Name]", "[k]"]
    assert t["rows"] == [["b", 2], ["d", 2], ["a", 1], ["c", 1]]


def test_order_by_blank_first_ascending():
    (t,) = run("EVALUATE 'Item' ORDER BY 'Item'[Qty]")
    assert [r[1] for r in t["rows"]] == [None, 0, 1, 2]
    (t,) = run("EVALUATE 'Item' ORDER BY 'Item'[Qty] DESC")
    assert [r[1] for r in t["rows"]] == [2, 1, 0, None]


def test_order_by_expression():
    (t,) = run("EVALUATE VALUES('Item'[Name]) ORDER BY IF('Item'[Name] = \"c\", 0, 1), 'Item'[Name] DESC")
    assert [r[0] for r in t["rows"]] == ["c", "d", "b", "a"]


def test_var_in_evaluate():
    (t,) = run("EVALUATE VAR x = 2 RETURN FILTER('Item', 'Item'[Qty] = x)")
    assert [r[0] for r in t["rows"]] == ["d"]


def test_model_types_win_over_values():
    (t,) = run("EVALUATE TOPN(1, 'Item', 'Item'[Name], ASC)",
               column_types={("Item", "Qty"): "Double", ("Item", "Name"): "String"})
    assert [(c["type"], c["type_source"]) for c in t["columns"]] == [
        ("Text", "model"), ("Double", "model"), ("DateTime", "values")]


def test_empty_result_says_its_columns_are_unknown():
    (t,) = run("EVALUATE FILTER('Item', FALSE())")
    assert t == {"columns": [], "rows": [], "columns_known": False}


def test_selectcolumns_named_and_unnamed_mixed():
    (t,) = run("EVALUATE SELECTCOLUMNS('Item', 'Item'[Name], \"twice\", 'Item'[Qty] * 2)"
               " ORDER BY 'Item'[Name]")
    assert names(t) == ["Item[Name]", "[twice]"]
    assert t["rows"] == [["a", None], ["b", 2], ["c", 0], ["d", 4]]


def test_calculation_groups_reach_the_query_measures():
    measures = dict(MEASURES)
    de.set_calculation_groups(measures, [{
        "table": "CG", "column": "Item", "precedence": 1,
        "items": [{"name": "Double", "expression": "SELECTEDMEASURE() * 2", "ordinal": 0}]}])
    try:
        tables = dict(TABLES)
        tables["CG"] = {"columns": ["Item"], "rows": [["Double"]]}
        (t,) = execute_query('EVALUATE CALCULATETABLE(ROW("t", [Total]), \'CG\'[Item] = "Double")',
                             tables, measures, measure_tables=dict(HOMES))
        assert t["rows"] == [[6]]
    finally:
        de.set_calculation_groups(measures, None)


# ---------------------------------------------------------------------------
# Refusals: errors, never empty
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,needle", [
    ("EVALUATE NOSUCHTABLE", "gave no table"),
    ("EVALUATE 1 + 1", "not a table expression"),
    ('EVALUATE SUMMARIZECOLUMNS("x", [nosuch])', "nosuch"),
    ("EVALUATE 'Item'[Nope]", "Nope"),
    ('EVALUATE ROW("a", \'Nope\'[x])', "no value"),
    ("EVALUATE 'Item' ORDER BY 'Item'[Nope]", "names no column"),
    ("EVALUATE SELECTCOLUMNS('Item', [Total])", "has no name"),
    ("EVALUATE SELECTCOLUMNS('Item', \"Name\", 1, 'Item'[Name])", "more than once"),
    ("DEFINE MEASURE 'Nope'[m] = 1 EVALUATE ROW(\"a\", 1)", "no table 'Nope'"),
    ("DEFINE MEASURE 'Item'[m] = 1 MEASURE 'Item'[M] = 2 EVALUATE ROW(\"a\", 1)", "more than once"),
])
def test_errors(text, needle):
    with pytest.raises(DAXEvaluationError) as e:
        run(text)
    assert needle in str(e.value)


@pytest.mark.parametrize("text,construct", [
    ("DEFINE VAR x = 1 EVALUATE ROW(\"a\", x)", "DEFINE VAR"),
    ("DEFINE TABLE t = 'Item' EVALUATE t", "DEFINE TABLE"),
    ("DEFINE COLUMN 'Item'[c] = 1 EVALUATE 'Item'", "DEFINE COLUMN"),
    ("DEFINE FUNCTION f = () => 1 EVALUATE { f() }", "DEFINE FUNCTION"),
    ("EVALUATE 'Item' ORDER BY 'Item'[Qty] START AT 1", "START AT"),
    ('EVALUATE ROW("x", NOSUCHFUNCTION(1))', "NOSUCHFUNCTION"),
])
def test_unsupported_is_named(text, construct):
    with pytest.raises(DAXUnsupportedError) as e:
        run(text)
    assert construct in str(e.value)


def test_unsupported_inside_a_measure_fails_the_query():
    with pytest.raises(DAXUnsupportedError) as e:
        run("DEFINE MEASURE 'Item'[u] = NOSUCHFUNCTION(1) + 1 EVALUATE ROW(\"u\", [u])")
    assert "NOSUCHFUNCTION" in str(e.value)


def test_one_failing_evaluate_fails_the_query():
    with pytest.raises(DAXEvaluationError):
        run("EVALUATE {1} EVALUATE NOSUCHTABLE")


# ---------------------------------------------------------------------------
# The statement scanner
# ---------------------------------------------------------------------------

def test_parse_keeps_expressions_verbatim():
    q = parse_query("DEFINE\n MEASURE 'My ''T'''[Odd ]]Name] = \"EVALUATE\" & [x] // EVALUATE\n"
                    "/* EVALUATE */ EVALUATE ROW(\"a\", 1) ORDER BY [a] DESC, 2 START AT 1, \"x\"")
    (d,) = q.definitions
    assert (d.kind, d.table, d.name) == ("MEASURE", "My 'T'", "Odd ]Name")
    assert d.expression == '"EVALUATE" & [x]'          # ends at its last token
    (ev,) = q.evaluates
    assert ev.expression == 'ROW("a", 1)'
    assert [(o.expression, o.descending) for o in ev.order_by] == [("[a]", True), ("2", False)]
    assert ev.start_at == ["1", '"x"']


def test_parse_var_belongs_to_its_expression():
    q = parse_query("DEFINE MEASURE T[m] = VAR a = 1 VAR b = 2 RETURN VAR c = 3 RETURN a + b + c "
                    "VAR q = 4 EVALUATE VAR z = 1 RETURN {z}")
    m, v = q.definitions
    assert m.expression == "VAR a = 1 VAR b = 2 RETURN VAR c = 3 RETURN a + b + c"
    assert (v.kind, v.name, v.expression) == ("VAR", "q", "4")
    assert q.evaluates[0].expression == "VAR z = 1 RETURN {z}"


def test_parse_lowercase_keywords():
    q = parse_query("define measure T[m] = 1 evaluate {1} order by [Value] desc")
    assert q.definitions[0].name == "m"
    assert q.evaluates[0].order_by[0].descending is True


@pytest.mark.parametrize("text", [
    "", "   // nothing", "DEFINE EVALUATE {1}", "EVALUATE", "{1}", "EVALUATE {1} junk(",
    "EVALUATE ROW(\"a\", 1", "EVALUATE \"open", "EVALUATE {1} /* open",
    "EVALUATE {1} DEFINE MEASURE T[m] = 1", "DEFINE MEASURE m = 1 EVALUATE {1}",
    "DEFINE MEASURE T[m] 1 EVALUATE {1}", "EVALUATE {1} ORDER BY", "EVALUATE {1} ORDER BY [a],",
    "DEFINE MEASURE T[m] = 1",
])
def test_parse_errors(text):
    with pytest.raises(DAXParseError):
        run(text)


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

@pytest.fixture()
def model(tmp_path):
    alias = "eq_" + uuid.uuid4().hex[:8]
    tables = [{"name": "Item",
               "columns": [{"name": "Name", "data_type": "String"},
                           {"name": "Qty", "data_type": "Double"}],
               "rows": [{"Name": "b", "Qty": 1.0}, {"Name": "a", "Qty": 2.5}]}]
    measures = [{"table": "Item", "name": "Total", "expression": "SUM(Item[Qty])"}]
    r = json.loads(server.pbix_create(str(tmp_path / "q.pbix"), alias, json.dumps(tables),
                                      json.dumps(measures)))
    assert r.get("success"), r
    yield alias
    server._open_files.pop(alias, None)
    server._dax_cache.pop(alias, None)


def test_tool_answers_tables(model):
    out = json.loads(server.pbix_execute_query(
        model, "DEFINE MEASURE Item[Twice] = [Total] * 2\n"
               "EVALUATE SELECTCOLUMNS('Item', 'Item'[Name], 'Item'[Qty]) ORDER BY 'Item'[Name]\n"
               "EVALUATE SUMMARIZECOLUMNS(\"Twice\", ''[Twice])"))
    assert out["success"], out
    a, b = out["data"]["tables"]
    assert a["columns"] == [{"name": "Item[Name]", "type": "Text", "type_source": "model"},
                            {"name": "Item[Qty]", "type": "Double", "type_source": "model"}]
    assert a["rows"] == [["a", 2.5], ["b", 1.0]]
    assert b["rows"] == [[7.0]]
    # the query's measure never reaches the model
    again = json.loads(server.pbix_execute_query(model, "EVALUATE ROW(\"t\", [Twice])"))
    assert not again["success"] and again["error_code"] == "DAX_EVAL_FAILED", again


def test_tool_refusals_are_errors(model):
    out = json.loads(server.pbix_execute_query(model, "DEFINE TABLE t = 'Item' EVALUATE t"))
    assert not out["success"] and out["error_code"] == "DAX_UNSUPPORTED_FUNCTION", out
    out = json.loads(server.pbix_execute_query(model, "EVALUATE"))
    assert not out["success"] and out["error_code"] == "DAX_PARSE_FAILED", out


def test_tool_serializes_dates_and_specials(model):
    out = json.loads(server.pbix_execute_query(
        model, 'EVALUATE ROW("d", DATE(2024, 2, 29), "inf", 1 / 0)'))
    assert out["success"], out
    assert out["data"]["tables"][0]["rows"] == [["2024-02-29T00:00:00", "Infinity"]]
