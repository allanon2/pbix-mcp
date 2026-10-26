"""#326: a UNION or table-constructor column that holds Int64
and Double values is a Double column.

A DAX table's column has one type. UNION kept each row's values as they
were, and so did a table constructor, so a column of 2**53 + 1 (Int64) and
2**53.0 (Double) held two different numbers, where Desktop makes the column
Double and the Int64 values become their Doubles -- one value. The type is
static: an empty Double side still makes the column Double, and IN's
constructor {2**53, 5.0} is one Double column too. The engine now unifies
such columns (_unify_number_columns, _unify_number_values) and an empty
SELECTCOLUMNS of model columns carries its columns' types to UNION
(_StaticRows). A column of Int64 values alone stays exact. An Int64 column
beside a Currency one is Currency, and an empty ADDCOLUMNS over an empty
SELECTCOLUMNS keeps the static types (build_b257c.py u_int_cur,
u_addcols_empty).

Expected values: Power BI Desktop 2.152 over ADOMD, batteries
F:/_desktop_oracle/build_b257.py (F:/_tmp_pbix/open1/b252) and
build_b257b.py (F:/_tmp_pbix/open2/b252-order53), by probe id. TK[K] =
2**53 - 1 .. 2**53 + 4 labelled a .. f, TC[K] = 2**53, 2**53 + 1 (Int64),
TD[D] = 2**53.0 (x), (2**53 + 4).0 (y) (Double).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

P = 2 ** 53
K6 = [["a", P - 1], ["b", P], ["c", P + 1], ["d", P + 2], ["e", P + 3], ["f", P + 4]]
TABLES = {
    "TK": {"columns": ["L", "K"], "rows": [list(r) for r in K6]},
    "TC": {"columns": ["L", "K", "A"], "rows": [["b", P, 1], ["c", P + 1, 2]]},
    "TD": {"columns": ["M", "D"], "rows": [["x", float(P)], ["y", float(P + 4)]]},
}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


U = ('VAR U = UNION(SELECTCOLUMNS(FILTER(TK, TK[L] IN {"b", "c"}), "v", TK[K], "l", TK[L]), '
     'SELECTCOLUMNS(TD, "v", TD[D], "l", TD[M])) RETURN ')
UE = 'VAR U = UNION(SELECTCOLUMNS(TC, "v", TC[K]), SELECTCOLUMNS(FILTER(TD, FALSE()), "v", TD[D])) RETURN '
UE2 = ('VAR U = UNION(SELECTCOLUMNS(TC, "v", TC[K], "l", TC[L]), '
       'SELECTCOLUMNS(FILTER(TD, FALSE()), "v", TD[D], "l", TD[M])) RETURN ')

# (probe, expression, Power BI Desktop 2.152's answer)
DESKTOP = [
    # build_b257.py
    ('u_top2_desc', U + 'CONCATENATEX(TOPN(2, U, [v], DESC), [l], "", [l], ASC)', 'bcxy'),
    ('u_top1_asc', U + 'CONCATENATEX(TOPN(1, U, [v], ASC), [l], "", [l], ASC)', 'bcx'),
    ('u_distinct', U + 'COUNTROWS(DISTINCT(SELECTCOLUMNS(U, "v", [v])))', 2),
    ('u_cx_two_keys', U + 'CONCATENATEX(U, [l], "", [v], ASC, [l], DESC)', 'xcby'),
    ('k_max_id', 'MAXX({9007199254740993, 9007199254740992.0}, [Value]) - 9007199254740992', 0),
    ('k_distinct_id', 'COUNTROWS(DISTINCT({9007199254740993, 9007199254740992.0}))', 1),
    # Int64 values alone stay exact
    ('t1_ctor', 'CONCATENATEX(TOPN(1, {(9007199254740992, "b"), (9007199254740993, "c")}, [Value1], DESC), '
                '[Value2], "", [Value2], ASC)', 'c'),
    ('k_max_ii_up', 'MAXX({9007199254740992, 9007199254740993}, [Value]) - 9007199254740992', 1),
    ('k_max_ii_dn', 'MAXX({9007199254740993, 9007199254740992}, [Value]) - 9007199254740992', 1),
    ('k_distinct_ii', 'COUNTROWS(DISTINCT({9007199254740993, 9007199254740992}))', 2),
    # build_b257b.py group A: the type is static
    ('ub_empty_distinct', UE + 'COUNTROWS(DISTINCT(U))', 1),
    ('ub_empty_max', UE + 'MAXX(U, [v]) - 9007199254740992', 0),
    ('ub_empty_top', UE2 + 'CONCATENATEX(TOPN(1, U, [v], DESC), [l], "", [l], ASC)', 'bc'),
    ('in_mixed_ctor', 'IF(9007199254740993 IN {9007199254740992, 5.0}, 1, 0)', 1),
    ('c_in_mixed', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), TK[K] IN {9007199254740992, 5.0})', 'bc'),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


def test_unify_copies_rows_and_keeps_their_layout():
    """Exact arithmetic on the helper: only rows whose Int64 value changes
    are copied; the constructor's Value / __value__ and a row constructor's
    __tuple__ / Value<i> stay in step; model rows are never written."""
    rows = [{'__table__': '', '__column__': 'Value', '__value__': P + 1, 'Value': P + 1},
            {'__table__': '', '__column__': 'Value', '__value__': 2.5, 'Value': 2.5}]
    out = de._unify_number_columns(rows)
    assert out[0] is not rows[0] and out[1] is rows[1]
    assert out[0]['Value'] == out[0]['__value__'] == float(P) and type(out[0]['Value']) is float
    assert rows[0]['Value'] == P + 1                              # the input is untouched
    tup = [{'__table__': '', '__tuple__': (P + 1, "c"), 'Value1': P + 1, 'Value2': "c"},
           {'__table__': '', '__tuple__': (0.5, "d"), 'Value1': 0.5, 'Value2': "d"}]
    out = de._unify_number_columns(tup)
    assert out[0]['__tuple__'] == (float(P), "c") and out[0]['Value1'] == float(P)
    ints = [{'__table__': '', '__column__': 'Value', '__value__': P + 1, 'Value': P + 1},
            {'__table__': '', '__column__': 'Value', '__value__': P, 'Value': P}]
    assert de._unify_number_columns(ints) is ints
    assert de._unify_number_values([P + 1, 5.0]) == [float(P), 5.0]
    assert de._unify_number_values([P + 1, P]) == [P + 1, P]


def test_an_empty_selectcolumns_carries_its_types_to_union():
    """An empty SELECTCOLUMNS of a Double column is a _StaticRows that says
    so; of a text column a plain empty list."""
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {})
    got = eng._eval_expr('SELECTCOLUMNS(FILTER(TD, FALSE()), "v", TD[D], "l", TD[M])', ctx)
    assert got == [] and isinstance(got, de._StaticRows) and got.num_kinds == (float, None)
    got = eng._eval_expr('SELECTCOLUMNS(FILTER(TD, FALSE()), "l", TD[M])', ctx)
    assert got == [] and not isinstance(got, de._StaticRows)


def test_an_empty_currency_side_is_no_double_side():
    """A Fixed Decimal (Currency) column loads as floats, like a Double one;
    only the table's currency_columns tell it apart. Read as a Double, an
    empty SELECTCOLUMNS of it turned a UNION's Int64 values into Doubles.
    Its cells' number kind is unknown (None), and the empty side says
    Currency: an Int64 column beside a Currency one is Currency
    (build_b257c.py u_int_cur), whose counts 2**53 * 10,000 wrap as
    CONVERT(<Int64>, CURRENCY) wraps (build_b254b.py g_ovf) and stay two
    values: COUNTROWS(DISTINCT(...)) over 2**53 and 2**53 + 1 is 2 (exact
    arithmetic)."""
    tables = {"TC": {"columns": ["K"], "rows": [[P], [P + 1]]},
              "TCur": {"columns": ["v"], "rows": [[0.1], [0.2]]}}
    de.mark_currency_columns(tables, {"TCur": ["v"]})
    ctx = de.DAXContext(tables, {})
    assert ctx._number_kind(tables["TCur"], 0) is None
    got = de.evaluate_measure("m", tables, {"m": (
        'COUNTROWS(DISTINCT(UNION(SELECTCOLUMNS(TC, "v", TC[K]), '
        'SELECTCOLUMNS(FILTER(TCur, FALSE()), "v", TCur[v]))))')})
    assert got == 2


# build_b257c.py group U (F:/_tmp_pbix/rel148/desk/desk_b257c.json), on its
# own tables: TK[K] = 2**53 - 1 .. 2**53 + 2, TS[G] = 1, 1, 2, 3, the Currency
# column TC[v] = 0.0001, 0.0002, 0.0003 and the Double column TD[D] = 0.5, 1.5
B257C = {
    "TK": {"columns": ["L", "K"], "rows": [["a", P - 1], ["b", P], ["c", P + 1], ["d", P + 2]]},
    "TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"], [3, "s"]]},
    "TC": {"columns": ["v", "L"], "rows": [[0.0001, "u1"], [0.0002, "u2"], [0.0003, "u3"]],
           "currency_columns": frozenset({"v"})},
    "TD": {"columns": ["M", "D"], "rows": [["x", 0.5], ["y", 1.5]]},
}


def _typ(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISCURRENCY({x}), "C", IF(ISDOUBLE({x}), "D", '
            f'IF(ISINT64({x}), "I", "?"))))')


@pytest.mark.parametrize("probe,expr,want", [
    # an empty ADDCOLUMNS side keeps its columns' static types: v is Double,
    # so 2**53 and 2**53 + 1 are one value (we answered 4)
    ('u_addcols_empty', 'VAR U = UNION(SELECTCOLUMNS(TK, "v", TK[K], "w", 1), '
                        'ADDCOLUMNS(SELECTCOLUMNS(FILTER(TD, FALSE()), "v", TD[D]), "w", 1)) '
                        'RETURN COUNTROWS(DISTINCT(SELECTCOLUMNS(U, "v", [v])))', 3),
    # an Int64 column beside a Currency one is Currency (we answered I)
    ('u_int_cur', _typ('MAXX(UNION(SELECTCOLUMNS(TS, "v", TS[G]), SELECTCOLUMNS(TC, "v", TC[v])), [v])'),
     'C'),
])
def test_b257c_matches_desktop(probe, expr, want):
    assert de.evaluate_measure("m", B257C, {"m": expr}) == want


def test_static_types_of_empty_sides():
    """An empty ADDCOLUMNS over a _StaticRows adds its columns' static
    types; an empty SELECTCOLUMNS of a Currency column says _Currency; a
    UNION of empty sides is a _StaticRows itself (exact on the rules
    above)."""
    eng = de.DAXEngine()
    ctx = de.DAXContext(B257C, {})
    got = eng._eval_expr('ADDCOLUMNS(SELECTCOLUMNS(FILTER(TD, FALSE()), "v", TD[D]), "w", 1, "c", '
                         'CURRENCY(1), "s", "x")', ctx)
    assert got == [] and isinstance(got, de._StaticRows)
    assert got.num_kinds == (float, int, de._Currency, None)
    got = eng._eval_expr('SELECTCOLUMNS(FILTER(TC, FALSE()), "v", TC[v])', ctx)
    assert isinstance(got, de._StaticRows) and got.num_kinds == (de._Currency,)
    got = eng._eval_expr('UNION(SELECTCOLUMNS(FILTER(TC, FALSE()), "v", TC[v]), '
                         'SELECTCOLUMNS(FILTER(TS, FALSE()), "v", TS[G]))', ctx)
    assert isinstance(got, de._StaticRows) and got.num_kinds == (de._Currency,)


def test_union_column_type():
    """Exact on the rules: Double beside Int64 or Currency is Double,
    Currency beside Int64 Currency, one type alone no change."""
    C = de._Currency
    assert de._union_column_type({int, float}, None) is float
    assert de._union_column_type({int, C}, None) is C
    assert de._union_column_type({C}, int) is C
    assert de._union_column_type({C, float}, None) is float
    assert de._union_column_type({int}, None) is None
    assert de._union_column_type({C}, None) is None
    rows = [{'__table__': '', '__column__': 'Value', '__value__': 3, 'Value': 3},
            {'__table__': '', '__column__': 'Value', '__value__': de._cur_make(1), 'Value': de._cur_make(1)}]
    out = de._unify_number_columns(rows)
    assert type(out[0]['Value']) is C and out[0]['Value'] == 3 and type(out[0]['__value__']) is C
    assert out[1] is rows[1]


# build_b257d.py group U (F:/_tmp_pbix/rel148/desk/desk_b257d.json), on its
# own tables: TS[G] = 0, 1, 1, 2, 3 (Int64), the Currency columns TC[v] =
# 0.0001, 0.0002, 0.0003 and TCU[v] = 1, 2.5, the Double column TD[D] = 0.5,
# 1.5, 1.0
B257D = {
    "TS": {"columns": ["G", "L"], "rows": [[0, "z"], [1, "p"], [1, "q"], [2, "r"], [3, "s"]]},
    "TC": {"columns": ["v", "L"], "rows": [[0.0001, "u1"], [0.0002, "u2"], [0.0003, "u3"]],
           "currency_columns": frozenset({"v"})},
    "TD": {"columns": ["M", "D"], "rows": [["x", 0.5], ["y", 1.5], ["e", 1.0]]},
    "TCU": {"columns": ["v", "L"], "rows": [[1.0, "c1"], [2.5, "c2"]], "currency_columns": frozenset({"v"})},
}
UCD = 'UNION(SELECTCOLUMNS(TC, "v", TC[v]), SELECTCOLUMNS(TD, "v", TD[D]))'


@pytest.mark.parametrize("probe,expr,want", [
    ('u_cur_dbl', _typ(f'MAXX({UCD}, [v])'), 'D'),
    # the smallest value is a row of TC, which read [v] as TC[v], a Currency
    # (we answered C)
    ('u_cur_dbl_min', _typ(f'MINX({UCD}, [v])'), 'D'),
    ('u_ctor_int_cur', _typ('MINX({1, CURRENCY(2)}, [Value])'), 'C'),
    # a model table's Currency column beside an Int64 one is Currency, also
    # when its rows are the table's own (we answered D)
    ('u_raw_int_cur', _typ('MINX(UNION(TS, TCU), [G])'), 'C'),
])
def test_b257d_matches_desktop(probe, expr, want):
    assert de.evaluate_measure("m", B257D, {"m": expr}) == want


def test_a_currency_made_double_is_marked_and_rows_are_copied():
    """Exact on the rules: a Currency cell turned Double is named in the
    row's __dbl__ and reads as a Double by name (_cur_lineage); the model's
    rows are never written."""
    ctx = de.DAXContext(B257D, {})
    eng = de.DAXEngine()
    rows = eng._eval_expr(UCD, ctx)
    assert [type(r["v"]) for r in rows] == [float] * 6
    assert all(r.get("__dbl__") == frozenset({"v"}) for r in rows[:3])
    assert all("__dbl__" not in r for r in rows[3:])
    assert not de._cur_lineage(rows[0], "v") and de._cur_lineage(rows[3], "v")
    raw = B257D["TCU"]["rows"]
    got = eng._eval_expr('UNION(TS, TCU)', ctx)
    assert [type(r["G"]) for r in got[:5]] == [de._Currency] * 5
    # TCU's rows are named as TS's columns, by position (#338)
    assert [type(r["G"]) for r in got[5:]] == [de._Currency] * 2
    assert raw == [[1.0, "c1"], [2.5, "c2"]] and type(raw[0][0]) is float
    # beside a Double column the raw Currency side is Double, and stays so by name
    got = eng._eval_expr('UNION(TCU, SELECTCOLUMNS(TD, "v", TD[D]))', ctx)
    assert [type(r["v"]) for r in got] == [float] * 5
    assert de.evaluate_measure("m", B257D, {"m": _typ('MINX(UNION(TCU, SELECTCOLUMNS(TD, "v", TD[D])), [v])')}) == 'D'
