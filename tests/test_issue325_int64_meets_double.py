"""#325: an Int64 that meets a Double compares as a Double.

Issue #240 (0.9.147) made every comparison of two numbers exact: Python
compares an int and a float by their exact values, so 9007199254740993 was
not 9007199254740992.0. Power BI Desktop compares two Int64 values exactly
(#240 stands there), but converts an Int64 that meets a Double to the
nearest Double and compares two Doubles -- in scalar comparisons (=, ==,
<>, <, >, IN), FILTER row by row, CALCULATE / KEEPFILTERS / NOT IN column
filters, a column-to-column comparison, LOOKUPVALUE, CONTAINS,
CONTAINSROW, SWITCH, ISAFTER / ISONORAFTER, EXCEPT / INTERSECT, MAX(a, b)
and the value RANKX / RANK.EQ rank. The engine now does the same
(_num_meet in _compare and _eval_comparison; second keys 'i2d' / 'dbl' in
_value_keys / _filter_value_keys for the column filters).

Expected values: Power BI Desktop 2.152 over ADOMD, batteries
F:/_desktop_oracle/build_b257.py (F:/_tmp_pbix/open1/b252),
build_b257b.py (F:/_tmp_pbix/open2/b252-order53) and build_b254b.py
c_coalesce (F:/_tmp_pbix/open2/b248-currency), by probe id. TK[K] =
2**53 - 1 .. 2**53 + 4 labelled a .. f (Int64); TD[D] = the Doubles 2**53
(x) and 2**53 + 4 (y); TC[K] = 2**53, 2**53 + 1. The key-helper assertions
are exact arithmetic.
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
    "TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"]]},
}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


# (probe, expression, Power BI Desktop 2.152's answer)
DESKTOP = [
    # build_b257.py: literals (9007199254740992.0 is a Double literal)
    ('p_eq_id', 'IF(9007199254740993 = 9007199254740992.0, 1, 0)', 1),
    ('p_gt_id', 'IF(9007199254740993 > 9007199254740992.0, 1, 0)', 0),
    ('p_lt_di', 'IF(9007199254740992.0 < 9007199254740993, 1, 0)', 0),
    ('p_in_id', 'IF(9007199254740993 IN {9007199254740992.0}, 1, 0)', 1),
    ('p_sub_di', '9007199254740993.0 - 9007199254740992', 0),
    ('p_eq_dd', 'IF(9007199254740993.0 = 9007199254740992.0, 1, 0)', 1),
    # FILTER row by row, CALCULATE column filters
    ('f_eq_id', 'CONCATENATEX(FILTER(TK, TK[K] = 9007199254740992.0), TK[L], "", TK[L], ASC)', 'bc'),
    ('f_gt_id', 'CONCATENATEX(FILTER(TK, TK[K] > 9007199254740992.0), TK[L], "", TK[L], ASC)', 'def'),
    ('f_eq_id3', 'CONCATENATEX(FILTER(TK, TK[K] = 9007199254740993.0), TK[L], "", TK[L], ASC)', 'bc'),
    ('c_eq_id', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), TK[K] = 9007199254740992.0)', 'bc'),
    ('c_eq_id3', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), TK[K] = 9007199254740993.0)', 'bc'),
    ('c_in_id', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), '
                'TK[K] IN {9007199254740992.0, 9007199254740996.0})', 'bcef'),
    # a column against a column, over CROSSJOIN
    ('f_x_eq', 'CONCATENATEX(FILTER(CROSSJOIN(TK, TD), TK[K] = TD[D]), TK[L] & TD[M], ",", TK[L] & TD[M], ASC)',
     'bx,cx,ey,fy'),
    ('f_x_gt', 'CONCATENATEX(FILTER(CROSSJOIN(TK, TD), TK[K] > TD[D]), TK[L] & TD[M], ",", TK[L] & TD[M], ASC)',
     'dx,ex,fx'),
    ('f_x_lt', 'CONCATENATEX(FILTER(CROSSJOIN(TK, TD), TK[K] < TD[D]), TK[L] & TD[M], ",", TK[L] & TD[M], ASC)',
     'ax,ay,by,cy,dy'),
    ('l_lookup_di', 'IF(ISBLANK(LOOKUPVALUE(TD[M], TD[D], 9007199254740993)), "-", '
                    'LOOKUPVALUE(TD[M], TD[D], 9007199254740993))', 'x'),
    ('m_pair_id', 'MAX(9007199254740993, 9007199254740992.0) - 9007199254740992', 0),
    ('r_val_id', 'RANKX(ALL(TK), TK[K], 9007199254740992.0)', 4),
    ('r_rankeq_id', 'RANK.EQ(9007199254740992.0, TK[K])', 4),
    # build_b257b.py group C: storage-engine filters
    ('c_gt_id', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), TK[K] > 9007199254740992.0)', 'def'),
    ('c_le_id', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), TK[K] <= 9007199254740992.0)', 'abc'),
    ('c_dcol_eq_i', 'VAR s = CALCULATE(CONCATENATEX(TD, TD[M], "", TD[M], ASC), TD[D] = 9007199254740993) '
                    'RETURN IF(ISBLANK(s), "-", s)', 'x'),
    ('c_dcol_in_i', 'VAR s = CALCULATE(CONCATENATEX(TD, TD[M], "", TD[M], ASC), '
                    'TD[D] IN {9007199254740993, 9007199254740995}) RETURN IF(ISBLANK(s), "-", s)', 'xy'),
    ('c_dcol_ge_i', 'VAR s = CALCULATE(CONCATENATEX(TD, TD[M], "", TD[M], ASC), TD[D] >= 9007199254740993) '
                    'RETURN IF(ISBLANK(s), "-", s)', 'xy'),
    ('kf_eq_id', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), KEEPFILTERS(TK[K] = 9007199254740992.0))',
     'bc'),
    ('c_not_in_id', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), NOT TK[K] IN {9007199254740992.0})',
     'adef'),
    # build_b257b.py group F: other comparisons
    ('sw_id', 'SWITCH(9007199254740993, 9007199254740992.0, "eq", "ne")', 'eq'),
    ('isafter_id', 'IF(ISAFTER(9007199254740993, 9007199254740992.0, ASC), 1, 0)', 0),
    ('isonorafter_di', 'IF(ISONORAFTER(9007199254740992.0, 9007199254740993, ASC), 1, 0)', 1),
    ('contains_di', 'IF(CONTAINS(TD, TD[D], 9007199254740993), 1, 0)', 1),
    ('containsrow_di', 'IF(CONTAINSROW(VALUES(TD[D]), 9007199254740993), 1, 0)', 1),
    ('except_id', 'COUNTROWS(EXCEPT(VALUES(TC[K]), VALUES(TD[D]))) + 0', 0),
    ('intersect_id', 'COUNTROWS(INTERSECT(VALUES(TC[K]), VALUES(TD[D]))) + 0', 2),
    ('except_di2', 'COUNTROWS(EXCEPT(VALUES(TD[D]), SELECTCOLUMNS(FILTER(TC, TC[L] = "c"), "k", TC[K]))) + 0', 1),
    ('rankx_dbl_asc', 'RANKX(ALL(TD), TD[D], 9007199254740993, ASC)', 1),
    ('rankeq_dbl', 'VAR r = RANK.EQ(9007199254740993, TD[D], 1) RETURN IF(ISBLANK(r), "-", r)', 1),
    ('rankeq_missing', 'VAR r = RANK.EQ(3, TS[G]) RETURN IF(ISBLANK(r), "-", r)', '-'),
    ('eq_strict_id', 'IF(9007199254740993 == 9007199254740992.0, 1, 0)', 1),
    ('ne_id', 'IF(9007199254740993 <> 9007199254740992.0, 1, 0)', 0),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


@pytest.mark.parametrize("probe,expr,want", [
    # build_b254b.py c_coalesce cells 4, 5 (F:/_tmp_pbix/open2/b248-currency): D1, D2
    ("c_coalesce_4", "MIN(1, 2.5)", 1.0),
    ("c_coalesce_5", "MAX(2, 0.5)", 2.0),
])
def test_two_argument_min_max_of_int64_and_double_is_double(probe, expr, want):
    got = _ev(expr)
    assert got == want and type(got) is float


def test_two_int64_values_still_compare_exactly():
    """Issue #240 stands between two Int64 values (Desktop, build_b257.py
    p_eq_ii 0, f_gt_ii cdef, l_lookup_ii c; build_b234.py i_pred 2), also
    for two that share a Double: 2**54 + 1 and 2**54 + 2 are both the
    Double 2**54 (exact arithmetic), and each Int64 filter keeps its own."""
    assert _ev("IF(9007199254740993 = 9007199254740992, 1, 0)") == 0
    assert _ev('CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), TK[K] = 9007199254740993)') == 'c'
    assert _ev('CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), TK[K] IN {9007199254740993})') == 'c'
    assert _ev("COUNTROWS(EXCEPT(VALUES(TC[K]), {9007199254740992}))") == 1
    q = 2 ** 54
    assert float(q + 1) == float(q + 2) == float(q)
    tables = {"T4": {"columns": ["L", "K"], "rows": [["p", q + 1], ["q", q + 2], ["r", q]]}}

    def ev4(expr):
        return de.evaluate_measure("m", tables, {"m": expr})
    assert ev4(f'CALCULATE(CONCATENATEX(T4, T4[L], "", T4[L], ASC), T4[K] = {q + 1})') == 'p'
    assert ev4(f'CALCULATE(CONCATENATEX(T4, T4[L], "", T4[L], ASC), T4[K] IN {{{q + 2}}})') == 'q'
    assert ev4(f'LOOKUPVALUE(T4[L], T4[K], {q + 2})') == 'q'
    # and the Double 2**54.0 meets all three as Doubles
    assert ev4(f'CALCULATE(CONCATENATEX(T4, T4[L], "", T4[L], ASC), T4[K] = {q}.0)') == 'pqr'


def test_key_helpers():
    """Exact arithmetic: an Int64 cell past 2**53 also answers to its Double
    ('i2d'), which only a Double filter value asks for; a whole Double cell
    past 2**53 also answers to ('dbl', itself), which only an Int64 value
    asks for; the first key stays the exact value."""
    b = P
    assert ("i2d", float(b)) in de._value_keys(b + 1)
    assert de._value_keys(b + 2) == (("num", b + 2),)          # its Double is itself
    assert de._value_keys(b + 1)[0] == ("num", b + 1)
    assert ("dbl", float(b)) in de._value_keys(float(b))
    assert ("dbl", float(b)) in de._filter_value_keys(b + 1)
    assert ("i2d", float(b)) in de._filter_value_keys(float(b))
    assert ("num", b) not in de._filter_value_keys(b + 1)
    assert ("i2d", float(b)) not in de._filter_value_keys(b + 1)
    assert de._value_keys(2) == de._value_keys(2.0) == (("num", 2),)
    assert de._num_meet(b + 1, float(b)) == (float(b), float(b))
    assert de._num_meet(b + 1, b) == (b + 1, b)
    assert de._num_meet(True, 1.5) == (True, 1.5)


_X = 'IF(TC[L] = "c", TC[K], TC[K] + 0.0)'


def test_order_keys_that_mix_int64_and_double_meet_as_doubles():
    """Power BI Desktop 2.152, build_b257b.py if_unify_top: TOPN(1, TC,
    IF(TC[L] = "c", TC[K], TC[K] + 0.0), DESC) is b and c -- the IF is a
    Double expression, so 2**53 + 1 is the Double 2**53 and ties with b's
    2**53.0. The engine's IF keeps each branch's type, and the exact order
    of #252 returned c alone (0.9.147 matched only by its float keys); the
    order keys now meet as RANKX's do."""
    assert _ev(f'CONCATENATEX(TOPN(1, TC, {_X}, DESC), TC[L], "", TC[L], ASC)') == "bc"


@pytest.mark.parametrize("expr,want", [
    # RANKX already tied them; TOPN, CONCATENATEX and TOPNSKIP agree now
    (f'CONCATENATEX(TC, TC[L] & RANKX(TC, {_X}), ",", TC[L], ASC)', "b1,c1"),
    (f'CONCATENATEX(TC, TC[L], "", {_X}, ASC, TC[L], DESC)', "cb"),
    (f'CONCATENATEX(TOPNSKIP(2, 0, TC, {_X}, DESC, TC[L], DESC), TC[L], "")', "cb"),
])
def test_every_order_ties_the_met_keys(expr, want):
    """Exact arithmetic on if_unify_top's rule: tied on the first key, the
    second orders them."""
    assert _ev(expr) == want


def test_int64_keys_alone_stay_exact():
    """build_b257.py t1_desc: TOPN(1, TK, TK[K], DESC) is f alone."""
    assert _ev('CONCATENATEX(TOPN(1, TK, TK[K], DESC), TK[L], "")') == "f"


def test_meet_key_column_helper():
    """Exact arithmetic: only an Int64 past 2**53 among Doubles changes; a
    BLANK or text key (whose number slot is 0.0) is no Double."""
    k = de._order_key
    col = [k(P + 1), k(float(P)), k(None)]
    met = de._meet_key_column(col)
    assert met[0] == k(float(P)) and met[1] == col[1] and met[2] == col[2]
    ints = [k(P + 1), k(P), k(None), k("x")]
    assert de._meet_key_column(ints) is ints
    small = [k(3), k(2.5)]
    assert de._meet_key_column(small) is small
