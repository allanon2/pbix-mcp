"""#287: a column read where no row context holds it is Desktop's error.

What was wrong: a table-qualified column reference with no row context to read it from -- inside
CALCULATE's expression, which consumes the iterated row, or under an iteration over a table of
OTHER columns -- resolved to a (table, column) marker that each consumer read its own way:
SUMX(FILTER(F, F[Id] = 3), CALCULATE(F[v])) took the one visible value (8),
CONCATENATEX(.., CALCULATE(F[v] & "")) concatenated the marker's text "('F', 'v')",
SUMX(F, CALCULATE(F[v] + 0)) was BLANK, and CONCATENATEX(ALL(S[Reg]), S[Cat], ",") gave ",".

Desktop refuses each with "A single value for column 'v' in table 'F' cannot be determined. This
can happen when a measure formula refers to a column that contains many values without specifying
an aggregation such as min, max, count, or sum to get a single result." (translated from Desktop's
Norwegian UI; the English is Microsoft's), before evaluating anything: IF(FALSE(), <it>, 1) fails
too, and IFERROR around it in the same expression does not catch it. So a measure's expression is
checked before it is evaluated (DAXEngine._static_refusal), conservatively: only where every call
around the column is one whose row context the check knows.

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, batteries B256c
(build_b256c.py b_one_row, b_concat, b_iferr, b_iffalse), B256b (build_b256b.py rl_col_in_calc)
over the model of tests/test_issue250_userelationship_date_part.py, and B253c (build_b253c.py
f_col_no_row) over build_b253b.py's table S, rebuilt here. The controls are exact arithmetic over
those tables (each F row's v a distinct power of two; S[V] 30 + 35 + 40 + 75), a variable taking
the row's value before the CALCULATE as B256b's rl_var does for RELATED (172).
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

MSG = ("A single value for column '{c}' in table '{t}' cannot be determined. This can happen when a "
       "measure formula refers to a column that contains many values without specifying an aggregation "
       "such as min, max, count, or sum to get a single result.")
ERRORS = {
    "b_one_row": "SUMX(FILTER(F, F[Id] = 3), CALCULATE(F[v]))",
    "b_concat": 'CONCATENATEX(FILTER(F, F[Id] = 3), CALCULATE(F[v] & ""))',
    "b_iferr": "IFERROR(SUMX(F, CALCULATE(F[v] + 0)), -1)",
    "b_iffalse": "IF(FALSE(), SUMX(F, CALCULATE(F[v] + 0)), 1)",
    "rl_col_in_calc": "SUMX(F, CALCULATE(F[v] + 0))",
}
VALUES = {
    "sumx_row": ("SUMX(F, F[v])", 16383),
    "var_before_calculate": ("SUMX(F, VAR x = F[v] RETURN CALCULATE(x + 0))", 16383),
    "sum_in_calculate": ("SUMX(F, CALCULATE(SUM(F[v])))", 16383),
    "values_own_column": ("SUMX(VALUES(F[pk]), F[pk])", 6),
}
ALL_MEASURES = {**MEASURES, **ERRORS, **{k: e for k, (e, _w) in VALUES.items()}}

S_TABLES = {"S": {"columns": ["Cat", "Reg", "V"],
                  "rows": [["A", "N", 30], ["B", "N", 35], ["C", "S", 40], ["D", "S", 75]]}}
S_MEASURES = {"f_col_no_row": 'CONCATENATEX(ALL(S[Reg]), S[Cat], ",")',
              "cols_own": "SUMX(ALL(S[Cat], S[V]), S[V])"}


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


@pytest.mark.parametrize("name", list(ERRORS))
def test_a_column_inside_calculate_has_no_row(name, path):
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, dict(ALL_MEASURES), None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) is None
    assert eng.eval_errors.get(name, "").endswith(MSG.format(t="F", c="v")), eng.eval_errors.get(name)


def test_a_column_the_rows_do_not_hold(path):
    eng = de.DAXEngine()
    ctx = de.DAXContext(S_TABLES, dict(S_MEASURES), None, None, {}, [])
    assert eng.evaluate_measure("f_col_no_row", ctx) is None
    assert eng.eval_errors.get("f_col_no_row", "").endswith(MSG.format(t="S", c="Cat"))
    assert de.evaluate_measure("cols_own", S_TABLES, S_MEASURES, {}, relationships=[]) == 180


@pytest.mark.parametrize("name", list(VALUES))
def test_a_column_the_row_holds(name, path):
    got = de.evaluate_measure(name, TABLES, ALL_MEASURES, {}, relationships=RELS)
    assert got == VALUES[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_in_one_batch(order, path):
    names = list(ERRORS) + list(VALUES)
    if order == "reverse":
        names.reverse()
    got = de.evaluate_measures_batch(names, TABLES, dict(ALL_MEASURES), {}, relationships=RELS)
    assert got == {**{n: None for n in ERRORS}, **{n: w for n, (_e, w) in VALUES.items()}}


# Battery B256d (build_b256d.py), Desktop 2.152: the shapes the first fix left to the evaluation
# are refused too -- an outer row's column under an inner iteration past a CALCULATE
# (b_outer_in_inner), a column the rows of FILTER's table of other columns do not hold, in an
# iterator's table argument (b_filter_other_col), both with the message above; an unqualified [v]
# under an iteration over VALUES(F[pk]) is "Column 'v' cannot be found or may not be used in this
# expression" (b_bare_col; Desktop's Norwegian text translated). The engine answered BLANK for all
# three (0.9.147 too). Through a REFERENCE, IFERROR catches the error (a_ref_col -1), and without
# it the referencing measure fails (a_ref_col_plus). The controls are exact arithmetic.
H_COL = "SUMX(F, CALCULATE(F[v] + 0))"
ERRORS_D = {
    "b_outer_in_inner": ("SUMX(F, CALCULATE(SUMX(P, P[x] + F[v])))", MSG.format(t="F", c="v")),
    "b_filter_other_col": ("SUMX(FILTER(ALL(F[pk]), F[v] > 1), 1)", MSG.format(t="F", c="v")),
    "b_bare_col": ("SUMX(VALUES(F[pk]), [v])", "Column 'v' cannot be found or may not be used in this expression."),
    "a_ref_col_plus": ("[h_col] + 1", MSG.format(t="F", c="v")),
}
VALUES_D = {
    "a_ref_col": ("IFERROR([h_col], -1)", -1),
    "inner_own_row": ("SUMX(F, CALCULATE(SUMX(F, F[v])))", 16383),
    "var_outer_row_inner": ("SUMX(F, VAR o = F[v] RETURN CALCULATE(SUMX(ALL(P), o)))", 3 * 16383),
    "filter_own_col_table_arg": ("SUMX(FILTER(ALL(F[pk]), F[pk] > 1), 1)", 2),
    "bare_measure_in_cols": ("SUMX(VALUES(F[pk]), [one])", 3),
    "bare_extension_column": ('SUMX(ADDCOLUMNS(VALUES(F[pk]), "v", 1), [v])', 3),
}
D_MEASURES = {**ALL_MEASURES, "h_col": H_COL, "one": "1",
              **{k: e for k, (e, _m) in ERRORS_D.items()}, **{k: e for k, (e, _w) in VALUES_D.items()}}


@pytest.mark.parametrize("name", list(ERRORS_D))
def test_shapes_left_to_the_evaluation_before(name, path):
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, dict(D_MEASURES), None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) is None
    assert eng.eval_errors.get(name, "").endswith(ERRORS_D[name][1]), eng.eval_errors.get(name)


@pytest.mark.parametrize("name", list(VALUES_D))
def test_shapes_that_answer(name, path):
    got = de.evaluate_measure(name, TABLES, D_MEASURES, {}, relationships=RELS)
    assert got == VALUES_D[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_b256d_in_one_batch(order, path):
    names = list(ERRORS) + list(VALUES) + list(ERRORS_D) + list(VALUES_D)
    if order == "reverse":
        names.reverse()
    got = de.evaluate_measures_batch(names, TABLES, dict(D_MEASURES), {}, relationships=RELS)
    assert got == {**{n: None for n in ERRORS}, **{n: w for n, (_e, w) in VALUES.items()},
                   **{n: None for n in ERRORS_D}, **{n: w for n, (_e, w) in VALUES_D.items()}}
