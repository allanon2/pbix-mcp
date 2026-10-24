"""Issue #212: TOPN rounds n half up and refuses an order that is no order.

- TOPN truncated n: TOPN(2.5, ...) and TOPN(2.6, ...) returned two rows where
  Desktop returns three.
- TOPN read any argument after an orderBy that was no order word as the next
  orderBy (descending): TOPN(3, T, [a], [v]) ordered by [a] and then [v], and
  TOPN(3, T, [v], 2) by [v] and then the constant 2. Desktop pairs the
  arguments by position -- the one after an orderBy is its order -- and
  refuses both queries: "Unexpected value for the ORDER argument in the
  function TOPN. Use 0/FALSE/DESC for descending order or 1/TRUE/ASC for
  ascending order".

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v", ...)
per probe, run twice with the same answers (the TOPN side probes of
docs/reverse-engineering/experiments/2026-10-24-sample.md; the probe ids are
the record's). tests/test_issue158_topn_order.py keeps the order pairs that
were already right.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"D": {"columns": ["x"], "rows": [[1]]}}
T10 = 'SELECTCOLUMNS(GENERATESERIES(1, 10), "v", [Value])'
GROUPS = 'SELECTCOLUMNS(GENERATESERIES(1, 20), "a", INT(([Value] - 1) / 4) + 1, "v", [Value])'

DESKTOP = [
    ("x-topn-n2.5", f"COUNTROWS(TOPN(2.5, {T10}, [v], ASC))", "3"),
    ("x-topn-n2.6", f"COUNTROWS(TOPN(2.6, {T10}, [v], ASC))", "3"),
    ("x-topn-a-v", f'CONCATENATEX(TOPN(3, {GROUPS}, [a], [v]), [v], ",", [v], ASC)', "ERROR"),
    ("x-topn-dir2", f'CONCATENATEX(TOPN(3, {T10}, [v], 2), [v], ",", [v], ASC)', "ERROR"),
]


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


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _engine(expr) == want


def test_order_error_names_topn():
    eng = de.DAXEngine()
    with pytest.raises(de._DAXEvaluationError, match="ORDER argument in the function TOPN"):
        eng._eval_expr(f"TOPN(3, {T10}, [v], 2)", de.DAXContext(TABLES, {}))


def test_rounded_n_keeps_ties():
    """The rounded n is the row the ties are counted from: 1.5 is the second
    row, and every row tied with it comes too."""
    assert _engine(f'CONCATENATEX(TOPN(1.5, {GROUPS}, [a], ASC), [v], ",", [v], ASC)') == "1,2,3,4"
    assert _engine(f'CONCATENATEX(TOPN(4.5, {GROUPS}, [a], ASC), [v], ",", [v], ASC)') == "1,2,3,4,5,6,7,8"
