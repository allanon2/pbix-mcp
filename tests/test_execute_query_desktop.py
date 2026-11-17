"""pbix_execute_query against Power BI Desktop 2.157's own answers.

Every expected value below was measured on Desktop 2.157 (2026-11-12): the
same query text sent over its loopback ADOMD connection and typed into its DAX
query view, on a public sample whose 'Sales stage' table has these columns and
types (Probability Double, Sales Stage String, Sales Stage ID Int64). The
table here is a synthetic one of the same shape. Behaviour, not code: what
Desktop returned, nothing read from it.

- Syntax and names are checked before any EVALUATE runs, with Desktop's text
  and its (line, column): "Query (1, 22) The syntax for '*' is incorrect.
  (<the query>)." A name error in the 2nd of 3 EVALUATEs returns no result.
- A query that stops early is "The end of the input was reached."
- ORDER BY puts BLANK just below its column's zero, DESC its exact reverse.
- An empty result keeps its columns and their types.
- MEDIAN of Int64 values is a Variant column; BLANK() alone an Int64 one;
  {1, "a"} a String column of "1" and "a".
- DEFINE VAR, TABLE and COLUMN; a VAR and a MEASURE see each other in any
  order.
- Silent wrong answers made errors (issue #338): ROW("x", ), a stray
  operator, MEDIAN over text, ADDCOLUMNS over an existing column.
"""
from __future__ import annotations

import datetime as dt

import pytest

from pbix_mcp.dax.query import execute_query
from pbix_mcp.errors import DAXEvaluationError, DAXParseError, DAXUnsupportedError

pytestmark = pytest.mark.unit

STAGE = "Sales stage"
TABLES = {
    STAGE: {"columns": ["Probability", "Sales Stage", "Sales Stage ID"],
            "rows": [[0.1, "Lead", 1], [0.2, "Qualify", 2], [0.4, "Solution", 3],
                     [0.6, "Proposal", 4], [0.8, "Finalize", 5]]},
    "Opp": {"columns": ["Rank"], "rows": [[1], [2], [3], [4]]},
}
TYPES = {(STAGE, "Probability"): "Double", (STAGE, "Sales Stage"): "String",
         (STAGE, "Sales Stage ID"): "Int64", ("Opp", "Rank"): "Int64"}
MEASURES = {"Revenue": "SUM('Opp'[Rank])"}


def run(text: str) -> list:
    return execute_query(text, TABLES, dict(MEASURES), measure_tables={"Revenue": "Opp"},
                         column_types=TYPES)


def one(text: str) -> dict:
    (t,) = run(text)
    return t


def cols(t: dict) -> list:
    return [(c["name"], c["type"]) for c in t["columns"]]


def error(text: str, kind=DAXEvaluationError) -> str:
    with pytest.raises(kind) as e:
        run(text)
    return e.value.message


# ---------------------------------------------------------------------------
# Syntax: Desktop's text and position
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, message", [
    ('EVALUATE ROW("x", 1 +* 2)',
     "Query (1, 22) The syntax for '*' is incorrect. (EVALUATE ROW(\"x\", 1 +* 2))."),
    ('EVALUAT ROW("x", 1)',
     "Query (1, 9) The syntax for 'ROW' is incorrect. (EVALUAT ROW(\"x\", 1))."),
    ('DEFINE MEASURE [m] = 1 EVALUATE ROW("m", [m])',
     "Query (1, 20) The syntax for '=' is incorrect. "
     "(DEFINE MEASURE [m] = 1 EVALUATE ROW(\"m\", [m]))."),
    ("EVALUATE TOPN(2, 'Sales stage'", "The end of the input was reached."),
    ("DEFINE VAR v = 1", "The end of the input was reached."),
])
def test_syntax_errors_as_desktop(text, message):
    assert error(text, DAXParseError) == message


@pytest.mark.parametrize("text", [
    'EVALUATE ROW("x", 1 2)', 'EVALUATE ROW("x", * 3)', 'EVALUATE ROW("x", 1 +)',
    'EVALUATE ROW("x", 1 */ 2)', "EVALUATE {1, 2} ORDER BY [Value] +",
])
def test_a_malformed_expression_is_never_a_value(text):
    # each of these answered a value (1, BLANK ...) before the syntax check
    with pytest.raises(DAXParseError):
        run(text)


def test_syntax_position_on_a_later_line():
    msg = error('EVALUATE ROW("a", 1)\nEVALUATE ROW("b", 1 +* 2)', DAXParseError)
    assert msg.startswith("Query (2, 22) The syntax for '*' is incorrect.")


@pytest.mark.parametrize("text", [
    'EVALUATE ROW("x", 1 + + 2)', 'EVALUATE ROW("x", -(1) * -2)', 'EVALUATE ROW("x", NOT TRUE())',
    'EVALUATE ROW("x", 1 IN {1, 2})', 'EVALUATE ROW("x", 1 <= 2 && 2 <> 3)',
    'EVALUATE {(1, "a"), (2, "b")}', 'EVALUATE ROW("x", VAR a = 1 VAR b = 2 RETURN a + b)',
    "EVALUATE TOPN(1, 'Sales stage', 'Sales stage'[Probability], DESC)",
    "EVALUATE ROW(\"x\", ''[Revenue])",
])
def test_valid_syntax_passes(text):
    run(text)


# ---------------------------------------------------------------------------
# Names, resolved before anything runs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, message", [
    ("EVALUATE NoSuchTable", "Query (1, 10) Failed to resolve name 'NoSuchTable'. "
                             "It is not a valid table, variable, or function name."),
    ("EVALUATE 'No Such Table'", "Query (1, 10) Cannot find table 'No Such Table'."),
    ("EVALUATE 1+1", "Query (1, 1) The expression specified in the query is not a valid "
                     "table expression."),
    ('EVALUATE ROW("x", [NoSuchMeasure])',
     "Query (1, 19) The value for 'NoSuchMeasure' cannot be determined. Either the column "
     "doesn't exist, or there is no current row for this column."),
    ("EVALUATE ROW(\"x\", SUM('Sales stage'[NoSuchColumn]))",
     "Query (1, 23) Column 'NoSuchColumn' in table 'Sales stage' cannot be found or may "
     "not be used in this expression."),
    ('EVALUATE ROW("a", 1) EVALUATE NoSuchTable EVALUATE ROW("b", 2)',
     "Query (1, 31) Failed to resolve name 'NoSuchTable'. It is not a valid table, "
     "variable, or function name."),
])
def test_name_errors_as_desktop(text, message):
    assert error(text) == message


def test_runtime_error_is_the_engines_text():
    # Desktop: "Query (1, 19) Cannot convert ..."; the position inside an
    # expression is not known to this engine yet
    assert error('EVALUATE ROW("x", VALUE("abc"))') == \
        "Cannot convert value 'abc' of type Text to type Number."


# ---------------------------------------------------------------------------
# Silent wrong answers made errors (#338)
# ---------------------------------------------------------------------------

def test_row_missing_argument():
    assert error('EVALUATE ROW("x", )') == "Argument '2' in ROW function is required."


def test_row_names_compare_without_case():
    assert error('EVALUATE ROW("AMPM", 1, "ampm", 2)') == \
        "Function 'ROW' cannot add column [ampm], as it has the same name with an existing column."


def test_addcolumns_over_an_existing_column():
    # it replaced the model column's values with 1
    assert error("EVALUATE TOPN(1, ADDCOLUMNS('Sales stage', \"probability\", 1))") == \
        "Function 'ADDCOLUMNS' cannot add column [probability] since it already exists."


def test_median_over_text():
    assert error("EVALUATE ROW(\"x\", MEDIAN('Sales stage'[Sales Stage]))") == \
        "MEDIAN does not support expressions of type string/Boolean/date."


def test_selectcolumns_name_beside_a_column_of_that_name_is_unsupported_not_an_error():
    # Desktop answers Sales stage[Sales Stage] and [Sales Stage]; refused by
    # name here, never an error Desktop doesn't give
    with pytest.raises(DAXUnsupportedError):
        run("EVALUATE SELECTCOLUMNS('Sales stage', 'Sales stage'[Sales Stage], \"Sales Stage\", 1)")
    with pytest.raises(DAXEvaluationError):
        run('EVALUATE SELECTCOLUMNS(\'Sales stage\', "a", 1, "A", 2)')


# ---------------------------------------------------------------------------
# ORDER BY: BLANK just below the type's zero
# ---------------------------------------------------------------------------

D = dt.datetime


@pytest.mark.parametrize("values, asc", [
    ("3, BLANK(), 1, 0, -1", [-1, None, 0, 1, 3]),
    ("0, BLANK(), 3", [None, 0, 3]),
    ("2.5, BLANK(), -0.5, 0.0", [-0.5, None, 0.0, 2.5]),
    ('"b", BLANK(), "a", ""', [None, "", "a", "b"]),
    ("TRUE(), BLANK(), FALSE()", [None, False, True]),
    ("DATE(2026,1,6), BLANK(), DATE(1899,12,29), DATE(1899,12,30), DATE(1899,12,31)",
     [D(1899, 12, 29), None, D(1899, 12, 30), D(1899, 12, 31), D(2026, 1, 6)]),
])
def test_order_by_blank(values, asc):
    up = [r[0] for r in one(f"EVALUATE {{{values}}} ORDER BY [Value] ASC")["rows"]]
    down = [r[0] for r in one(f"EVALUATE {{{values}}} ORDER BY [Value] DESC")["rows"]]
    assert up == asc
    assert down == asc[::-1]


def test_no_order_by_keeps_the_constructors_order():
    assert [r[0] for r in one("EVALUATE {3, BLANK(), 1, 0, -1}")["rows"]] == [3, None, 1, 0, -1]


# ---------------------------------------------------------------------------
# Types, and an empty result's columns
# ---------------------------------------------------------------------------

def test_static_types():
    t = one("EVALUATE ROW(\"s\", SUM('Opp'[Rank]), \"avg\", AVERAGE('Opp'[Rank]), \"div\", 7/2, "
            "\"i\", 7, \"c\", CURRENCY(1.5), \"f\", 1.5, \"b\", TRUE(), \"t\", \"t\", "
            "\"dt\", DATE(2026,1,6), \"blank\", BLANK())")
    assert [c["type"] for c in t["columns"]] == [
        "Integer", "Double", "Double", "Integer", "Decimal", "Double", "Boolean", "Text",
        "DateTime", "Integer"]


@pytest.mark.parametrize("text, value", [
    ("EVALUATE ROW(\"m\", MEDIAN('Opp'[Rank]))", 2.5),
    ('EVALUATE ROW("m", MEDIANX({1, 2}, [Value]))', 1.5),
    ('EVALUATE ROW("m", MEDIANX({1, 2, 3}, [Value]))', 2),
])
def test_median_of_integers_is_a_variant(text, value):
    t = one(text)
    assert cols(t) == [("[m]", "Variant")]
    assert t["rows"] == [[value]] and type(t["rows"][0][0]) is type(value)


def test_constructor_of_number_and_text_is_text():
    t = one('EVALUATE {1, "a"}')
    assert cols(t) == [("[Value]", "Text")]
    assert t["rows"] == [["1"], ["a"]]


def test_constructor_of_int_and_double_is_double():
    t = one("EVALUATE {1, 2.5}")
    assert cols(t) == [("[Value]", "Double")]
    assert t["rows"] == [[1.0], [2.5]]


@pytest.mark.parametrize("text, columns", [
    ("EVALUATE FILTER('Sales stage', FALSE())",
     [("Sales stage[Probability]", "Double"), ("Sales stage[Sales Stage]", "Text"),
      ("Sales stage[Sales Stage ID]", "Integer")]),
    ('EVALUATE FILTER(ROW("x", 1, "t", "a"), FALSE())', [("[x]", "Integer"), ("[t]", "Text")]),
    ("EVALUATE FILTER({1, 2}, FALSE())", [("[Value]", "Integer")]),
    ("EVALUATE SUMMARIZECOLUMNS('Sales stage'[Sales Stage], \"m\", BLANK())",
     [("Sales stage[Sales Stage]", "Text"), ("[m]", "Integer")]),
    ('EVALUATE FILTER(ROW("m", MEDIANX({1, 2}, [Value])), FALSE())', [("[m]", "Variant")]),
])
def test_empty_result_keeps_its_columns(text, columns):
    t = one(text)
    assert t["rows"] == []
    assert t["columns_known"] is True
    assert cols(t) == columns


def test_empty_result_of_an_unread_shape_says_so():
    t = one("EVALUATE FILTER(CROSSJOIN('Opp', {1}), FALSE())")
    assert t["rows"] == [] and t["columns_known"] is False and t["columns"] == []


# ---------------------------------------------------------------------------
# DEFINE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, value", [
    ("DEFINE VAR v = 5 MEASURE 'Sales stage'[m] = v * 2 EVALUATE ROW(\"m\", [m])", 10),
    ("DEFINE MEASURE 'Sales stage'[m] = v * 2 VAR v = 5 EVALUATE ROW(\"m\", [m])", 10),
    ("DEFINE MEASURE 'Sales stage'[m] = 21 VAR v = [m] * 2 EVALUATE ROW(\"v\", v)", 42),
])
def test_define_var_and_measure_see_each_other(text, value):
    assert one(text)["rows"] == [[value]]


def test_define_var_is_a_query_constant_not_the_measures_context():
    # evaluated once in the query's context: 4 rows wherever it is used
    t = one("DEFINE VAR n = COUNTROWS('Opp') MEASURE 'Opp'[k] = n "
            "EVALUATE ADDCOLUMNS(VALUES('Opp'[Rank]), \"k\", [k])")
    assert [r[1] for r in t["rows"]] == [4, 4, 4, 4]


def test_define_var_table():
    t = one("DEFINE VAR t = {1, 2} EVALUATE t")
    assert cols(t) == [("[Value]", "Integer")] and t["rows"] == [[1], [2]]


def test_define_var_referring_to_itself_is_an_error():
    with pytest.raises(DAXEvaluationError):
        run('DEFINE VAR a = b VAR b = a EVALUATE ROW("x", a)')


def test_define_table():
    t = one("DEFINE TABLE T2 = {1, 2} EVALUATE T2")
    assert cols(t)[0][0] == "T2[Value]" and t["rows"] == [[1], [2]]


def test_define_column():
    t = one("DEFINE COLUMN 'Sales stage'[c] = 'Sales stage'[Probability] * 2 "
            "EVALUATE TOPN(2, SELECTCOLUMNS('Sales stage', \"c\", 'Sales stage'[c]), [c], ASC)")
    assert cols(t) == [("[c]", "Double")]
    assert t["rows"] == [[0.2], [0.4]]


def test_define_column_leaves_the_model_alone():
    run("DEFINE COLUMN 'Sales stage'[c] = 1 EVALUATE 'Sales stage'")
    assert TABLES[STAGE]["columns"] == ["Probability", "Sales Stage", "Sales Stage ID"]
    assert all(len(r) == 3 for r in TABLES[STAGE]["rows"])
    with pytest.raises(DAXEvaluationError):
        run("EVALUATE SELECTCOLUMNS('Sales stage', \"c\", 'Sales stage'[c])")


def test_query_vars_do_not_leak_into_the_next_query():
    run('DEFINE VAR leak = 1 EVALUATE ROW("x", leak)')
    from pbix_mcp.dax import engine as de
    assert not de._engine._query_vars
