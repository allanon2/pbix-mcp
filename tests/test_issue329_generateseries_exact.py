"""#329: GENERATESERIES of Int64 values steps exactly past 2**53
and materializes every row; a series too long to hold is refused, never
cut short.

GENERATESERIES called float() on its arguments and added the step in
floats, so past 2**53 the rows collapsed onto the Doubles they share
(9007199254740991 .. 9007199254740996 gave 1,000,000 rows of two values,
stopped by the cap), and every series stopped silently at 1,000,000 rows.
Int64 arguments now step exactly (range()), and a series longer than
_GENERATESERIES_MAX_ROWS raises an error before any row is built.

Expected values: Power BI Desktop 2.152 over ADOMD, battery
F:/_desktop_oracle/build_b257.py (F:/_tmp_pbix/open1/b252), by probe id:
g_series_n 6, g_series_max 2, g_series_distinct 6, z_series_big 1200000.
The bound's own test patches the limit to 1,000 (the engine's refusal, not
Desktop's).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit

TABLES = {"T": {"columns": ["x"], "rows": [[1]]}}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


def _error(expr):
    """The engine's error for the measure (eval_errors), None when it answered."""
    eng = de.DAXEngine()
    eng.evaluate_measure("m", de.DAXContext(TABLES, {"m": expr}))
    return eng.eval_errors.get("m")


@pytest.mark.parametrize("probe,expr,want", [
    ('g_series_n', 'COUNTROWS(GENERATESERIES(9007199254740991, 9007199254740996))', 6),
    ('g_series_max', 'MAXX(GENERATESERIES(9007199254740991, 9007199254740993), [Value]) - 9007199254740991', 2),
    ('g_series_distinct', 'COUNTROWS(DISTINCT(GENERATESERIES(9007199254740991, 9007199254740996)))', 6),
])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


def test_more_than_a_million_rows():
    """z_series_big: Desktop counts 1,200,000 rows; we stopped at 1,000,000."""
    assert _ev("COUNTROWS(GENERATESERIES(1, 1200000))") == 1200000


def test_the_values_are_the_exact_integers():
    eng = de.DAXEngine()
    rows = eng._eval_expr("GENERATESERIES(9007199254740991, 9007199254740996)", de.DAXContext(TABLES, {}))
    assert [r['Value'] for r in rows] == list(range(9007199254740991, 9007199254740997))
    assert all(type(r['Value']) is int and r['__value__'] == r['Value'] for r in rows)


def test_a_series_past_the_bound_is_refused(monkeypatch):
    monkeypatch.setattr(de, "_GENERATESERIES_MAX_ROWS", 1000)
    assert _ev("COUNTROWS(GENERATESERIES(1, 1000))") == 1000
    assert "GENERATESERIES would return 1,001 rows" in (_error("COUNTROWS(GENERATESERIES(1, 1001))") or "")
    assert "GENERATESERIES would return" in (_error("COUNTROWS(GENERATESERIES(0, 1000, 0.5))") or "")
    with pytest.raises(DAXEvaluationError, match="the engine materializes at most 1,000"):
        de.DAXEngine()._eval_expr("GENERATESERIES(1, 1001)", de.DAXContext(TABLES, {}))
