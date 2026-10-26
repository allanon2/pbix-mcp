"""#338: UNION names its columns after the first table.

Microsoft's DAX reference for UNION: the result's column names are those
of table_expression1, and the columns of the tables are matched by
position. The engine chained the tables' rows as they were, so a later
table's rows kept their own column names and a read by the first table's
name found nothing on them: over TS(G, L) and TCU(v, L),
SUMX(UNION(TS, TCU), [G]) added BLANK for TCU's rows (7 where TCU's v
makes it 10.5), UNION(SELECTCOLUMNS(TS, "a", ...), SELECTCOLUMNS(TD, "b",
...)) could not be read as [a] at all ("[a] cannot be found"), and
CONCATENATEX over UNION(TS, TCU) ordered by [G] put TCU's rows first, as
BLANK. Every later table's rows are now renamed to the first table's
names by position: single-column rows (VALUES, ALL, a table constructor),
model-table rows, SELECTCOLUMNS / ADDCOLUMNS rows, row constructors, and
any number of tables. A renamed row is a copy, no row of a model table:
a UNION of columns of different lineage has none (Microsoft's reference),
so a context transition over it filters no table by it.

Expected values: exact arithmetic on Microsoft's documented rule (sums,
counts and orders of the tables' rows under the first table's names).
The column typing (an Int64 column beside a Currency one is Currency,
beside a Double one Double) is #326's, measured on Power BI
Desktop 2.152 (test_issue326_mixed_number_columns.py).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TS": {"columns": ["G", "L"], "rows": [[0, "z"], [1, "p"], [1, "q"], [2, "r"], [3, "s"]]},
    "TD": {"columns": ["M", "D"], "rows": [["x", 0.5], ["y", 1.5], ["e", 1.0]]},
    "TCU": {"columns": ["v", "L"], "rows": [[1.0, "c1"], [2.5, "c2"]], "currency_columns": frozenset({"v"})},
}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


def _typ(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISCURRENCY({x}), "C", IF(ISDOUBLE({x}), "D", '
            f'IF(ISINT64({x}), "I", "?"))))')


SA = 'SELECTCOLUMNS(TS, "a", TS[G])'
SB = 'SELECTCOLUMNS(TD, "b", TD[D])'


@pytest.mark.parametrize("expr,want", [
    # model-table rows: TCU's v is read as [G] (0+1+1+2+3 + 1+2.5)
    ("SUMX(UNION(TS, TCU), [G])", 10.5),
    ("MAXX(UNION(TS, TCU), [G])", 3),
    ("MINX(UNION(TCU, TS), [v])", 0),
    ("SUMX(UNION(TCU, TS), [v])", 10.5),
    # SELECTCOLUMNS rows: TD's b is read as [a] (7 + 0.5+1.5+1.0)
    (f"SUMX(UNION({SA}, {SB}), [a])", 10),
    (f"COUNTROWS(FILTER(UNION({SA}, {SB}), [a] > 1))", 3),
    # single-column rows: VALUES / ALL of a column, table constructors
    ("SUMX(UNION(VALUES(TS[G]), VALUES(TD[D])), [G])", 9),
    ("SUMX(UNION({1, 2}, ALL(TD[D])), [Value])", 6),
    ("SUMX(UNION(VALUES(TD[D]), {1, 2}), [D])", 6),
    ("SUMX(UNION(TS, ROW(\"a\", 5, \"b\", \"x\")), [G])", 12),
    ("SUMX(UNION({(1, \"a\")}, ROW(\"x\", 3, \"y\", \"b\")), [Value1])", 4),
    ("CONCATENATEX(UNION({(1, \"a\")}, ROW(\"x\", 3, \"y\", \"b\")), [Value2], \",\", [Value1], ASC)", "a,b"),
    # ADDCOLUMNS rows: the added columns by position too (5 * 1 + 2 * 2)
    ('SUMX(UNION(ADDCOLUMNS(TS, "w", 1), ADDCOLUMNS(TCU, "z", 2)), [w])', 9),
    ('SUMX(UNION(ADDCOLUMNS(TS, "w", 1), ADDCOLUMNS(TCU, "z", 2)), [G])', 10.5),
    # more than two tables
    (f"SUMX(UNION({SA}, {SB}, {{7}}), [a])", 17),
    ('SUMX(UNION(TS, {(5, "x")}, TCU), [G])', 15.5),
    ("COUNTROWS(UNION(TS, TCU, TS))", 12),
])
def test_later_tables_read_by_the_first_tables_names(expr, want):
    assert _ev(expr) == want


@pytest.mark.parametrize("expr,want", [
    # every row ordered by the first table's G (TCU's v), ties by L
    ('CONCATENATEX(UNION(TS, TCU), [L], ",", [G], ASC, [L], ASC)', "z,c1,p,q,r,c2,s"),
    ('CONCATENATEX(UNION(TCU, TS), [L], ",", [v], ASC, [L], ASC)', "z,c1,p,q,r,c2,s"),
    ('CONCATENATEX(UNION(TS, TCU), [L], ",", [G], DESC, [L], DESC)', "s,c2,r,q,p,c1,z"),
    ('CONCATENATEX(UNION(TS, {(5, "x")}, TCU), [L], ",", [G], ASC, [L], ASC)', "z,c1,p,q,r,c2,s,x"),
])
def test_concatenatex_orders_by_the_first_tables_column(expr, want):
    assert _ev(expr) == want


def test_concatenatex_order_of_the_issue():
    """CONCATENATEX(UNION(TS, TCU), [L], ",", [G], ASC): TCU's rows sit by
    their v (1 among the 1s, 2.5 between 2 and 3), not first as BLANK."""
    got = _ev('CONCATENATEX(UNION(TS, TCU), [L], ",", [G], ASC)').split(",")
    assert got[0] == "z" and got[-1] == "s" and got[-3:] == ["r", "c2", "s"]
    assert sorted(got[1:4]) == ["c1", "p", "q"]


def test_positional_set_functions_still_agree():
    """DISTINCT, EXCEPT and INTERSECT match columns by position as before:
    TCU's 2 rows are no rows of TS, TS twice is TS, and the [a] column is a
    Double column whose 1, 1, 2, 1.5 and 1.0 meet {1, 2, 1.5} and whose
    distinct values are 0, 1, 2, 3, 0.5 and 1.5."""
    assert _ev("COUNTROWS(EXCEPT(UNION(TS, TCU), TS))") == 2
    assert _ev("COUNTROWS(DISTINCT(UNION(TS, TCU, TS)))") == 7
    assert _ev(f"COUNTROWS(INTERSECT(UNION({SA}, {SB}), {{1, 2, 1.5}}))") == 5
    assert _ev(f"COUNTROWS(DISTINCT(UNION({SA}, {SB})))") == 6     # 1 and 1.0 are one Double


def test_number_types_unify_under_the_first_name():
    """An Int64 column beside a Currency one is Currency, beside a Double
    one Double (#326), now that both sides name it alike; a
    renamed raw Fixed Decimal cell stays a Currency without the Int64
    side's help."""
    assert _ev(_typ("MINX(UNION(TS, TCU), [G])")) == "C"
    assert _ev(_typ("MAXX(UNION(TS, TCU), [G])")) == "C"
    assert _ev(_typ(f"MAXX(UNION({SA}, {SB}), [a])")) == "D"
    assert _ev(_typ('MINX(UNION(ROW("c", CURRENCY(5), "l", "k"), TCU), [c])')) == "C"
    assert _ev('MINX(UNION(ROW("c", CURRENCY(5), "l", "k"), TCU), [c])') == 1


def test_renamed_rows_filter_no_table_in_a_context_transition():
    """A renamed row has no lineage (its columns' lineage differs from the
    first table's): the transition over a TCU row of UNION(TS, TCU) leaves
    TCU unfiltered (7 rows x COUNTROWS(TCU) = 2 is 14; the TS rows filter
    TS only), and over a TS row of UNION(TCU, TS) leaves TS unfiltered (7
    rows x SUM(TS[G]) = 7 is 49)."""
    assert _ev("SUMX(UNION(TS, TCU), CALCULATE(COUNTROWS(TCU)))") == 14
    assert _ev("SUMX(UNION(TCU, TS), CALCULATE(SUM(TS[G])))") == 49
    # UNION of a table with itself keeps its rows, and their filters
    assert _ev("SUMX(UNION(TS, TS), CALCULATE(SUM(TS[G])))") == 14


def test_rows_are_copied_and_the_first_side_kept():
    """Exact on the helper: the first side's rows and rows already named
    alike are the very rows; a renamed row is a new row of the first
    side's names, without __row__, and the model's rows are untouched."""
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {})
    ts = eng._eval_expr("TS", ctx)
    ts2 = eng._eval_expr("FILTER(TS, TS[G] > 1)", ctx)
    tcu = eng._eval_expr("TCU", ctx)
    sides = eng._union_named([ts, ts2], ctx)
    assert sides[0] is ts and sides[1] is ts2
    sides = eng._union_named([ts, tcu, ts2], ctx)
    assert sides[0] is ts and sides[2] is ts2
    assert [r["G"] for r in sides[1]] == [1.0, 2.5] and [r["L"] for r in sides[1]] == ["c1", "c2"]
    assert all("__row__" not in r and r["__table__"] == "" and "v" not in r for r in sides[1])
    assert tcu[0] == {"__table__": "TCU", "__row__": True, "v": 1.0, "L": "c1"}
    assert TABLES["TCU"]["rows"] == [[1.0, "c1"], [2.5, "c2"]]
    # a single-column first side: the renamed row holds its cell as __value__ too
    vals = eng._eval_expr("VALUES(TS[G])", ctx)
    sides = eng._union_named([vals, eng._eval_expr("VALUES(TD[D])", ctx)], ctx)
    assert sides[1][0] == {"__table__": "", "G": 0.5, "__column__": "G", "__value__": 0.5}
    # a row constructor's rows keep a __tuple__
    sides = eng._union_named([eng._eval_expr('{(1, "a")}', ctx),
                              eng._eval_expr('ROW("x", 3, "y", "b")', ctx)], ctx)
    assert sides[1][0]["__tuple__"] == (3, "b") and sides[1][0]["Value1"] == 3
