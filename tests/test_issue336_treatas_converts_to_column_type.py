"""#336: TREATAS converts its values to the type of the column it
puts them on.

A comparison converts no value to the other side's column type (an Int64
meeting a Double compares as a Double, #325; a Currency
compared with a Double compares as Doubles), but TREATAS does: onto an
Int64 column a Double rounds half away from zero, onto a Double column an
Int64 becomes its Double, onto a Currency column a number rounds to 4
decimals half away from zero -- from the Double product x * 10000 -- and a
text is its decimal number. The engine handed the values over as they were,
so TREATAS({0.10001}, TCur3[v]) found no 0.1 row and TREATAS({1.6}, TS[G])
no 2. Onto an Int64 column a text is its number rounded half to even, as a
text becomes a Currency, and TRUE is 1 (build_b257c.py t_text_int,
t_true_int). TRUE is 1 on a Double and a Currency column too, and a text
that is no number fails onto an Int64 column (build_b257d.py t_true_dbl,
t_true_cur, t_text_bad).

Expected values: Power BI Desktop 2.152 over ADOMD, by probe id --
F:/_desktop_oracle/build_b257.py (F:/_tmp_pbix/open1/b252), build_b257b.py
group D (F:/_tmp_pbix/open2/b252-order53), build_b254.py k_filter cell 5
(F:/_tmp_pbix/open1/b248) and build_b254b.py d_treatas / d_treatas_text
(F:/_tmp_pbix/open2/b248-currency). TK[K] = 2**53 - 1 .. 2**53 + 4 labelled
a .. f; TD[D] = 2**53.0 (x), (2**53 + 4).0 (y); TS = (1, p), (1, q), (2, r);
TCur3[v] = 0.1, 0.1, 0.2 and TKC[v] = 0.0312, 0.0313, 0.0999, 0.1, 0.1001
(ids 1 .. 5) are Currency columns, TKC[k] = 2, 3, 0, 0, 0 an Int64 one.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

P = 2 ** 53
K6 = [["a", P - 1], ["b", P], ["c", P + 1], ["d", P + 2], ["e", P + 3], ["f", P + 4]]
TABLES = {
    "TK": {"columns": ["L", "K"], "rows": [list(r) for r in K6]},
    "TD": {"columns": ["M", "D"], "rows": [["x", float(P)], ["y", float(P + 4)]]},
    "TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"]]},
    "TCur3": {"columns": ["v"], "rows": [[0.1], [0.1], [0.2]], "currency_columns": frozenset({"v"})},
    "TKC": {"columns": ["id", "v", "k"],
            "rows": [[1, 0.0312, 2], [2, 0.0313, 3], [3, 0.0999, 0], [4, 0.1, 0], [5, 0.1001, 0]],
            "currency_columns": frozenset({"v"})},
}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


def _dash(expr):
    return f'VAR s = {expr} RETURN IF(ISBLANK(s), "-", s)'


TKL = 'CONCATENATEX(TK, TK[L], "", TK[L], ASC)'
TSL = 'CONCATENATEX(TS, TS[L], "", TS[L], ASC)'
TDL = 'CONCATENATEX(TD, TD[M], "", TD[M], ASC)'

DESKTOP = [
    # build_b257.py
    ('c_treatas_id', f'CALCULATE({TKL}, TREATAS({{9007199254740992.0}}, TK[K]))', 'b'),
    ('c_treatas_col', f'CALCULATE({TKL}, TREATAS(VALUES(TD[D]), TK[K]))', 'bf'),
    # build_b257b.py group D
    ('tr_16', _dash(f'CALCULATE({TSL}, TREATAS({{1.6}}, TS[G]))'), 'r'),
    ('tr_25', _dash(f'CALCULATE({TSL}, TREATAS({{2.5}}, TS[G]))'), '-'),
    ('tr_14', _dash(f'CALCULATE({TSL}, TREATAS({{1.4}}, TS[G]))'), 'pq'),
    ('tr_two_cols', _dash(f'CALCULATE({TKL}, TREATAS({{(9007199254740992.0, "c")}}, TK[K], TK[L]))'), '-'),
    ('tr_int_on_dbl', _dash(f'CALCULATE({TDL}, TREATAS({{9007199254740993}}, TD[D]))'), 'x'),
    # build_b254.py k_filter cell 5
    ('k_filter_5', 'CALCULATE(COUNTROWS(TCur3), TREATAS({0.10001}, TCur3[v])) + 0', 2),
    # build_b254b.py d_treatas (2 4 5 2 4 4 4), d_treatas_text (4)
    ('d_treatas_0', 'CALCULATE(SUM(TKC[id]), TREATAS({0.03125}, TKC[v])) + 0', 2),
    ('d_treatas_1', 'CALCULATE(SUM(TKC[id]), TREATAS({0.09996}, TKC[v])) + 0', 4),
    ('d_treatas_2', 'CALCULATE(SUM(TKC[id]), TREATAS({0.10005}, TKC[v])) + 0', 5),
    ('d_treatas_3', 'CALCULATE(SUM(TKC[id]), TREATAS({2.5}, TKC[k])) + 0', 2),
    ('d_treatas_4', 'CALCULATE(SUM(TKC[id]), TREATAS({0.1}, TKC[v])) + 0', 4),
    ('d_treatas_5', 'CALCULATE(SUM(TKC[id]), TREATAS({CURRENCY(0.1)}, TKC[v])) + 0', 4),
    ('d_treatas_6', 'CALCULATE(SUM(TKC[id]), TREATAS({0.09995}, TKC[v])) + 0', 4),
    ('d_treatas_text', 'CALCULATE(SUM(TKC[id]), TREATAS({"0.1"}, TKC[v])) + 0', 4),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


# build_b257c.py group T (F:/_tmp_pbix/rel148/desk/desk_b257c.json): its own
# tables, TS[G] = 1, 1, 2, 3 labelled p, q, r, s and the Currency column
# TC[v] = 0.0001, 0.0002, 0.0003 labelled u1, u2, u3
B257C = {
    "TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"], [3, "s"]]},
    "TC": {"columns": ["v", "L"], "rows": [[0.0001, "u1"], [0.0002, "u2"], [0.0003, "u3"]],
           "currency_columns": frozenset({"v"})},
}
TCL = 'CONCATENATEX(TC, TC[L], "", TC[L], ASC)'


@pytest.mark.parametrize("probe,expr,want", [
    ('t_text_cur_25', _dash(f'CALCULATE({TCL}, TREATAS({{"0.00025"}}, TC[v]))'), 'u2'),
    ('t_text_cur_15', _dash(f'CALCULATE({TCL}, TREATAS({{"0.00015"}}, TC[v]))'), 'u2'),
    ('t_text_int', _dash(f'CALCULATE({TSL}, TREATAS({{"2.5"}}, TS[G]))'), 'r'),
    ('t_true_int', _dash(f'CALCULATE({TSL}, TREATAS({{TRUE()}}, TS[G]))'), 'pq'),
])
def test_b257c_matches_desktop(probe, expr, want):
    """Text onto an Int64 or a Currency column is its number, rounded half
    to even (t_text_int: "2.5" keeps G = 2, where the Double 2.5 keeps 3,
    tr_25); TRUE onto an Int64 column is 1. We kept nothing for the Int64
    ones."""
    assert de.evaluate_measure("m", B257C, {"m": expr}) == want


# build_b257d.py group T (F:/_tmp_pbix/rel148/desk/desk_b257d.json): its own
# tables, TK[K] = 2**53 - 1 .. 2**53 + 2 labelled a .. d, TS[G] = 0, 1, 1, 2, 3
# labelled z, p, q, r, s, the Double column TD[D] = 0.5, 1.5, 1.0 labelled x,
# y, e and the Currency column TCU[v] = 1, 2.5 labelled c1, c2
B257D = {
    "TK": {"columns": ["L", "K"], "rows": [["a", P - 1], ["b", P], ["c", P + 1], ["d", P + 2]]},
    "TS": {"columns": ["G", "L"], "rows": [[0, "z"], [1, "p"], [1, "q"], [2, "r"], [3, "s"]]},
    "TD": {"columns": ["M", "D"], "rows": [["x", 0.5], ["y", 1.5], ["e", 1.0]]},
    "TCU": {"columns": ["v", "L"], "rows": [[1.0, "c1"], [2.5, "c2"]], "currency_columns": frozenset({"v"})},
}
TDL3 = 'CONCATENATEX(TD, TD[M], "", TD[M], ASC)'
TCUL = 'CONCATENATEX(TCU, TCU[L], "", TCU[L], ASC)'


@pytest.mark.parametrize("probe,expr,want", [
    ('t_text_int_15', _dash(f'CALCULATE({TSL}, TREATAS({{"1.5"}}, TS[G]))'), 'r'),
    ('t_text_int_27', _dash(f'CALCULATE({TSL}, TREATAS({{"2.7"}}, TS[G]))'), 's'),
    ('t_text_big', _dash(f'CALCULATE({TKL}, TREATAS({{"9007199254740993"}}, TK[K]))'), 'c'),
    ('t_false_int', _dash(f'CALCULATE({TSL}, TREATAS({{FALSE()}}, TS[G]))'), 'z'),
    ('t_text_dbl', _dash(f'CALCULATE({TDL3}, TREATAS({{"0.5"}}, TD[D]))'), 'x'),
    # TRUE is 1 on a Double and a Currency column too (we kept nothing)
    ('t_true_dbl', _dash(f'CALCULATE({TDL3}, TREATAS({{TRUE()}}, TD[D]))'), 'e'),
    ('t_true_cur', _dash(f'CALCULATE({TCUL}, TREATAS({{TRUE()}}, TCU[v]))'), 'c1'),
])
def test_b257d_matches_desktop(probe, expr, want):
    assert de.evaluate_measure("m", B257D, {"m": expr}) == want


def test_b257d_text_that_is_no_number_fails_onto_an_int64():
    """t_text_bad: Desktop fails the query, "Kan ikke konvertere verdien abc
    av typen Text til typen Integer ..." (Cannot convert value 'abc' of type
    Text to type Integer); we kept no row and answered "-"."""
    eng = de.DAXEngine()
    expr = _dash(f'CALCULATE({TSL}, TREATAS({{"abc"}}, TS[G]))')
    assert eng.evaluate_measure("m", de.DAXContext(B257D, {"m": expr})) is None
    assert "Cannot convert value 'abc' of type Text to type Integer." in (eng.eval_errors.get("m") or "")


@pytest.mark.parametrize("probe,expr,want", [
    # build_b254.py k_filter cells 0, 4: a comparison converts nothing
    ('k_filter_0', 'CALCULATE(COUNTROWS(TCur3), TCur3[v] = 0.10001) + 0', 0),
    ('k_filter_4', 'CALCULATE(COUNTROWS(TCur3), TCur3[v] IN {0.10001}) + 0', 0),
    # build_b254b.py d_lookup, d_setops: neither do LOOKUPVALUE, CONTAINS, IN, INTERSECT, EXCEPT
    ('d_lookup_0', 'LOOKUPVALUE(TKC[id], TKC[v], 0.09996) + 0', 0),
    ('d_lookup_1', 'IF(CONTAINS(TKC, TKC[v], 0.09996), 1, 0)', 0),
    ('d_lookup_2', 'LOOKUPVALUE(TKC[id], TKC[k], 2.5) + 0', 0),
    ('d_lookup_3', 'CALCULATE(SUM(TKC[id]), TKC[v] IN {0.09996}) + 0', 0),
    ('d_setops_0', 'COUNTROWS(INTERSECT(VALUES(TKC[v]), {0.09996})) + 0', 0),
    ('d_setops_1', 'COUNTROWS(EXCEPT(VALUES(TKC[v]), {0.09996})) + 0', 5),
])
def test_comparisons_do_not_convert(probe, expr, want):
    assert _ev(expr) == want


def test_converters():
    """Exact arithmetic on the converters: half away from zero, from the
    Double product for Currency (0.09995 * 10000.0 is 999.5 in Double)."""
    assert [de._treatas_int64(x) for x in (1.6, 1.4, 2.5, -2.5, float(P))] == [2, 1, 3, -3, P]
    # text half to even, as a text becomes a Currency; TRUE / FALSE 1 / 0
    # (build_b257c.py t_text_int, t_true_int)
    assert [de._treatas_int64(x) for x in ("2.5", "1.6", " 3 ", "-2.5", "9007199254740993")] == [
        2, 2, 3, -2, P + 1]
    assert de._treatas_int64(True) == 1 and type(de._treatas_int64(True)) is int
    assert de._treatas_int64(False) == 0
    # text that is no number fails (build_b257d.py t_text_bad)
    with pytest.raises(de._DAXEvaluationError, match="Cannot convert value 'abc' of type Text to type Integer"):
        de._treatas_int64("abc")
    # TRUE / FALSE onto Double and Currency columns (t_true_dbl, t_true_cur)
    assert de._treatas_double(True) == 1.0 and type(de._treatas_double(True)) is float
    assert de._treatas_double(False) == 0.0 and type(de._treatas_double(False)) is float
    assert de._treatas_double(P + 1) == float(P) and de._treatas_double("0.5") == "0.5"
    assert de._treatas_currency(True) == 1.0 and de._treatas_currency(False) == 0.0
    assert de._treatas_currency(0.09995) == 0.1 and 0.09995 * 10000.0 == 999.5
    assert de._treatas_currency(0.03125) == 0.0313
    assert de._treatas_currency(-0.03125) == -0.0313
    assert de._treatas_currency(2) == 2.0
    assert de._treatas_currency("0.1") == 0.1
    assert de._treatas_currency("abc") == "abc"
