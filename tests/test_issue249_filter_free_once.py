"""Issue #249: FILTER evaluates a conjunct that reads nothing the row
changes once per scan, not once per row.

FILTER tries its column predicates first, then its row-free conjuncts, then
the rest (Power BI Desktop 2.152, build_b255d.py f1 / f2; B255 e12). A
row-free conjunct has the same value -- or the same error -- on every row of
the scan, so it is evaluated at the first row that reaches it and reused:
evaluated on every row, COUNTROWS(FILTER(T, T[v] * 2 > 1990 && MAXX(ALL(T),
T[v]) > 0)) was quadratic in the rows (11 s over 2,000 rows).

The reuse must not change an answer. A column inside an aggregate or an
iterator is no read of the row for ordering the conjuncts, but RANKX's
expression is also evaluated for the current row, a nested FILTER over
another table reads the scan's column, and EARLIER reads it by definition:
those conjuncts are evaluated on every row. The guards' values are exact
arithmetic over T(v) = 1..10 and U(k) = 0, 2, 4, 6, 8; the Desktop answers
are quoted by probe id.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "T": {"columns": ["v", "g"], "rows": [[v, v % 3] for v in range(1, 11)]},
    "U": {"columns": ["k"], "rows": [[k] for k in range(0, 10, 2)]},
}
HOME = {"m_all": "COUNTROWS(ALL(T))"}

# B255d's model (build_b255d.py) for the Desktop answers
TM = {
    "TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]},
}
TM_HOME = {"m_zero": "COUNTROWS(ALL(TM)) - 4"}
ERROR = "ERROR"


def _run(dax, tables=TABLES, home=HOME, count=None):
    eng = de.DAXEngine()
    calls = [0]
    if count is not None:
        inner = eng._eval_expr

        def counting(expr, ctx, var_scope=None):
            if expr.strip() == count:
                calls[0] += 1
            return inner(expr, ctx, var_scope)

        eng._eval_expr = counting  # type: ignore[method-assign]
    got = eng.evaluate_measure("p", de.DAXContext(tables, {**home, "p": dax}))
    if "p" in eng.eval_errors:
        got = ERROR
    return got, calls[0]


@pytest.mark.parametrize("dax, conj, expected", [
    # no column predicate (T[v] * 2 is no column): the row-free conjunct
    # goes first, and was evaluated on all 10 rows; v * 2 > 7 keeps 4..10
    ("COUNTROWS(FILTER(T, T[v] * 2 > 7 && COUNTROWS(ALL(T)) > 0))", "COUNTROWS(ALL(T)) > 0", 7),
    ("COUNTROWS(FILTER(T, T[v] * 2 > 7 && MAXX(ALL(T), T[v]) > 9))", "MAXX(ALL(T), T[v]) > 9", 7),
    # v > 3 keeps 7 rows (4..10); the row-free conjunct is TRUE
    ("COUNTROWS(FILTER(T, T[v] > 3 && COUNTROWS(ALL(T)) > 0))", "COUNTROWS(ALL(T)) > 0", 7),
    ("COUNTROWS(FILTER(T, T[v] > 3 && MAXX(ALL(T), T[v]) > 9))", "MAXX(ALL(T), T[v]) > 9", 7),
    ("COUNTROWS(FILTER(T, T[v] > 3 && [m_all] = 10))", "[m_all] = 10", 7),
    ("COUNTROWS(FILTER(T, T[v] > 3 && SUM(T[v]) = 55))", "SUM(T[v]) = 55", 7),
    # written first: the column predicate still goes first, then it once
    ("COUNTROWS(FILTER(T, MAXX(ALL(T), T[v]) > 9 && T[v] > 3))", "MAXX(ALL(T), T[v]) > 9", 7),
    # the CALCULATE argument
    ("CALCULATE(SUM(T[v]), FILTER(T, T[v] > 3 && COUNTROWS(ALL(T)) > 0))", "COUNTROWS(ALL(T)) > 0",
     sum(range(4, 11))),
])
def test_row_free_conjunct_evaluated_once_per_scan(dax, conj, expected):
    got, calls = _run(dax, count=conj)
    assert got == expected
    assert calls == 1


def test_false_row_free_conjunct_empties_the_scan_once():
    got, calls = _run("COUNTROWS(FILTER(T, T[v] > 3 && COUNTROWS(ALL(T)) > 99))",
                      count="COUNTROWS(ALL(T)) > 99")
    assert got is None
    assert calls == 1


def test_each_scan_has_its_own_memo():
    # the inner FILTER runs once per row of U; its row-free conjunct reads
    # U[k]: T has 10, 8, 6, 4, 2 values above k = 0, 2, 4, 6, 8, so it is TRUE
    # for k = 0, 2, 4 and each of those scans keeps the 7 rows above 3
    got, _ = _run("SUMX(U, COUNTROWS(FILTER(T, T[v] > 3 && COUNTROWS(FILTER(ALL(T), T[v] > U[k])) > 5)))")
    assert got == 3 * 7


@pytest.mark.parametrize("dax, expected", [
    # RANKX's expression is also evaluated for the scan's row: v = 10, 9, 8
    ("COUNTROWS(FILTER(T, T[v] > 3 && RANKX(ALL(T), T[v]) <= 3))", 3),
    # a nested FILTER over U reads the scan's T[v]: v = 4, 6, 8
    ("COUNTROWS(FILTER(T, T[v] > 3 && COUNTROWS(FILTER(ALL(U), U[k] = T[v])) > 0))", 3),
    # EARLIER reads the scan's row: v - 1 values below v, at least 6 for v >= 7
    ("COUNTROWS(FILTER(T, T[v] > 3 && COUNTROWS(FILTER(ALL(T), T[v] < EARLIER(T[v]))) >= 6))", 4),
    # MIN of two values reads the row: v >= 5
    ("COUNTROWS(FILTER(T, T[v] > 3 && MIN(T[v], 5) = 5))", 6),
])
def test_conjuncts_reading_the_scan_row_are_evaluated_per_row(dax, expected):
    assert _run(dax)[0] == expected


@pytest.mark.parametrize("pid, dax, expected", [
    # Power BI Desktop 2.152, build_b255d.py f1: the column predicate first,
    # then the row-free conjunct (FALSE), so MOD never runs
    ("f1", "COUNTROWS(FILTER(TM, MOD(TM[n], TM[d]) = 1 && [m_zero] <> 0))", None),
    # build_b255.py e12: the column predicate rejects every row
    ("e12", 'COUNTROWS(FILTER(TM, TM[d] = 99 && ERROR("x")))', None),
    # build_b255d.py c37: the guard first, then MOD on the rows it keeps
    ("c37", "CALCULATE(COUNTROWS(TM), FILTER(TM, MOD(7, TM[d]) = 1 && TM[d] <> 0))", 2),
])
def test_desktop_answers_on_the_memo_path(pid, dax, expected):
    assert _run(dax, TM, TM_HOME)[0] == expected, pid


def test_row_free_error_raises_on_every_scan_that_reaches_it():
    # the error is reused, not swallowed: TM[d] <> 0 keeps 3 rows, and MOD(1,
    # [m_zero]) divides by zero whatever the row
    got, calls = _run("COUNTROWS(FILTER(TM, TM[d] <> 0 && MOD(1, [m_zero]) = 1))", TM, TM_HOME,
                      count="MOD(1, [m_zero]) = 1")
    assert got == ERROR
    assert calls == 1


def test_conjunct_reading_the_row_through_rankx_stays_where_written():
    # RANKX(ALL(T), T[v]) reads the scan's row, so it is no row-free
    # conjunct: tried after T[v] * 2 > 17 as written, on its 2 rows (v = 9,
    # 10) -- moved first it ran on all 10 rows, quadratic in the rows
    got, calls = _run("COUNTROWS(FILTER(T, T[v] * 2 > 17 && RANKX(ALL(T), T[v]) <= 2))",
                      count="RANKX(ALL(T), T[v]) <= 2")
    assert got == 2
    assert calls == 2
