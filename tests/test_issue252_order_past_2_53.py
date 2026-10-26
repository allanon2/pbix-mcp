"""Issue #252: TOPN, TOPNSKIP, SAMPLE, CONCATENATEX's order, RANKX, RANK.EQ and
FIRST/LASTNONBLANK(VALUE) order and rank Int64 values exactly, past 2**53 too.

float(9007199254740993) is 9007199254740992.0: past 2**53 two whole numbers
share one Double. The engine's sort key (_order_key), RANKX's keys
(_rankx_keys) and RANK.EQ's numbers went through float(), so 2**53 and
2**53 + 1 tied: TOPN(1, ..., DESC) over 2**53 - 1 .. 2**53 + 4 returned two
rows, RANKX shared ranks, SAMPLE and CONCATENATEX's order put the tied rows
in table order. Numbers now keep their exact values (Python orders an int
against an int, and an int against a float, exactly).

Expected values: Power BI Desktop 2.152 over ADOMD, battery
F:/_desktop_oracle/build_b257.py (pbix and Desktop answers under
F:/_tmp_pbix/open1/b252), by probe id. Tables: TK[K] = 2**53 - 1 .. 2**53 + 4
labelled a .. f, TKR the same rows reversed, TN their negatives, TC / TCR
2**53 and 2**53 + 1 in both insertion orders, TS small numbers. The
exactness rows (m_*, p_*, f_*, c_*, l_lookup_ii, k_*) already matched and
must keep matching. The window functions' share of the battery (RANK,
ROWNUMBER, INDEX, OFFSET, WINDOW) belongs to their own fix and is not here.
The unit assertions on _order_key are exact arithmetic.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

P = 2 ** 53
K6 = [["a", P - 1], ["b", P], ["c", P + 1], ["d", P + 2], ["e", P + 3], ["f", P + 4]]
TABLES = {
    "TK": {"columns": ["L", "K"], "rows": [list(r) for r in K6]},
    "TKR": {"columns": ["L", "K"], "rows": [list(r) for r in reversed(K6)]},
    "TN": {"columns": ["L", "K"], "rows": [[lab, -k] for lab, k in K6]},
    "TC": {"columns": ["L", "K", "A"], "rows": [["b", P, 1], ["c", P + 1, 2]]},
    "TCR": {"columns": ["L", "K", "A"], "rows": [["c", P + 1, 2], ["b", P, 1]]},
    "TD": {"columns": ["M", "D"], "rows": [["x", float(P)], ["y", float(P + 4)]]},
    "TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"]]},
}
MEASURES = {"mk": "MAX(TK[K])", "mkr": "MAX(TKR[K])"}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {**MEASURES, "m": expr})


# (probe, expression, Power BI Desktop 2.152's answer) -- build_b257.py
DESKTOP = [
    # TOPN returns the rows tied with the n-th: none, once the order is exact
    ('t1_desc', 'CONCATENATEX(TOPN(1, TK, TK[K], DESC), TK[L], "", TK[L], ASC)', 'f'),
    ('t1_desc_r', 'CONCATENATEX(TOPN(1, TKR, TKR[K], DESC), TKR[L], "", TKR[L], ASC)', 'f'),
    ('t2_asc', 'CONCATENATEX(TOPN(2, TK, TK[K], ASC), TK[L], "", TK[L], ASC)', 'ab'),
    ('t4_desc', 'CONCATENATEX(TOPN(4, TK, TK[K], DESC), TK[L], "", TK[L], ASC)', 'cdef'),
    ('t1_neg_asc', 'CONCATENATEX(TOPN(1, TN, TN[K], ASC), TN[L], "", TN[L], ASC)', 'f'),
    ('t2_neg_desc', 'CONCATENATEX(TOPN(2, TN, TN[K], DESC), TN[L], "", TN[L], ASC)', 'ab'),
    ('t1_tc', 'CONCATENATEX(TOPN(1, TC, TC[K], DESC), TC[L], "", TC[L], ASC)', 'c'),
    ('t1_tcr_asc', 'CONCATENATEX(TOPN(1, TCR, TCR[K], ASC), TCR[L], "", TCR[L], ASC)', 'b'),
    ('t1_int_expr', 'CONCATENATEX(TOPN(1, TK, TK[K] + 0, DESC), TK[L], "", TK[L], ASC)', 'f'),
    # Int64 + Double is a Double: 2**53 + 3 and 2**53 + 4 are one Double, a tie
    ('t1_dbl_expr', 'CONCATENATEX(TOPN(1, TK, TK[K] + 0.0, DESC), TK[L], "", TK[L], ASC)', 'ef'),
    ('t1_measure', 'CONCATENATEX(TOPN(1, TK, [mk], DESC), TK[L], "", TK[L], ASC)', 'f'),
    ('t1_two_keys', 'CONCATENATEX(TOPN(1, TK, TK[K], DESC, TK[L], ASC), TK[L], "", TK[L], ASC)', 'f'),
    ('t1_ctor', 'CONCATENATEX(TOPN(1, {(9007199254740992, "b"), (9007199254740993, "c")}, [Value1], DESC), '
                '[Value2], "", [Value2], ASC)', 'c'),
    # TOPNSKIP (skip 0), SAMPLE: first, last and evenly between
    ('k_skip1', 'CONCATENATEX(TOPNSKIP(1, 0, TK, TK[K], DESC, TK[L], ASC), TK[L], "", TK[L], ASC)', 'f'),
    ('k_skip_tc_desc', 'CONCATENATEX(TOPNSKIP(1, 0, TC, TC[K], DESC), TC[L], "", TC[L], ASC)', 'c'),
    ('k_skip_tcr_asc', 'CONCATENATEX(TOPNSKIP(1, 0, TCR, TCR[K], ASC), TCR[L], "", TCR[L], ASC)', 'b'),
    ('s2', 'CONCATENATEX(SAMPLE(2, TK, TK[K], DESC, TK[L], ASC), TK[L], "", TK[L], ASC)', 'af'),
    ('s3', 'CONCATENATEX(SAMPLE(3, TK, TK[K], ASC, TK[L], DESC), TK[L], "", TK[L], ASC)', 'adf'),
    ('s4', 'CONCATENATEX(SAMPLE(4, TK, TK[K], ASC, TK[L], DESC), TK[L], "", TK[L], ASC)', 'acdf'),
    ('s3_neg', 'CONCATENATEX(SAMPLE(3, TN, TN[K], DESC, TN[L], DESC), TN[L], "", TN[L], ASC)', 'adf'),
    # CONCATENATEX's order
    ('x_tc_desc', 'CONCATENATEX(TC, TC[L], "", TC[K], DESC)', 'cb'),
    ('x_tcr_asc', 'CONCATENATEX(TCR, TCR[L], "", TCR[K], ASC)', 'bc'),
    ('x_tk_desc', 'CONCATENATEX(TK, TK[L], "", TK[K], DESC)', 'fedcba'),
    ('x_tkr_asc', 'CONCATENATEX(TKR, TKR[L], "", TKR[K], ASC)', 'abcdef'),
    ('x_tk_two', 'CONCATENATEX(TK, TK[L], "", TK[K], DESC, TK[L], ASC)', 'fedcba'),
    ('x_tn_asc', 'CONCATENATEX(TN, TN[L], "", TN[K], ASC)', 'fedcba'),
    ('x_measure_r', 'CONCATENATEX(TKR, TKR[L], "", [mkr], ASC)', 'abcdef'),
    # RANKX, RANK.EQ
    ('r_skip', 'CONCATENATEX(TK, RANKX(ALL(TK), TK[K]), ",", TK[L], ASC)', '6,5,4,3,2,1'),
    ('r_skip_r', 'CONCATENATEX(TKR, RANKX(ALL(TKR), TKR[K]), ",", TKR[L], ASC)', '6,5,4,3,2,1'),
    ('r_dense_asc', 'CONCATENATEX(TK, RANKX(ALL(TK), TK[K], , ASC, DENSE), ",", TK[L], ASC)', '1,2,3,4,5,6'),
    ('r_neg', 'CONCATENATEX(TN, RANKX(ALL(TN), TN[K]), ",", TN[L], ASC)', '1,2,3,4,5,6'),
    ('r_measure', 'CONCATENATEX(TK, RANKX(ALL(TK), [mk]), ",", TK[L], ASC)', '6,5,4,3,2,1'),
    ('r_val_ii_asc', 'RANKX(ALL(TK), TK[K], 9007199254740993, ASC)', 3),
    ('r_rankeq_asc', 'RANK.EQ(9007199254740993, TK[K], 1)', 3),
    # FIRST / LASTNONBLANK(VALUE): the column's own order
    ('n_last_tc', 'LASTNONBLANK(TC[K], 1) - 9007199254740992', 1),
    ('n_last_tcr', 'LASTNONBLANK(TCR[K], 1) - 9007199254740992', 1),
    ('n_first_tcr', 'FIRSTNONBLANK(TCR[K], 1) - 9007199254740992', 0),
    ('n_lastval_tcr', 'LASTNONBLANKVALUE(TCR[K], MAX(TCR[A]))', 2),
    ('n_firstval_tcr', 'FIRSTNONBLANKVALUE(TCR[K], MAX(TCR[A]))', 1),
    # exact already, and must stay exact
    ('m_max_tc', 'MAX(TC[K]) - 9007199254740992', 1),
    ('m_max_tcr', 'MAX(TCR[K]) - 9007199254740992', 1),
    ('m_min_tc', 'MIN(TC[K]) - 9007199254740992', 0),
    ('m_min_tcr', 'MIN(TCR[K]) - 9007199254740992', 0),
    ('m_maxx_tc', 'MAXX(TC, TC[K]) - 9007199254740992', 1),
    ('m_minx_tcr', 'MINX(TCR, TCR[K]) - 9007199254740992', 0),
    ('m_minx_tn', 'MINX(TN, TN[K]) + 9007199254740996', 0),
    ('m_pair_ii', 'MAX(9007199254740992, 9007199254740993) - 9007199254740992', 1),
    ('m_gt_max', 'IF(MAX(TC[K]) > 9007199254740992, 1, 0)', 1),
    ('m_sum_tc', 'SUM(TC[K]) - 18014398509481984', 1),
    ('k_max_ii_up', 'MAXX({9007199254740992, 9007199254740993}, [Value]) - 9007199254740992', 1),
    ('k_max_ii_dn', 'MAXX({9007199254740993, 9007199254740992}, [Value]) - 9007199254740992', 1),
    ('k_distinct_ii', 'COUNTROWS(DISTINCT({9007199254740993, 9007199254740992}))', 2),
    ('p_sub_ii', '9007199254740993 - 9007199254740992', 1),
    ('p_eq_ii', 'IF(9007199254740993 = 9007199254740992, 1, 0)', 0),
    ('p_gt_ii', 'IF(9007199254740993 > 9007199254740992, 1, 0)', 1),
    ('p_in_ii', 'IF(9007199254740993 IN {9007199254740992}, 1, 0)', 0),
    ('f_gt_ii', 'CONCATENATEX(FILTER(TK, TK[K] > 9007199254740992), TK[L], "", TK[L], ASC)', 'cdef'),
    ('f_ge_ii', 'CONCATENATEX(FILTER(TK, TK[K] >= 9007199254740993), TK[L], "", TK[L], ASC)', 'cdef'),
    ('f_lt_ii', 'CONCATENATEX(FILTER(TK, TK[K] < 9007199254740993), TK[L], "", TK[L], ASC)', 'ab'),
    ('f_neg_lt', 'CONCATENATEX(FILTER(TN, TN[K] < -9007199254740992), TN[L], "", TN[L], ASC)', 'cdef'),
    ('c_gt_ii', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), TK[K] > 9007199254740992)', 'cdef'),
    ('c_lt_ii', 'CALCULATE(CONCATENATEX(TK, TK[L], "", TK[L], ASC), TK[K] < 9007199254740993)', 'ab'),
    ('l_lookup_ii', 'LOOKUPVALUE(TK[L], TK[K], 9007199254740993)', 'c'),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


def test_order_key_keeps_the_exact_number():
    """Exact arithmetic: 2**53 + 1 orders after 2**53, and the Int64 2**53
    and the Double 2**53.0 are one place; a bool still orders as 0 / 1."""
    assert de._order_key(P + 1) > de._order_key(P)
    assert de._order_key(-(P + 1)) < de._order_key(-P)
    assert de._order_key(P) == de._order_key(float(P))
    assert de._order_key(P + 3) < de._order_key(float(P + 4)) == de._order_key(P + 4)
    assert de._order_key(None) < de._order_key(False) < de._order_key(True) == de._order_key(1)
    assert de._order_key(2.5) < de._order_key(3) < de._order_key("a")


def test_rankx_keys_are_exact_unless_a_double_is_among_them():
    """Exact arithmetic: Int64 keys keep their values (a BLANK is the int 0),
    and one Double among the keys makes them all Doubles. The value ranked
    (the first) is another expression and changes no key (build_b257c.py
    r_dense_dbl)."""
    assert de.DAXEngine._rankx_keys([P + 1, P, None]) == [P + 1, P, 0]
    assert all(type(k) is int for k in de.DAXEngine._rankx_keys([P + 1, P, None]))
    keys = de.DAXEngine._rankx_keys([float(P), P + 1, P])
    assert keys == [float(P), P + 1, P] and [type(k) for k in keys] == [float, int, int]
    keys = de.DAXEngine._rankx_keys([5, P + 1, float(P)])
    assert keys == [5, float(P), float(P)] and [type(k) for k in keys] == [int, float, float]


def test_rankx_dense_counts_the_keys_own_values():
    """Power BI Desktop 2.152 (build_b257c.py r_dense_dbl, over TK[K] = 2**53 -
    1 .. 2**53 + 2): RANKX(ALL(TK), TK[K], 9007199254740991.0, DESC, DENSE)
    is 4 -- the value meets each key as a Double, but 2**53 and 2**53 + 1
    stay two keys; we answered 3."""
    tables = {"TK": {"columns": ["L", "K"], "rows": [["a", P - 1], ["b", P], ["c", P + 1], ["d", P + 2]]}}
    assert de.evaluate_measure("m", tables, {"m": "RANKX(ALL(TK), TK[K], 9007199254740991.0, DESC, DENSE)"}) == 4
