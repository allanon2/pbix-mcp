"""#324: calculated columns keep Int64 values past 2**53 apart.

The calculated-column path (calc_tables) keyed numbers through float():
LOOKUPVALUE's index (_lv_key, int(float(v))), the FILTER inequality's sort
keys (_ord_key, float(v)) and the running SUM (a float accumulator). Past
2**53, Int64 values that share a Double merged: a LOOKUPVALUE over the keys
2**53, 2**53 + 1, 2**53 + 2 was refused as ambiguous, FILTER(T, T[K] <=
EARLIER(T[K])) kept 2**53 + 1 for the row 2**53, DISTINCTCOUNT counted the
two once, and SUM over 2**53, 1, 1 stayed 2**53 -- the defect class of #240
and #252, in the calculated-column path.

Expected values: exact arithmetic under Power BI Desktop 2.152's rules,
measured for measures in the same engine -- two Int64 values compare,
order and sum exactly (build_b234.py, issue #240; build_b257.py, issue
#252: TOPN / RANKX / MIN / MAX / SUM over 2**53 - 1 .. 2**53 + 4), and an
Int64 that meets a Double compares as a Double (build_b257.py f_eq_id /
f_x_lt, #325). The calculated-column path itself is not
measured past 2**53.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax.calc_tables import _lv_key, _ord_key, _running, evaluate_row_context_column

pytestmark = pytest.mark.unit

P = 2 ** 53


def _col(table, snap, expr):
    vals, err = evaluate_row_context_column(
        snap[table]["columns"], snap[table]["rows"], expr, table, snap, [])
    assert err is None, err
    return vals


SNAP = {"T": {"columns": ["K", "A"], "rows": [[P, 1], [P + 1, 2], [P + 2, 4]]},
        "M": {"columns": ["K", "L"], "rows": [[P, "b"], [P + 1, "c"], [P + 2, "d"]]},
        "B": {"columns": ["K", "A"], "rows": [[1, P], [2, 1], [3, 1]]}}


def test_lookupvalue_finds_each_int64_key():
    """0.9.147 refused it: 'matches rows with different values'."""
    assert _col("T", SNAP, "LOOKUPVALUE(M[L], M[K], T[K])") == ["b", "c", "d"]


def test_running_sum_by_int64_order():
    assert _col("T", SNAP, "CALCULATE(SUM(T[A]), FILTER(T, T[K] <= EARLIER(T[K])))") == [1, 3, 7]
    assert _col("T", SNAP, "CALCULATE(SUM(T[A]), FILTER(T, T[K] < EARLIER(T[K])))") == [None, 1, 3]
    assert _col("T", SNAP, "CALCULATE(SUM(T[A]), FILTER(T, T[K] >= EARLIER(T[K])))") == [7, 6, 4]


def test_running_max_and_distinctcount():
    assert _col("T", SNAP, "CALCULATE(MAX(T[K]), FILTER(T, T[K] <= EARLIER(T[K]))) - "
                           "9007199254740992") == [0, 1, 2]
    assert _col("T", SNAP, "CALCULATE(DISTINCTCOUNT(T[K]), FILTER(T, T[K] <= EARLIER(T[K])))") == [1, 2, 3]


def test_running_sum_of_int64_values_is_exact():
    """2**53 + 1 + 1 is 2**53 + 2, exactly (a float accumulator stays at
    2**53)."""
    got = _col("B", SNAP, "CALCULATE(SUM(B[A]), FILTER(B, B[K] <= EARLIER(B[K]))) - 9007199254740992")
    assert got == [0, 1, 2]


def test_int64_keys_meet_a_double_threshold_as_doubles():
    """K = 2**53, 2**53 + 1, 2**53 + 2 against the Double 2**53.0: as
    Doubles the first two are 2**53, so two rows are <= it (exactly, one)."""
    snap = {"T": {"columns": ["K", "D"], "rows": [[P, float(P)], [P + 1, float(P)], [P + 2, float(P)]]}}
    assert _col("T", snap, "CALCULATE(COUNTROWS(T), FILTER(T, T[K] <= EARLIER(T[D])))") == [2, 2, 2]


def test_double_keys_meet_an_int64_threshold_as_doubles():
    """D = 2**53.0, (2**53 + 4).0, (2**53 + 2).0 against the Int64
    2**53 + 3, which is the Double 2**53 + 4 (round half to even): all
    three are <= it (exactly, two)."""
    snap = {"U": {"columns": ["D", "K"],
                  "rows": [[float(P), P + 3], [float(P + 4), P + 3], [float(P + 2), P + 3]]}}
    assert _col("U", snap, "CALCULATE(COUNTROWS(U), FILTER(U, U[D] <= EARLIER(U[K])))") == [3, 3, 3]


def test_helpers_keep_int64_exact():
    """Exact arithmetic; below 2**53 the keys are what they were."""
    assert _lv_key(P + 1) != _lv_key(P)
    assert _lv_key(3) == _lv_key(3.0) == 3
    assert _lv_key(2.5) == 2.5
    assert _ord_key(P + 1) > _ord_key(P)
    assert _ord_key(3) == 3.0
    assert _running("sum", [P, 1, 1]) == [P, P + 1, P + 2]
    run = _running("sum", [1, 2, None])
    assert run == [1.0, 3.0, 3.0] and all(type(v) is float for v in run)
