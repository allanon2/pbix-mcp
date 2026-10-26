"""#328: CONCATENATEX orders by every orderBy / order pair, and
reads its order words as TOPN does.

CONCATENATEX read only its first orderBy and order, and any order word
without "DESC" ascended: 0 and FALSE() ascended, an order of 2 or an
orderBy where an order is due went unnoticed. Power BI Desktop pairs the
arguments by position, orders by each pair in turn, reads 0 / FALSE / DESC
as descending and 1 / TRUE / ASC as ascending, ascends for a last orderBy
without an order (as an omitted order ascends, b175 cx_doubles in
tests/test_issue178), and refuses any other order: "Unexpected value for
ORDER argument in CONCATENATEX function. Use 0/FALSE/DESC for descending
order or 1/TRUE/ASC for ascending order." (Desktop's own English,
build_b257c.py e_cx_iferror; #330. It printed the Norwegian
"Uventet verdi for ORDER-argumentet i funksjonen CONCATENATEX. Bruk
0/FALSE/DESC for synkende rekkefolge eller 1/TRUE/ASC for stigende
rekkefolge." in build_b257b.py).

Expected values: Power BI Desktop 2.152 over ADOMD, batteries
F:/_desktop_oracle/build_b257.py (F:/_tmp_pbix/open1/b252) and
build_b257b.py (group H; F:/_tmp_pbix/open2/b252-order53), by probe id.
TS = (1, p), (1, q), (2, r); TK[K] = 2**53 - 1 .. 2**53 + 4 labelled a .. f.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit

P = 2 ** 53
TABLES = {
    "TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"]]},
    "TK": {"columns": ["L", "K"], "rows": [[lab, P - 1 + i] for i, lab in enumerate("abcdef")]},
}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


def _error(expr):
    """The engine's error for the measure (eval_errors), None when it answered."""
    eng = de.DAXEngine()
    eng.evaluate_measure("m", de.DAXContext(TABLES, {"m": expr}))
    return eng.eval_errors.get("m")


DESKTOP = [
    ('z_cx_two_small', 'CONCATENATEX(TS, TS[L], "", TS[G], ASC, TS[L], DESC)', 'qpr'),
    ('x_tk_two', 'CONCATENATEX(TK, TK[L], "", TK[K], DESC, TK[L], ASC)', 'fedcba'),
    ('cx_zero', 'CONCATENATEX(TS, TS[L], "", TS[L], 0)', 'rqp'),
    ('cx_false', 'CONCATENATEX(TS, TS[L], "", TS[L], FALSE())', 'rqp'),
    ('cx_one', 'CONCATENATEX(TS, TS[L], "", TS[L], 1)', 'pqr'),
    ('cx_trailing', 'CONCATENATEX(TS, TS[L], "", TS[G], DESC, TS[L])', 'rpq'),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


@pytest.mark.parametrize("probe,expr", [
    ('cx_bad', 'CONCATENATEX(TS, TS[L], "", TS[L], 2)'),
    ('cx_noword', 'CONCATENATEX(TS, TS[L], "", TS[G], TS[L])'),
])
def test_an_order_that_is_no_order_is_refused(probe, expr):
    assert ("Unexpected value for ORDER argument in CONCATENATEX function. Use 0/FALSE/DESC for "
            "descending order or 1/TRUE/ASC for ascending order.") in (_error(expr) or "")
    with pytest.raises(DAXEvaluationError, match="ORDER argument in CONCATENATEX function"):
        de.DAXEngine()._eval_expr(expr, de.DAXContext(TABLES, {}))
