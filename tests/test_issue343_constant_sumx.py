"""Issue #343: SUMX / AVERAGEX of an expression that reads no column.

What was wrong: SUMX(T, 0.1) added the constant once per row, left to right,
in the formula engine -- an expression of no column had no storage-engine
plan. Power BI Desktop 2.152 adds it in the storage engine like any other
expression of the table, along the 16,384-row chunks of the scan, and since
the expression reads no column each chunk adds the value times its rows:
build_b243c.py x_const, SUMX(FactSales, 0.1) - 19999 over Agents' 199,999
rows, is 0.899999999997817 -- 12 chunks of 1638.4 and one of 339.1 -- where
the rows folded one by one give 0.8999999894549546 and 0.1 x 199,999 gives
0.9 (pbix_mcp.dax.se_scan rule 5).

Where the expected values come from: Desktop's answer (the Agents file,
skipped when the community corpus is not present), and on hand-made tables
exact IEEE arithmetic of the rule.
"""
from __future__ import annotations

import json
import os
import warnings

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

_SAMPLES = os.environ.get("PBIX_TEST_SAMPLES") or os.path.join(
    os.path.dirname(__file__), "..", "test_samples")


def _corpus(clean, legacy: str) -> str:
    for p in (clean, legacy):
        if p and os.path.exists(os.path.join(_SAMPLES, p)):
            return os.path.join(_SAMPLES, p)
    return os.path.join(_SAMPLES, legacy)


AGENTS = _corpus("Agents_Performance.pbix", os.path.join(
    "temp_dl", "Full Dashboards", "Agents Performance - Dashboard",
    "Agents Performance - Dashboard.pbix"))

N = 40000                       # chunks of 16384, 16384 and 7232 rows


def _tables() -> dict:
    # a table without a layout: one segment, one bit-packed run
    return {"T": {"columns": ["k", "v"], "rows": [[i % 7, 1.0] for i in range(N)]}}


def _chunked(c: float, sizes=(16384, 16384, 7232)) -> float:
    acc = 0.0
    for k in sizes:
        acc += c * k
    return acc


def _folded(c: float, n: int) -> float:
    acc = 0.0
    for _ in range(n):
        acc += c
    return acc


def test_a_constant_adds_value_times_rows_per_chunk():
    got = de.evaluate_measures_batch(["m"], _tables(), {"m": "SUMX(T, 0.1)"}, {})["m"]
    assert got == _chunked(0.1)
    assert _chunked(0.1) != _folded(0.1, N)          # the rows one by one: 0.9.147


def test_averagex_of_a_constant_is_that_total_over_the_count():
    # Desktop agrees (build_b243d.py k_const_avgx: AVERAGEX(FactSales, 0.7)
    # * 1E15 - 7E14 is 0.125)
    # 60,000 rows: chunks of 16384 x 3 and 10848, whose totals of 0.7 add to
    # 41999.99999999999 (correctly rounded, as 0.9.147 averaged: 42000)
    n = 60000
    tables = {"T": {"columns": ["k", "v"], "rows": [[0, 1.0] for _ in range(n)]}}
    got = de.evaluate_measures_batch(["m"], tables, {"m": "AVERAGEX(T, 0.7)"}, {})["m"]
    total = _chunked(0.7, (16384, 16384, 16384, 10848))
    assert total == 41999.99999999999
    assert got == total / n


def test_a_constant_under_a_filter_counts_the_selected_rows_of_each_chunk():
    # Desktop agrees (build_b243d.py k_const_f 0.899999999997817)
    # T[k] = 0 keeps every 7th row: 2341 of chunk 0, 2340 of chunk 1, 1033 of chunk 2
    got = de.evaluate_measures_batch(["m"], _tables(), {"m": "CALCULATE(SUMX(T, 0.1), T[k] = 0)"}, {})["m"]
    sizes = [sum(1 for i in range(a, min(a + 16384, N)) if i % 7 == 0) for a in range(0, N, 16384)]
    assert got == _chunked(0.1, sizes)
    assert got != _folded(0.1, sum(sizes))


def test_a_whole_number_constant_stays_exact():
    got = de.evaluate_measures_batch(["m"], _tables(), {"m": "SUMX(T, 3)"}, {})["m"]
    assert got == 3 * N


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answer():
    from pbix_mcp import server as S
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d7ag"))["success"]
    try:
        ctx = S._get_dax_context("d7ag")
        ctx["measure_defs"]["__x_const"] = "SUMX(FactSales, 0.1) - 19999"
        got = json.loads(S.pbix_evaluate_dax("d7ag", "__x_const", apply_default_filters=False))
    finally:
        S.pbix_close("d7ag")
    v = got["results"][0]["value"]
    assert float(f"{v:.15g}") == 0.899999999997817     # build_b243c.py x_const
