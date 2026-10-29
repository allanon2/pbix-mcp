"""#361: a measure reference in a model table's rows looks its name up among
the table's columns once, not per row.

Since #358 a bare [name] reads an open row's column before a measure of that
name, so SUMX(Fact, [S]) asks every Fact row whether it has a column S. 0.9.151
answered by lowering every key of the row (_key_any_case) and then running
_find_col_idx's three passes over the table's columns, per row, in vain: the
measure-reference iteration became about 7% slower than on 0.9.149, and
tests/test_issue150_memo_key_speed.py's 1.45 ratio failed. The answer is a fact
about the table and the name, kept on the model's cache ('col-name').

Expected values: by construction (counts of the lookup, independent of the row
count), and the sums by exact arithmetic.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def _tables(n: int) -> dict:
    return {"Fact": {"columns": ["Key", "Amt"], "rows": [[i % 7, float(i)] for i in range(n)]}}


def _lookups(n: int, monkeypatch) -> int:
    calls = []
    real = de.DAXContext._find_col_idx

    def counting(self, cols, name):
        if name == "S":
            calls.append(name)
        return real(self, cols, name)

    monkeypatch.setattr(de.DAXContext, "_find_col_idx", counting)
    got = de.evaluate_measures_batch(["m"], _tables(n), {"m": "SUMX(Fact, [S])", "S": "SUM(Fact[Amt])"}, {})
    assert got["m"] == sum(float(i) for i in range(n))
    return len(calls)


def test_the_measures_name_is_looked_up_once_not_per_row(monkeypatch):
    few = _lookups(20, monkeypatch)
    many = _lookups(400, monkeypatch)
    assert many == few <= 2


def test_a_row_column_still_wins_over_the_measure():
    # #358 unchanged: ADDCOLUMNS' column S is read, not the measure S
    got = de.evaluate_measures_batch(
        ["m"], _tables(5), {"m": 'SUMX(ADDCOLUMNS(Fact, "s", 1), [S])', "S": "SUM(Fact[Amt])"}, {})
    assert got["m"] == 5


def test_a_model_column_in_another_case_is_still_found():
    got = de.evaluate_measures_batch(["m"], _tables(4), {"m": "SUMX(Fact, [amt])"}, {})
    assert got["m"] == 0.0 + 1.0 + 2.0 + 3.0
