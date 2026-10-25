"""Issue #237: TOPNSKIP reads its order arguments as TOPN does.

TOPNSKIP ordered by its first orderBy alone, always descending: the order
argument after it was never read, and any further orderBy was ignored. So
TOPNSKIP(3, 0, T, [v], 1) over 1..10 returned 8, 9, 10. The arguments after
the table now pair by position as TOPN's do (DAXEngine._order_pairs, issue
#212) and every pair orders in turn (DAXEngine._pair_order).

Expected values: Power BI Desktop 2.152 over ADOMD (battery
F:/_desktop_oracle/build_b234.py, measure t_skip_1). That model's Desktop
refuses a skip above 0 ("not compatible with the DirectQuery data source",
t_skip_asc / _desc / _default / _two), so the skip keeps its meaning and
the other cases here follow TOPN's measured order rules (issues #158,
#212), not Desktop answers for TOPNSKIP itself. The conformance golden
SUMX(TOPNSKIP(2, 1, F, F[v]), F[v]) = 300 (tests/conformance) still holds.
An orderBy without an order keeps its old direction, descending as TOPN's
default; that is not measured for TOPNSKIP and is deliberately not pinned.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"D": {"columns": ["x"], "rows": [[1]]}}
T10 = 'SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value])'
GROUPS = 'SELECTCOLUMNS(GENERATESERIES(1, 12), "a", INT(([Value] - 1) / 4), "v", [Value])'


def _engine(dax):
    """The measure's answer as ADOMD prints it; "ERROR" when the engine
    reports the measure failed."""
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    if "p" in eng.eval_errors:
        return "ERROR"
    if isinstance(got, float) and got.is_integer():
        got = int(got)
    return None if got is None else str(got)


def _skip(n, skip, table, orders):
    return f'CONCATENATEX(TOPNSKIP({n}, {skip}, {table}, {orders}), [v], ",", [v], ASC)'


DESKTOP = [
    ("t_skip_1", _skip(3, 0, T10, "[v], 1"), "1,2,3"),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _engine(expr) == want


@pytest.mark.parametrize("order,want", [
    ("ASC", "1,2,3"), ("asc", "1,2,3"), ("TRUE", "1,2,3"), ("TRUE()", "1,2,3"),
    ("DESC", "8,9,10"), ("0", "8,9,10"), ("FALSE()", "8,9,10"),
])
def test_order_words(order, want):
    assert _engine(_skip(3, 0, T10, f"[v], {order}")) == want


def test_every_order_pair_counts():
    # a ascending, then v descending within a: 4, 3, 2, 1, 8, 7, ...
    assert _engine(_skip(5, 0, GROUPS, "[a], ASC, [v], DESC")) == "1,2,3,4,8"
    assert _engine(_skip(3, 0, GROUPS, "[a], DESC, [v], ASC")) == "9,10,11"
    # the skip counts rows of that order, as before
    assert _engine(_skip(3, 2, GROUPS, "[a], ASC, [v], DESC")) == "1,2,8"


def test_no_ties_past_n():
    # unlike TOPN, the rows tied with the last are not added
    assert _engine(f'COUNTROWS(TOPNSKIP(2, 0, {GROUPS}, [a], ASC))') == "2"
    assert _engine(f'COUNTROWS(TOPN(2, {GROUPS}, [a], ASC))') == "4"


def test_an_order_that_is_no_order_is_an_error():
    assert _engine(_skip(3, 0, T10, "[v], 2")) == "ERROR"
    eng = de.DAXEngine()
    with pytest.raises(de._DAXEvaluationError, match="ORDER argument in the function TOPNSKIP"):
        eng._eval_expr(f"TOPNSKIP(3, 0, {T10}, [v], 2)", de.DAXContext(TABLES, {}))
