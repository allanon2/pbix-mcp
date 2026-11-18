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
