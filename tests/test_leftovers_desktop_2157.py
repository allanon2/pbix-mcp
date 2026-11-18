"""IN with one value, GROUPBY's own columns, and measures whose text is not DAX,
against Power BI Desktop 2.157's answers.

Every expected value was measured on Desktop 2.157 (2026-11-18, engine
17.0.83.18): the query text sent over its loopback ADOMD connection, on a
synthetic model of this shape (Opp: Rank Int64, Name String, Flag Boolean, When
DateTime; _M, no rows, home of Total = SUM(Opp[Rank]) and of four measures whose
text is not DAX). Behaviour, not code: what Desktop returned, nothing read
from it.

- ``<value> IN <table>`` compares as CONTAINSROW does: a value of another type
  than the table's is an error, whether or not a row would have matched;
  ``1 IN {1, "a"}`` (a Text table) is "Function 'CONTAINSROW' does not support
  comparing values of type Text with values of type Integer. ...", the
  table's type first. ``"a" IN {1, "a"}`` and ``"A" IN ...`` are TRUE,
  BLANK() IN {1, "a"} FALSE.
- GROUPBY's own columns are headed ``[Name]`` whatever their name, as
  ADDCOLUMNS' are; two of one name (any case) are "Function 'GROUPBY' cannot
  add column [X], as it has the same name with an existing column."
- A measure whose text is not DAX (``1 +* 2``, ``1 2``, ``1 +``) is kept, and
  any query that touches it, or a measure using it, is "MdxScript(Model)
  (1, 1) Failed to resolve name 'SYNTAXERROR'. It is not a valid table,
  variable, or function name.": IFERROR catches it, ISERROR is TRUE, a
  query not touching it answers.
"""
from __future__ import annotations

import datetime as dt

import pytest

from pbix_mcp.dax.engine import DAXContext, DAXEngine
from pbix_mcp.dax.query import execute_query
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit

TABLES = {
    "Opp": {"columns": ["Rank", "Name", "Flag", "When"],
            "rows": [[1, "a", True, dt.datetime(2026, 1, 5)], [2, "b", False, dt.datetime(2026, 1, 6)],
                     [3, "a", True, dt.datetime(2026, 1, 9)]]},
    "Sales stage": {"columns": ["Probability", "Sales Stage", "Sales Stage ID"],
                    "rows": [[0.1, "Lead", 1], [0.2, "Qualify", 2]]},
}
TYPES = {("Opp", "Rank"): "Int64", ("Opp", "Name"): "String", ("Opp", "Flag"): "Boolean",
         ("Opp", "When"): "DateTime", ("Sales stage", "Probability"): "Double",
         ("Sales stage", "Sales Stage"): "String", ("Sales stage", "Sales Stage ID"): "Int64"}
MEASURES = {"Total": "SUM(Opp[Rank])", "Bad": "1 +* 2", "Bad2": "1 2", "Bad3": "1 +",
            "UsesBad": "[Bad] + 1"}
HOMES = {m: "_M" for m in MEASURES}


def context(measures: dict, homes: dict) -> DAXContext:
    c = DAXContext(TABLES, measures)
    c.measure_tables = homes
    return c


def run(text: str) -> list:
    return execute_query(text, TABLES, dict(MEASURES), measure_tables=dict(HOMES), column_types=TYPES)


def one(text: str) -> dict:
    (t,) = run(text)
    return t


def cols(t: dict) -> list:
    return [(c["name"], c["type"]) for c in t["columns"]]


def error(text: str) -> str:
    with pytest.raises(DAXEvaluationError) as e:
        run(text)
    return e.value.message


def _clash(first: str, second: str) -> str:
    return (f"Function 'CONTAINSROW' does not support comparing values of type {first} with values of type "
            f"{second}. Consider using the VALUE or FORMAT function to convert one of the values.")


# ---------------------------------------------------------------------------
# One value IN a table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, value", [
    ('EVALUATE ROW("x", "a" IN {1, "a"})', True),
    ('EVALUATE ROW("x", "A" IN {1, "a"})', True),
    ('EVALUATE ROW("x", BLANK() IN {1, "a"})', False),
])
def test_one_value_in_a_text_table(text, value):
    assert one(text)["rows"] == [[value]]


@pytest.mark.parametrize("text, first, second", [
    ('EVALUATE ROW("x", 1 IN {1, "a"})', "Text", "Integer"),
    ('EVALUATE ROW("x", 2 IN {1, "a"})', "Text", "Integer"),
    ('EVALUATE ROW("x", NOT (2 IN {1, "a"}))', "Text", "Integer"),
    ('EVALUATE ROW("x", "1" IN {1})', "Integer", "Text"),
    ('EVALUATE ROW("x", 1 IN {"1"})', "Text", "Integer"),
    ('EVALUATE ROW("x", "a" IN {1, 2})', "Integer", "Text"),
    ('EVALUATE ROW("x", TRUE() IN {1})', "Integer", "True/False"),
    ('EVALUATE ROW("x", DATE(2026,1,5) IN {"1/5/2026"})', "Text", "Date"),
    ("EVALUATE ROW(\"n\", COUNTROWS(FILTER('Opp', 'Opp'[Rank] IN {1, \"a\"})))", "Text", "Integer"),
    ("EVALUATE ROW(\"n\", COUNTROWS(FILTER('Opp', 'Opp'[Name] IN {1})))", "Integer", "Text"),
    ('EVALUATE ROW("x", CONTAINSROW({1, "a"}, 1))', "Text", "Integer"),
    ('EVALUATE ROW("x", CONTAINSROW({1}, "1"))', "Integer", "Text"),
])
def test_a_value_of_another_type_is_containsrows_error(text, first, second):
    # Desktop's text carries the IN's (or CONTAINSROW's) position as Query (l, c): the engine raises
    # it at run time, where the query route defers positions (as for every run-time error)
    assert error(text).endswith(_clash(first, second))


def test_numbers_of_either_kind_compare():
    # Integer against Double is no clash (the engine's own rule, as before)
    assert one('EVALUATE ROW("x", 1.0 IN {1, 2})')["rows"] == [[True]]
    assert one('EVALUATE ROW("x", 2 IN {1.5, 2})')["rows"] == [[True]]


# ---------------------------------------------------------------------------
# GROUPBY's own columns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, values", [
    ("Rank", [["a", 2], ["b", 1]]),          # Opp has a column Rank
    ("Probability", [["a", 2], ["b", 1]]),   # another table's column
])
def test_groupby_heads_its_columns_by_name_alone(name, values):
    t = one(f"EVALUATE GROUPBY('Opp', 'Opp'[Name], \"{name}\", SUMX(CURRENTGROUP(), 1))")
    assert [c["name"] for c in t["columns"]] == ["Opp[Name]", f"[{name}]"]
    assert sorted(t["rows"]) == values


def test_groupby_a_column_named_as_a_group_column_is_refused():
    # Desktop: Opp[Name] and [Name] side by side; a row here holds a column by its name alone, so the engine
    # says it can't, never overwriting the group column
    from pbix_mcp.errors import DAXUnsupportedError
    with pytest.raises(DAXUnsupportedError):
        run("EVALUATE GROUPBY('Opp', 'Opp'[Name], \"Name\", SUMX(CURRENTGROUP(), 1))")


def test_groupby_with_no_group_column():
    t = one("EVALUATE GROUPBY('Opp', \"Rank\", SUMX(CURRENTGROUP(), 'Opp'[Rank]))")
    assert [c["name"] for c in t["columns"]] == ["[Rank]"]
    assert t["rows"] == [[6]]


def test_groupby_two_columns_of_one_name():
    assert error("EVALUATE GROUPBY('Opp', 'Opp'[Name], \"x\", SUMX(CURRENTGROUP(), 1), \"X\", "
                 "SUMX(CURRENTGROUP(), 2))").endswith(
        "Function 'GROUPBY' cannot add column [X], as it has the same name with an existing column.")


# ---------------------------------------------------------------------------
# Measures whose text is not DAX
# ---------------------------------------------------------------------------

SYNTAXERROR = ("MdxScript(Model) (1, 1) Failed to resolve name 'SYNTAXERROR'. It is not a valid table, "
               "variable, or function name.")


@pytest.mark.parametrize("text", [
    'EVALUATE ROW("x", [Bad])',
    "EVALUATE ROW(\"x\", '_M'[Bad])",
    'EVALUATE ROW("x", [Bad2])',
    'EVALUATE ROW("x", [Bad3])',
    'EVALUATE ROW("x", [UsesBad])',
    'EVALUATE ROW("t", [Total], "x", [Bad])',
    "EVALUATE SUMMARIZECOLUMNS('Opp'[Name], \"x\", [Bad])",
])
def test_a_measure_that_is_not_dax_fails_the_query(text):
    assert error(text) == SYNTAXERROR


def test_iferror_and_iserror_catch_it():
    assert one('EVALUATE ROW("x", IFERROR([Bad], -1))')["rows"] == [[-1]]
    assert one('EVALUATE ROW("x", ISERROR([Bad]))')["rows"] == [[True]]


def test_a_query_not_touching_it_answers():
    assert one('EVALUATE ROW("t", [Total])')["rows"] == [[6]]
    assert sorted(one("EVALUATE SUMMARIZECOLUMNS('Opp'[Name], \"t\", [Total])")["rows"]) == [["a", 4], ["b", 2]]


def test_the_measure_route_reports_it_as_an_error():
    # the per-filter route (report visuals): the measure's value is no number; its error is recorded
    eng = DAXEngine()
    assert eng.evaluate_measure("Bad", context(dict(MEASURES), dict(HOMES))) is None
    assert eng.eval_errors["Bad"].endswith(SYNTAXERROR)
    eng2 = DAXEngine()
    assert eng2.evaluate_measure("UsesBad", context(dict(MEASURES), dict(HOMES))) is None
    assert eng2.eval_errors["UsesBad"].endswith(SYNTAXERROR)
    assert DAXEngine().evaluate_measure("Total", context(dict(MEASURES), dict(HOMES))) == 6


@pytest.mark.parametrize("expr", [
    # valid measure texts the check must pass: what the public samples hold, in kind
    "VAR a = 1 RETURN a + 1",
    "CALCULATE(SUM(Opp[Rank]), Opp[Name] = \"a\")",
    "IF(ISBLANK([Total]), BLANK(), [Total] * -1)",
    "SWITCH(TRUE(), [Total] > 1, \"big\", \"small\")",
    "-[Total]",
    "NOT ISBLANK([Total])",
    "[Total] IN {1, 2}",
    "MAX(Opp[Rank]) >= 1 && MAX(Opp[Rank]) <= 3 || TRUE()",
    "// a comment\n[Total]",
    "/* block */ [Total] -- trailing",
    "FORMAT([Total], \"0.0%\")",
    "MAX('Opp'[Rank])",
])
def test_valid_measure_text_still_evaluates(expr):
    m = dict(MEASURES, Probe=expr)
    eng = DAXEngine()
    eng.evaluate_measure("Probe", context(m, dict(HOMES, Probe="_M")))
    assert "Probe" not in eng.eval_errors, eng.eval_errors.get("Probe")


# ---------------------------------------------------------------------------
# 'T'[Measure] under a table in no ``tables`` (#339's last leftover)
# ---------------------------------------------------------------------------

def _measure_of(homes: dict, name: str, model_columns: dict | None = None):
    t = {"Opp": {"columns": ["Rank"], "rows": [[1], [2], [3]]}}
    m = {"Total": "SUM(Opp[Rank])", "Uses": "'_M'[Total] + 1"}
    c = DAXContext(t, m)
    c.measure_tables = homes
    c.model_columns = model_columns or {}
    e = DAXEngine()
    return e.evaluate_measure(name, c), e.eval_errors.get(name)


def test_a_measure_whose_home_is_unknown_is_taken_under_any_table():
    # it leaked its (table, name) marker: a BLANK, and no error recorded
    assert _measure_of({}, "Uses") == (7, None)
    assert _measure_of({}, "Uses", {"Opp": ["Rank"], "_M": ["Column1"]}) == (7, None)


def test_under_a_table_the_model_does_not_name_or_not_its_home_it_is_an_error():
    for homes, model_columns in (({}, {"Opp": ["Rank"]}), ({"Total": "Opp"}, {})):
        value, err = _measure_of(homes, "Uses", model_columns)
        assert value is None
        assert err.endswith("Column 'Total' in table '_M' cannot be found or may not be used in this expression")


def test_a_column_of_an_empty_table_sharing_a_measures_name_stays_the_column():
    t = {"Opp": {"columns": ["Rank"], "rows": [[1], [2], [3]]}}
    m = {"Amount": "SUM(Opp[Rank]) * 100", "Probe": "SELECTEDVALUE('E'[Amount], -1)"}
    for homes in ({"Amount": "Opp"}, {}):
        c = DAXContext(t, m)
        c.measure_tables = homes
        c.model_columns = {"Opp": ["Rank"], "E": ["Amount"]}
        e = DAXEngine()
        assert e.evaluate_measure("Probe", c) == -1, e.eval_errors





def test_groupby_a_column_named_as_a_group_column_in_another_case_fits():
    t = one("EVALUATE GROUPBY('Opp', 'Opp'[Name], \"name\", SUMX(CURRENTGROUP(), 1))")
    assert [c["name"] for c in t["columns"]] == ["Opp[Name]", "[name]"]
    assert sorted(t["rows"]) == [["a", 2], ["b", 1]]


def test_a_dt_literal_is_not_called_a_syntax_error():
    m = dict(MEASURES, Probe='IF(dt"2026-01-05" > 0, 1, 0)')
    eng = DAXEngine()
    eng.evaluate_measure("Probe", context(m, dict(HOMES, Probe="_M")))
    assert "SYNTAXERROR" not in eng.eval_errors.get("Probe", "")


# ---------------------------------------------------------------------------
# #340: CALCULATE's filters, mixed tables, the last pairs (Desktop 2.157, 2026-11-18, leftovers340.json)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, first, second", [
    ("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[Rank] IN {1, \"a\"}))", "Text", "Integer"),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[Name] IN {1}))", "Integer", "Text"),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[Rank] IN {\"1\"}))", "Text", "Integer"),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[When] IN {DATE(2026,1,5), \"x\"}))", "Text", "Date"),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[Flag] IN {1}))", "Integer", "True/False"),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[Flag] IN {\"a\"}))", "Text", "True/False"),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], KEEPFILTERS(Opp[Rank] IN {1, \"a\"})))", "Text", "Integer"),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], NOT Opp[Rank] IN {1, \"a\"}))", "Text", "Integer"),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], FILTER(ALL(Opp[Rank]), Opp[Rank] IN {1, \"a\"})))", "Text", "Integer"),
    ("EVALUATE CALCULATETABLE(VALUES(Opp[Name]), Opp[Rank] IN {1, \"a\"})", "Text", "Integer"),
    ("EVALUATE SUMMARIZECOLUMNS('Opp'[Name], FILTER(ALL(Opp[Rank]), Opp[Rank] IN {1, \"a\"}), \"t\", [Total])",
     "Text", "Integer"),
    # IFERROR around the CALCULATE does not catch it: checked before anything runs
    ("EVALUATE ROW(\"x\", IFERROR(CALCULATE([Total], Opp[Rank] IN {1, \"a\"}), -1))", "Text", "Integer"),
    ('EVALUATE ROW("x", 1 IN UNION({1}, {"a"}))', "Text", "Integer"),
    ("EVALUATE ROW(\"x\", 1 IN UNION(VALUES(Opp[Rank]), VALUES(Opp[Name])))", "Text", "Integer"),
    ('EVALUATE ROW("x", TRUE() IN {"a"})', "Text", "True/False"),
    ('EVALUATE ROW("x", "a" IN {TRUE()})', "True/False", "Text"),
])
def test_340_another_type_is_containsrows_error(text, first, second):
    assert error(text).endswith(_clash(first, second))


def test_340_a_calculate_equality_of_text_with_a_number():
    assert error("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[Name] = 1))") == (
        "DAX comparison operations do not support comparing values of type Text with values of type Integer. "
        "Consider using the VALUE or FORMAT function to convert one of the values.")
    assert error("EVALUATE ROW(\"x\", IFERROR(CALCULATE([Total], Opp[Name] = 1), -1))").startswith("DAX comparison")


@pytest.mark.parametrize("text, rows", [
    ("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[Rank] IN {1, 3}))", [[4]]),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[Name] IN {\"A\"}))", [[4]]),
    ("EVALUATE ROW(\"x\", CALCULATE([Total], Opp[When] IN {46027}))", [[1]]),     # a date serial
    ('EVALUATE ROW("x", "a" IN UNION({1}, {"a"}))', [[True]]),
    ("EVALUATE ROW(\"x\", \"a\" IN UNION(VALUES(Opp[Rank]), VALUES(Opp[Name])))", [[True]]),
    ('EVALUATE ROW("x", DATE(2026,1,5) IN {46027})', [[True]]),
    ('EVALUATE ROW("x", 46027 IN {DATE(2026,1,5)})', [[True]]),
    ("EVALUATE ROW(\"n\", COUNTROWS(FILTER('Opp', 'Opp'[When] IN {46027})))", [[1]]),
    ('EVALUATE ROW("x", CURRENCY(1) IN {1, 2})', [[True]]),
])
def test_340_what_compares(text, rows):
    assert one(text)["rows"] == rows


def _model_dates():
    """Opp with its When column as a decoded model holds it (DAXDateTime, a datetime subclass)."""
    from pbix_mcp.formats.vertipaq_decoder import _oa_date_to_python
    t = {k: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]} for k, v in TABLES.items()}
    for r in t["Opp"]["rows"]:
        r[3] = _oa_date_to_python(float((r[3] - dt.datetime(1899, 12, 30)).days))
    assert type(t["Opp"]["rows"][0][3]) is not dt.datetime
    return t


@pytest.mark.parametrize("probe, first, second", [
    ('CALCULATE([Total], Opp[When] IN {DATE(2026,1,5), "x"})', "Text", "Date"),
    ('CALCULATE([Total], Opp[When] IN {"x"})', "Text", "Date"),
    ('COUNTROWS(FILTER(Opp, Opp[When] IN {"x"}))', "Text", "Date"),
])
def test_340_a_models_own_date_type_is_checked(probe, first, second):
    c = DAXContext(_model_dates(), dict(MEASURES, Probe=probe))
    c.measure_tables = dict(HOMES, Probe="_M")
    e = DAXEngine()
    assert e.evaluate_measure("Probe", c) is None
    assert e.eval_errors["Probe"].endswith(_clash(first, second))


@pytest.mark.parametrize("serial", ["3000000", "-700000", "1e10"])
def test_340_a_serial_past_any_date_matches_nothing(serial):
    # as the FILTER route answers it (not measured on Desktop)
    assert one(f'EVALUATE ROW("x", CALCULATE([Total], Opp[When] IN {{{serial}}}))')["rows"] == [[None]]


def test_340_an_expressions_mixed_column_compares_as_before():
    # Desktop's reading of a Variant expression column is not measured: only constructors and UNION are Text
    assert one('EVALUATE ROW("x", 2 IN SELECTCOLUMNS(Opp, "v", IF(Opp[Rank] = 1, "x", Opp[Rank])))')["rows"] == [[True]]


# ---------------------------------------------------------------------------
# #341, #342, #343 (Desktop 2.157, 2026-11-18, leftovers341.json): SUMMARIZECOLUMNS' filter tables, UNION's column
# types, comparisons of values of different types. Generated from Desktop's answers: columns, types and rows (in any
# order), or the error's text after its "Query (l, c) ".
# ---------------------------------------------------------------------------

TABLES_341 = dict(TABLES, **{"Sales stage": {"columns": ["Probability", "Sales Stage", "Sales Stage ID"],
                                              "rows": [[0.1, "Lead", 1], [0.2, "Qualify", 2], [0.4, "Solution", 3],
                                                       [0.6, "Proposal", 4], [0.8, "Finalize", 5]]},
                              "_M": {"columns": ["Column1"], "rows": []}})
TYPES_341 = {**TYPES, ("_M", "Column1"): "String"}
MEASURES_341 = {"Total": "SUM(Opp[Rank])"}


def _run_341(text: str) -> list:
    return execute_query(text, TABLES_341, dict(MEASURES_341), measure_tables={"Total": "_M"}, column_types=TYPES_341)


def _norm_341(v):
    if isinstance(v, dt.datetime):
        return ("dt", v.isoformat()[:19])
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


ROWS_341 = [
    ('341 filter all rank', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], FILTER(ALL(Opp[Rank]), Opp[Rank] = 1), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     [['a', 1]]),
    ('341 treatas rank', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], TREATAS({1}, Opp[Rank]), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     [['a', 1]]),
    ('341 keepfilters filter', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], KEEPFILTERS(FILTER(ALL(Opp[Rank]), Opp[Rank] = 1)), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     [['a', 1]]),
    ('341 filter flag', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], FILTER(ALL(Opp[Flag]), Opp[Flag] = TRUE()), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     [['a', 4]]),
    ('341 treatas group col', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], TREATAS({"a"}, Opp[Name]), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     [['a', 4]]),
    ('341 two filters', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], TREATAS({1, 2, 3}, Opp[Rank]), FILTER(ALL(Opp[Flag]), Opp[Flag] = TRUE()), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     [['a', 4]]),
    ('341 unrelated table filter', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], TREATAS({"Lead"}, \'Sales stage\'[Sales Stage]), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     [['a', 4], ['b', 2]]),
    ('341 no measure', "EVALUATE SUMMARIZECOLUMNS('Opp'[Name], TREATAS({1}, Opp[Rank]))",
     [('Opp[Name]', 'Text')],
     [['a']]),
    ('341 no group', 'EVALUATE SUMMARIZECOLUMNS(TREATAS({1}, Opp[Rank]), "t", [Total])',
     [('[t]', 'Integer')],
     [[1]]),
    ('341 filter removes all', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], TREATAS({99}, Opp[Rank]), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     []),
    ('341 values filter', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], FILTER(VALUES(Opp[Rank]), Opp[Rank] >= 2), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     [['a', 3], ['b', 2]]),
    ('341 filter whole table', 'EVALUATE SUMMARIZECOLUMNS(\'Opp\'[Name], FILTER(Opp, Opp[Rank] = 3), "t", [Total])',
     [('Opp[Name]', 'Text'), ('[t]', 'Integer')],
     [['a', 3]]),
    ('341 count', 'EVALUATE ROW("n", COUNTROWS(SUMMARIZECOLUMNS(\'Opp\'[Name], TREATAS({1}, Opp[Rank]), "t", [Total])))',
     [('[n]', 'Integer')],
     [[1]]),
    ('342 union int double', 'EVALUATE UNION({1}, {2.5})',
     [('[Value]', 'Double')],
     [[1], [2.5]]),
    ('342 union date int', 'EVALUATE UNION({DATE(2026,1,6)}, {1})',
     [('[Value]', 'Double')],
     [[46028], [1]]),
    ('342 union bool int', 'EVALUATE UNION({TRUE()}, {1})',
     [('[Value]', 'Integer')],
     [[1], [1]]),
    ('342 union date text', 'EVALUATE UNION({DATE(2026,1,6)}, {"a"})',
     [('[Value]', 'Text')],
     [['1/6/2026'], ['a']]),
    ('342 union bool text', 'EVALUATE UNION({TRUE()}, {"a"})',
     [('[Value]', 'Text')],
     [['TRUE'], ['a']]),
    ('342 union double text', 'EVALUATE UNION({2.5}, {"a"})',
     [('[Value]', 'Text')],
     [['2.5'], ['a']]),
    ('342 union blank text', 'EVALUATE UNION({BLANK()}, {"a"})',
     [('[Value]', 'Text')],
     [[None], ['a']]),
    ('342 union row names', 'EVALUATE UNION(ROW("x", 1), ROW("y", "a"))',
     [('[x]', 'Text')],
     [['1'], ['a']]),
    ('342 union two cols mixed', 'EVALUATE UNION(SELECTCOLUMNS(Opp, "r", Opp[Rank], "n", Opp[Name]), SELECTCOLUMNS(Opp, "n", Opp[Name], "r", Opp[Rank]))',
     [('[r]', 'Text'), ('[n]', 'Text')],
     [['1', 'a'], ['3', 'a'], ['2', 'b'], ['a', '1'], ['a', '3'], ['b', '2']]),
    ('342 union text first', 'EVALUATE UNION({"a"}, {1})',
     [('[Value]', 'Text')],
     [['a'], ['1']]),
    ('342 union columns', 'EVALUATE UNION(VALUES(Opp[Rank]), VALUES(Opp[Name]))',
     [('Opp[Rank]', 'Text')],
     [['1'], ['2'], ['3'], ['a'], ['b']]),
    ('342 union countrows', 'EVALUATE ROW("n", COUNTROWS(UNION({1}, {"a"}, {1})))',
     [('[n]', 'Integer')],
     [[3]]),
    ('342 union distinct', 'EVALUATE DISTINCT(UNION({1}, {"1"}))',
     [('[Value]', 'Text')],
     [['1']]),
    ('343 calc date eq serial', 'EVALUATE ROW("x", CALCULATE([Total], Opp[When] = 46027))',
     [('[x]', 'Integer')],
     [[1]]),
    ('343 treatas serial', 'EVALUATE ROW("x", CALCULATE([Total], TREATAS({46027}, Opp[When])))',
     [('[x]', 'Integer')],
     [[1]]),
    ('343 treatas text on number', 'EVALUATE ROW("x", CALCULATE([Total], TREATAS({"1"}, Opp[Rank])))',
     [('[x]', 'Integer')],
     [[1]]),
    ('343 neg serial a', 'EVALUATE ROW("x", DATE(1899,12,29) + TIME(6,0,0) IN {-1.25})',
     [('[x]', 'Boolean')],
     [[False]]),
    ('343 neg serial b', 'EVALUATE ROW("x", DATE(1899,12,28) + TIME(18,0,0) IN {-1.25})',
     [('[x]', 'Boolean')],
     [[True]]),
    ('343 neg serial value', 'EVALUATE ROW("x", CONVERT(-1.25, DATETIME))',
     [('[x]', 'DateTime')],
     [[('dt', '1899-12-29T06:00:00')]]),
]


@pytest.mark.parametrize("case, text, columns, rows", ROWS_341, ids=[c[0] for c in ROWS_341])
def test_341_342_343_desktops_rows(case, text, columns, rows):
    (t,) = _run_341(text)
    assert [(c["name"], c["type"]) for c in t["columns"]] == [tuple(c) for c in columns]
    assert sorted(map(repr, ([_norm_341(v) for v in r] for r in t["rows"]))) == sorted(map(repr, rows))


ERRORS_341 = [
    ('343 calc or', 'EVALUATE ROW("x", CALCULATE([Total], Opp[Name] = 1 || Opp[Rank] = 1))',
     'DAX comparison operations do not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 filter text eq number', 'EVALUATE ROW("x", COUNTROWS(FILTER(Opp, Opp[Name] = 1)))',
     'DAX comparison operations do not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 calc ne', 'EVALUATE ROW("x", CALCULATE([Total], Opp[Name] <> 1))',
     'DAX comparison operations do not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 calc gt', 'EVALUATE ROW("x", CALCULATE([Total], Opp[Name] > 1))',
     'DAX comparison operations do not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 calc literal first', 'EVALUATE ROW("x", CALCULATE([Total], 1 = Opp[Name]))',
     'DAX comparison operations do not support comparing values of type Integer with values of type Text. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 calc text eq true', 'EVALUATE ROW("x", CALCULATE([Total], Opp[Name] = TRUE()))',
     'DAX comparison operations do not support comparing values of type Text with values of type True/False. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 calc bool eq text', 'EVALUATE ROW("x", CALCULATE([Total], Opp[Flag] = "a"))',
     'DAX comparison operations do not support comparing values of type True/False with values of type Text. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 calc bool eq number', 'EVALUATE ROW("x", CALCULATE([Total], Opp[Flag] = 1))',
     'DAX comparison operations do not support comparing values of type True/False with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 calc date eq text', 'EVALUATE ROW("x", CALCULATE([Total], Opp[When] = "1/5/2026"))',
     'DAX comparison operations do not support comparing values of type Date with values of type Text. Consider using the VALUE or FORMAT function to convert one of the values.'),
    pytest.param('343 empty table col in number', 'EVALUATE ROW("x", CALCULATE([Total], \'_M\'[Column1] IN {1}))',
     "Function 'CONTAINSROW' does not support comparing values of type Integer with values of type Text. Consider using the VALUE or FORMAT function to convert one of the values.", marks=pytest.mark.xfail(strict=True, reason="Desktop checks types before anything runs: an IF branch never taken, an empty table's declared column type (not done)")),
    pytest.param('343 if false branch', 'EVALUATE ROW("x", IF(FALSE(), CALCULATE([Total], Opp[Rank] IN {"a"}), 7))',
     "Function 'CONTAINSROW' does not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.", marks=pytest.mark.xfail(strict=True, reason="Desktop checks types before anything runs: an IF branch never taken, an empty table's declared column type (not done)")),
    ('343 scalar text eq number', 'EVALUATE ROW("x", "a" = 1)',
     'DAX comparison operations do not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 scalar text number eq', 'EVALUATE ROW("x", "1" = 1)',
     'DAX comparison operations do not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 scalar text lt number', 'EVALUATE ROW("x", "a" < 1)',
     'DAX comparison operations do not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('343 iferror plain in', 'EVALUATE ROW("x", IFERROR(1 IN {"a"}, -1))',
     "Function 'CONTAINSROW' does not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values."),
    ('343 iferror filter eq', 'EVALUATE ROW("x", IFERROR(COUNTROWS(FILTER(Opp, Opp[Name] = 1)), -1))',
     'DAX comparison operations do not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.'),
]


@pytest.mark.parametrize("case, text, message", ERRORS_341, ids=[(c.values[0] if hasattr(c, "values") else c[0]) for c in ERRORS_341])
def test_341_342_343_desktops_errors(case, text, message):
    with pytest.raises(DAXEvaluationError) as e:
        _run_341(text)
    assert e.value.message.rstrip(".") == message.rstrip(".")


# Variants, date-like text, TREATAS and UNION over model columns, INTERSECT (Desktop 2.157, 2026-11-18, leftoversv.json):
# a comparison is checked by its operands' declared types, a Variant by none

ROWS_V = [
    ('v selectedvalue strict eq', 'EVALUATE ROW("x", CALCULATE(IF(SELECTEDVALUE(Opp[Name], 0) == 0, "none", "one"), Opp[Name] = "a"))',
     [('[x]', 'Text')],
     [['one']]),
    ('v selectedvalue eq', 'EVALUATE ROW("x", CALCULATE(IF(SELECTEDVALUE(Opp[Name], 0) = 0, "none", "one"), Opp[Name] = "a"))',
     [('[x]', 'Text')],
     [['one']]),
    ('v selectedvalue eq iferror', 'EVALUATE ROW("x", IFERROR(CALCULATE(IF(SELECTEDVALUE(Opp[Name], 0) = 0, "none", "one"), Opp[Name] = "a"), "err"))',
     [('[x]', 'Text')],
     [['one']]),
    ('v selectedvalue alt text', 'EVALUATE ROW("x", IF(SELECTEDVALUE(Opp[Rank], "none") = 1, 1, 0))',
     [('[x]', 'Integer')],
     [[0]]),
    ('v if variant eq', 'EVALUATE ROW("x", IF(IF(1 = 1, "x", 1) = 1, 1, 0))',
     [('[x]', 'Integer')],
     [[0]]),
    ('v if variant eq iferror', 'EVALUATE ROW("x", IFERROR(IF(IF(1 = 1, "x", 1) = 1, 1, 0), -1))',
     [('[x]', 'Integer')],
     [[0]]),
    ('v coalesce', 'EVALUATE ROW("x", IF(COALESCE(MAX(Opp[Name]), 0) = 0, 1, 2))',
     [('[x]', 'Integer')],
     [[2]]),
    ('v sumx variant', 'EVALUATE ROW("x", SUMX(Opp, IF(IF(Opp[Rank] = 1, "x", Opp[Rank]) = 2, 1, 0)))',
     [('[x]', 'Integer')],
     [[1]]),
    ('v switch variant', 'EVALUATE ROW("x", SWITCH(TRUE(), IF(1 = 1, "x", 1) = 1, 1, 0))',
     [('[x]', 'Integer')],
     [[0]]),
    ('v treatas serial table', 'EVALUATE TREATAS({46027}, Opp[When])',
     [('Opp[When]', 'DateTime')],
     [[('dt', '2026-01-05T00:00:00')]]),
    ('v treatas text date table', 'EVALUATE TREATAS({"2026-01-05"}, Opp[When])',
     [('Opp[When]', 'DateTime')],
     [[('dt', '2026-01-05T00:00:00')]]),
    ('v union date rank', 'EVALUATE UNION(VALUES(Opp[When]), VALUES(Opp[Rank]))',
     [('Opp[When]', 'Double')],
     [[46027], [46028], [46031], [1], [2], [3]]),
    ('v union flag rank', 'EVALUATE UNION(VALUES(Opp[Flag]), VALUES(Opp[Rank]))',
     [('Opp[Flag]', 'Integer')],
     [[0], [1], [1], [2], [3]]),
]


@pytest.mark.parametrize("case, text, columns, rows", ROWS_V, ids=[c[0] for c in ROWS_V])
def test_v_desktops_rows(case, text, columns, rows):
    test_341_342_343_desktops_rows(case, text, columns, rows)


ERRORS_V = [
    ('v strict text number', 'EVALUATE ROW("x", 1 == "a")',
     'DAX comparison operations do not support comparing values of type Integer with values of type Text. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('v date eq iso text', 'EVALUATE ROW("x", DATE(2026,1,5) = "2026-01-05")',
     'DAX comparison operations do not support comparing values of type Date with values of type Text. Consider using the VALUE or FORMAT function to convert one of the values.'),
    ('v filter date eq text', 'EVALUATE ROW("x", COUNTROWS(FILTER(Opp, Opp[When] = "1/5/2026")))',
     'DAX comparison operations do not support comparing values of type Date with values of type Text. Consider using the VALUE or FORMAT function to convert one of the values.'),
    pytest.param('v blank text max eq number', 'EVALUATE ROW("x", IF(MAX(\'_M\'[Column1]) = 1, 1, 0))',
     'DAX comparison operations do not support comparing values of type Text with values of type Integer. Consider using the VALUE or FORMAT function to convert one of the values.', marks=pytest.mark.xfail(strict=True, reason="an empty table's declared column type (not known to the engine)")),
    ('v intersect union text', 'EVALUATE ROW("n", COUNTROWS(INTERSECT(UNION({1}, {"a"}), VALUES(Opp[Rank]))))',
     "Function 'INTERSECT' does not support joining a column of type Text with a column of type Integer."),
]


@pytest.mark.parametrize("case, text, message", ERRORS_V, ids=[(c.values[0] if hasattr(c, "values") else c[0]) for c in ERRORS_V])
def test_v_desktops_errors(case, text, message):
    test_341_342_343_desktops_errors(case, text, message)


def test_a_variant_value_in_a_calculate_filter_is_checked_by_no_type():
    # inferred from the measured rule (a Variant is checked by no type: IF(1 = 1, "x", 1) = 1 is FALSE), not asked
    # in this form: the text column against a Variant 1 compares, keeping no row
    assert _run_341('EVALUATE ROW("x", CALCULATE([Total], Opp[Name] = IF(1 = 1, 1, "x")))')[0]["rows"] == [[None]]
