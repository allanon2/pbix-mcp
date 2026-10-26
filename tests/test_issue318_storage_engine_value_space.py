"""#318 (#248): in a storage-engine iteration -- SUMX /
AVERAGEX / MINX / MAXX or a FILTER condition over a model table whose row
expression is + - * / of the table's columns and constants (VAR included) --
Desktop computes Currency * Double on the VALUES: the Double product of the
Currency's value and the Double, x 10000 in Double, rounded half away from
zero. Where the formula engine computes it (IF, ROUND, ABS, DIVIDE,
ADDCOLUMNS, CALCULATE(...) * x, a CONCATENATEX row) the product is the
count's, as in a scalar. Currency / Integer is the count's in both.

What was wrong (0.9.147): every such product was an unrounded Double.

Expected values: Power BI Desktop 2.152's g() / r() cells over ADOMD (type
letter + FORMAT(CONVERT(x, DOUBLE), "0.000000000"); type letter + counts of
1/10,000), builders verbatim: build_b254b.py b_se_basic, b_se_aggs,
b_se_shapes, b_se_list, b_se_list_div (T3 = ten rows of 0.0003 = 3 counts,
-0.0003 and a Double 0.5; TR = counts -7 .. 7) and build_b254.py s_half,
s_other, s_fe, s_order (T1 = ten rows of 0.0001, -0.0001 and 0.5). The
value-space product 0.0003 * 0.5 is 0.00014999999999999999, 1.4999999999999998
counts, so 1 per row; the count-space one 1.5, so 2.

build_b248g.py (Desktop 2.152, test_desktop_answers_b248g): p_se_tables --
ALLSELECTED(T3) and ALLNOBLANKROW(T3) are pushed down as ALL(T3) is (C0.001;
the engine of round 3 iterated them in the formula engine, C0.002), and so
are FILTER(ALL(T3), ..), CALCULATETABLE(FILTER(T3, ..)), RELATEDTABLE(T3)
outside a row context and VAR t = FILTER(T3, ..). p_se_div0_filter: a zero
divisor in a FILTER condition fails the query with the range message.
p_fe_div0_iter: SUMX(T3, IF(TRUE(), T3[v] / (T3[id] - 1))) fails the query
the same way -- an IF whose condition is a constant is its branch, folded
before the scan; the engine raised the formula engine's "Division by zero
was encountered.".
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def r(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE) * 10000, "0.00"))'


def cells(*xs):
    return ' & " " & '.join(xs)


def rows_list(table, cell, order):
    return f'CONCATENATEX({table}, {cell}, ",", {order}, ASC)'


def _tables():
    tables = {
        "T3": {"columns": ["id", "v", "n", "d"], "rows": [[i, 0.0003, -0.0003, 0.5] for i in range(1, 11)]},
        "T1": {"columns": ["v", "n", "d"], "rows": [[0.0001, -0.0001, 0.5] for _ in range(10)]},
        "TR": {"columns": ["id", "v"], "rows": [[i, (i - 8) / 10000] for i in range(1, 16)]},
    }
    de.mark_currency_columns(tables, {"T3": ["v", "n"], "T1": ["v", "n"], "TR": ["v"]})
    return tables


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(_tables(), {}, None, None, None, []))


PROBES = {
    "b_se_basic": cells(g("SUMX(T3, T3[v] * 0.5)"), g("SUMX(T3, T3[n] * 0.5)"), g("SUMX(T3, T3[v] / 2)"),
                        g("SUMX(T3, T3[v] * T3[d])"), g("SUMX(T3, 0.5 * T3[v])"), g("SUMX(T3, T3[v] * 1.5)"),
                        g("SUMX(T3, T3[v] * 0.5 * 2)"), g("SUMX(T3, T3[v] * 3 * 0.5)")),
    "b_se_aggs": cells(g("AVERAGEX(T3, T3[v] * 0.5)"), g("MAXX(T3, T3[v] * 0.5)"), g("MINX(T3, T3[v] * 0.5)"),
                       g("SUMX(FILTER(T3, T3[d] > 0), T3[v] * 0.5)"),
                       g("COUNTROWS(FILTER(T3, T3[v] * 0.5 = 0.0002)) + 0"),
                       g("COUNTROWS(FILTER(T3, T3[v] * 0.5 = 0.0001)) + 0")),
    "b_se_shapes": cells(g("SUMX(T3, IF(T3[d] > 0, T3[v] * 0.5))"), g("SUMX(T3, T3[v] * 0.5 + 0)"),
                         g("SUMX(T3, VAR x = T3[v] * 0.5 RETURN x)"), g("SUMX(T3, ROUND(T3[v] * 0.5, 4))"),
                         g("SUMX(T3, DIVIDE(T3[v], 2))"), g('SUMX(ADDCOLUMNS(T3, "w", T3[v] * 0.5), [w])'),
                         g("SUMX(VALUES(T3[id]), CALCULATE(SUM(T3[v])) * 0.5)"), g("SUMX(T3, ABS(T3[v] * 0.5))")),
    "b_se_list": rows_list("GENERATESERIES(1, 15)",
                           "VAR k = [Value] RETURN " + r("CALCULATE(SUMX(TR, TR[v] * 0.5), TR[id] = k)"), "[Value]"),
    "b_se_list_div": rows_list("GENERATESERIES(1, 15)",
                               "VAR k = [Value] RETURN " + r("CALCULATE(SUMX(TR, TR[v] / 2), TR[id] = k)"),
                               "[Value]"),
    "s_half": cells(g("SUMX(T1, T1[v] * 0.5)"), g("SUMX(T1, T1[n] * 0.5)"), g("SUMX(T1, T1[v] / 2)"),
                    g("SUMX(T1, T1[n] / 2)"), g("AVERAGEX(T1, T1[v] * 0.5)")),
    "s_other": cells(g("SUMX(T1, T1[v] * 1.5)"), g("SUMX(T1, T1[v] * 2.5)"), g("SUMX(T1, T1[v] * 0.9)"),
                     g("SUMX(T1, T1[v] * T1[d])"), g("SUMX(T1, DIVIDE(T1[v], 2))")),
    "s_fe": cells(g("SUMX(T1, IF(T1[d] > 0, T1[v] * 0.5))"), g("SUMX(T1, T1[v] * 0.5 + 0)"),
                  g("SUMX(T1, VAR x = T1[v] * 0.5 RETURN x)"), g("SUMX(T1, ROUND(T1[v] * 0.5, 4))")),
    "s_order": cells(g("SUMX(T1, 3 * T1[v] * (1 - T1[d]))"), g("SUMX(T1, T1[v] * (1 - T1[d]) * 3)"),
                     g("SUMX(T1, T1[v] * ((1 - T1[d]) * 3))")),
}
DESKTOP = {
    "b_se_basic": "C0.001000000 C-0.001000000 C0.002000000 C0.001000000 C0.001000000 C0.005000000 C0.002000000 "
                  "C0.005000000",
    "b_se_aggs": "C0.000100000 C0.000100000 C0.000100000 C0.001000000 I0.000000000 I10.000000000",
    "b_se_shapes": "C0.002000000 C0.001000000 C0.001000000 C0.002000000 C0.002000000 C0.002000000 C0.002000000 "
                   "C0.002000000",
    "b_se_list": "C-4.00,C-3.00,C-3.00,C-2.00,C-1.00,C-1.00,C-1.00,C0.00,C1.00,C1.00,C1.00,C2.00,C3.00,C3.00,C4.00",
    "b_se_list_div": "C-4.00,C-3.00,C-3.00,C-2.00,C-2.00,C-1.00,C-1.00,C0.00,C1.00,C1.00,C2.00,C2.00,C3.00,C3.00,"
                     "C4.00",
    "s_half": "C0.001000000 C-0.001000000 C0.001000000 C-0.001000000 C0.000100000",
    "s_other": "C0.002000000 C0.003000000 C0.001000000 C0.001000000 C0.001000000",
    "s_fe": "C0.001000000 C0.001000000 C0.001000000 C0.001000000",
    "s_order": "C0.001000000 C0.003000000 C0.002000000",
}


@pytest.mark.parametrize("name", list(DESKTOP))
def test_desktop_answers(name):
    assert _ev(PROBES[name]) == DESKTOP[name]


# build_b248c.py: T3 = ten rows of 0.0003 (3 counts), a Double 0.5 and k = 1;
# D3 one row (1, 0.5, Fixed Decimal 0.25), T3[k] -> D3[k]. Value space is
# C0.001 for ten rows of T3[v] * 0.5, count space C0.002.
def _ev_c(expr):
    tables = {"T3": {"columns": ["id", "v", "d", "k"], "rows": [[i, 0.0003, 0.5, 1] for i in range(1, 11)]},
              "D3": {"columns": ["k", "p", "pc"], "rows": [[1, 0.5, 0.25]]}}
    de.mark_currency_columns(tables, {"T3": ["v"], "D3": ["pc"]})
    rels = [{"FromTable": "T3", "FromColumn": "k", "ToTable": "D3", "ToColumn": "k", "IsActive": 1}]
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(tables, {}, None, None, None, rels))


PROBES_C = {
    # RELATED, CALCULATETABLE with a filter, a FILTER with an IF condition,
    # && and unary minus in storage-engine shapes
    "d_se_div": cells(g("SUMX(T3, T3[v] / 0.4)"), g("SUMX(T3, T3[v] / T3[d])"), g("SUMX(T3, T3[v] / 2.5)"),
                      g("SUMX(T3, T3[v] * RELATED(D3[p]))"), g("SUMX(CALCULATETABLE(T3, T3[id] > 0), T3[v] * 0.5)"),
                      g("SUMX(FILTER(T3, IF(T3[id] > 0, TRUE(), FALSE())), T3[v] * 0.5)"),
                      g("COUNTROWS(FILTER(T3, T3[v] * 0.5 = 0.0001 && T3[id] > 0)) + 0"),
                      g("SUMX(T3, -T3[v] * 0.5)")),
    # which iterated tables are pushed down: ALL, CALCULATETABLE,
    # RELATEDTABLE and a VAR holding the table are; TOPN, SAMPLE and FILTER
    # over TOPN are not
    "d_se_tables": cells(g("SUMX(TOPN(10, T3, T3[id]), T3[v] * 0.5)"), g("SUMX(SAMPLE(10, T3, T3[id]), T3[v] * 0.5)"),
                         g("SUMX(ALL(T3), T3[v] * 0.5)"), g("SUMX(CALCULATETABLE(T3), T3[v] * 0.5)"),
                         g("AVERAGEX(TOPN(10, T3, T3[id]), T3[v] * 0.5)"),
                         g("SUMX(D3, SUMX(RELATEDTABLE(T3), T3[v] * 0.5))"),
                         g("VAR t = T3 RETURN SUMX(t, T3[v] * 0.5)"),
                         g("SUMX(FILTER(TOPN(10, T3, T3[id]), T3[d] > 0), T3[v] * 0.5)")),
    "d_div0_se_divide": g("SUMX(T3, DIVIDE(T3[v], T3[id] - 1))"),
}
DESKTOP_C = {
    "d_se_div": "C0.008000000 C0.006000000 C0.001000000 C0.001000000 C0.001000000 C0.001000000 I10.000000000 "
                "C-0.001000000",
    "d_se_tables": "C0.002000000 C0.002000000 C0.001000000 C0.001000000 C0.000200000 C0.001000000 C0.001000000 "
                   "C0.002000000",
    "d_div0_se_divide": "C0.000900000",
}


@pytest.mark.parametrize("name", list(DESKTOP_C))
def test_desktop_answers_b248c(name):
    assert _ev_c(PROBES_C[name]) == DESKTOP_C[name]


@pytest.mark.parametrize("expr", ["SUMX(T3, T3[v] / (T3[id] - 1))", g("SUMX(T3, T3[v] / (T3[id] - 1))")])
def test_a_storage_engine_quotient_by_zero_is_out_of_range(expr):
    """build_b248c.py d_div0_se_raw, d_div0_se_g: one row's divisor is 0;
    the query fails (ExecuteReader) with "The result of a conversion or
    arithmetic operation is either too large or too small.", ISERROR
    around it included."""
    from pbix_mcp.errors import DAXEvaluationError
    with pytest.raises(DAXEvaluationError) as ei:
        _ev_c(expr)
    assert "The result of a conversion or arithmetic operation is either too large or too small." in str(ei.value)
    assert getattr(ei.value, "_dax_uncatchable", False)


def test_a_formula_engine_row_is_count_space():
    # build_b254b.py b_fe_row: the same product in a CONCATENATEX row is 2 counts
    assert _ev(rows_list("TOPN(1, T3, T3[id], ASC)", r("T3[v] * 0.5"), "T3[id]")) == "C2.00"


def test_a_column_marked_later_is_taken_as_currency():
    """The storage-engine verdict is memoized per row expression on the
    model's cache. A column recorded as Fixed Decimal after a first
    evaluation (mark_currency_columns; the server marks again after the
    calculated tables) starts the cache over, so a verdict read while the
    column was a Double is not served (review of this fix). Desktop:
    build_b254b.py b_se_basic cell 0, SUMX(T3, T3[v] * 0.5) is C0.001."""
    tables = {"T3": {"columns": ["id", "v", "n"], "rows": [[i, 0.0003, -0.0003] for i in range(1, 11)]}}
    de.mark_currency_columns(tables, {"T3": ["n"]})
    measures: dict = {}
    eng = de.DAXEngine()
    first = eng._eval_expr("SUMX(T3, T3[v] * 0.5)", de.DAXContext(tables, measures, None, None, None, []))
    assert type(first) is float                     # v is a Double so far
    de.mark_currency_columns(tables, {"T3": ["v"]})
    ctx = de.DAXContext(tables, measures, None, None, None, [])
    assert eng._eval_expr(g("SUMX(T3, T3[v] * 0.5)"), ctx) == "C0.001000000"


RANGE_MSG = "The result of a conversion or arithmetic operation is either too large or too small."


def test_desktop_answers_b248g():
    # build_b248g.py p_se_tables verbatim, Desktop 2.152 (desk_b248g.json)
    expr = cells(g("SUMX(ALLSELECTED(T3), T3[v] * 0.5)"), g("SUMX(ALLNOBLANKROW(T3), T3[v] * 0.5)"),
                 g("SUMX(FILTER(ALL(T3), T3[id] > 0), T3[v] * 0.5)"),
                 g("SUMX(CALCULATETABLE(FILTER(T3, T3[id] > 0)), T3[v] * 0.5)"),
                 g("SUMX(RELATEDTABLE(T3), T3[v] * 0.5)"),
                 g("VAR t = FILTER(T3, T3[id] > 0) RETURN SUMX(t, T3[v] * 0.5)"))
    assert _ev_c(expr) == " ".join(["C0.001000000"] * 6)


@pytest.mark.parametrize("expr", ["COUNTROWS(FILTER(T3, T3[v] / (T3[id] - 1) > 0))",
                                  "SUMX(T3, IF(TRUE(), T3[v] / (T3[id] - 1)))"],
                         ids=["p_se_div0_filter", "p_fe_div0_iter"])
def test_desktop_zero_divisor_b248g(expr):
    """build_b248g.py p_se_div0_filter, p_fe_div0_iter: the query fails
    (ExecuteReader) with "The result of a conversion or arithmetic operation
    is either too large or too small."."""
    from pbix_mcp.errors import DAXEvaluationError
    with pytest.raises(DAXEvaluationError) as ei:
        _ev_c(expr)
    assert RANGE_MSG in str(ei.value)
    assert getattr(ei.value, "_dax_uncatchable", False)


@pytest.mark.parametrize("expr, expected", [
    # a constant condition is its branch: value space (C0.001); IF(FALSE())
    # without an else is BLANK
    (g("SUMX(T3, IF(TRUE(), T3[v] * 0.5))"), "C0.001000000"),
    (g("SUMX(T3, IF(FALSE(), 1, T3[v] * 0.5))"), "C0.001000000"),
    (g("SUMX(T3, IF(FALSE(), T3[v] * 0.5))"), "B"),
    # a condition that reads the row stays the formula engine's
    # (build_b254b.py b_se_shapes cell 0)
    (g("SUMX(T3, IF(T3[d] > 0, T3[v] * 0.5))"), "C0.002000000"),
])
def test_constant_if_folds_to_its_branch(expr, expected):
    assert _ev_c(expr) == expected
