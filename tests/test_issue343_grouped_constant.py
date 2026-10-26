"""Issue #343, build_b243d.py: SUMX of a constant in a grouped scan.

What was wrong (rel148): in a grouped scan, where the group column's rows of
several groups share a bit-packed piece, each row added the constant on its
own (se_scan rule 2), as a column's values do. Power BI Desktop 2.152
(build_b243d.py k_const_gb, the Desktop-saved Agents file):
SUMMARIZECOLUMNS(FactSales[channelKey], "v", VAR s = SUMX(FactSales, 0.1)
RETURN s - TRUNC(s)) gives channel 1 0.800000000001091, channel 4
0.300000000001546 and channel 3 0.500000000000227 -- each 16,384-row chunk
adds 0.1 times the group's rows in it, as without a group (rule 5) -- where
rel148 gave 0.8000000001229637 for channel 1 and 0.29999999992560333 for
channel 4, and missed all four channels. Channel 2
(0.299999999999272) is matched by no chunking tried: the engine gives
0.29999999999972715 (one ulp away), kept until a battery tells.

The same battery confirmed the rest of rule 5: a constant under a filter
(k_const_f 0.899999999997817), AVERAGEX of a constant (k_const_avgx 0.125)
and a constant beside SUM(SalesAmount) (k_const_sum_sa 0.115398049354553).

Where the expected values come from: Desktop's answers (the Agents test skips
without the community corpus), and on a hand-made table exact IEEE
arithmetic of the rule.
"""
from __future__ import annotations

import json
import os
import warnings
from array import array

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


def _chunked(c: float, sizes) -> float:
    acc = 0.0
    for k in sizes:
        acc += c * k
    return acc


def _folded(c: float, n: int) -> float:
    acc = 0.0
    for _ in range(n):
        acc += c
    return acc


def test_a_constant_in_a_grouped_scan_adds_value_times_the_groups_rows_per_chunk():
    # g = i % 2, one bit-packed run: each group has half of every chunk's
    # rows -- 8192, 8192 and 3616
    t = {"columns": ["g", "v"], "rows": [[i % 2, 1.0] for i in range(N)],
         "storage": {"rows": N, "segments": (N,), "columns": {
             "g": {"starts": array("q", [0]), "rle": bytes([0]), "enc": "value", "mag": 1.0},
             "v": {"starts": array("q", [0]), "rle": bytes([1]), "enc": "hash", "mag": 1.0}}}}
    ms = {"m": 'MAXX(FILTER(SUMMARIZECOLUMNS(T[g], "v", SUMX(T, 0.1)), T[g] = 0), [v])'}
    got = de.evaluate_measures_batch(["m"], {"T": t}, ms, {})["m"]
    want = _chunked(0.1, (8192, 8192, 3616))
    assert want != _folded(0.1, N // 2)              # each row on its own
    assert got == want


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    gb = 'SUMMARIZECOLUMNS(FactSales[channelKey], "v", VAR s = SUMX(FactSales, 0.1) RETURN s - TRUNC(s))'
    want = {  # build_b243d.py, desk_b243d_agents.json
        "k_const_gb_1": (f"MAXX(FILTER({gb}, FactSales[channelKey] = 1), [v])", "0.800000000001091"),
        "k_const_gb_4": (f"MAXX(FILTER({gb}, FactSales[channelKey] = 4), [v])", "0.300000000001546"),
        "k_const_gb_3": (f"MAXX(FILTER({gb}, FactSales[channelKey] = 3), [v])", "0.500000000000227"),
        "k_const_f": ("CALCULATE(SUMX(FactSales, 0.1), FactSales[StoreKey] <> 0) - 19999", "0.899999999997817"),
        "k_const_avgx": ("AVERAGEX(FactSales, 0.7) * 1E15 - 700000000000000", "0.125"),
        "k_const_sum_sa": ("SUM(FactSales[SalesAmount]) + SUMX(FactSales, 0.1) - 729802919",
                           "0.115398049354553"),
    }
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d7b243d"))["success"]
    try:
        ctx = S._get_dax_context("d7b243d")
        for k, (e, _w) in want.items():
            ctx["measure_defs"]["__" + k] = e
        got = json.loads(S.pbix_evaluate_dax("d7b243d", ",".join("__" + k for k in want),
                                             apply_default_filters=False))
    finally:
        S.pbix_close("d7b243d")
    for r, (k, (_e, w)) in zip(got["results"], want.items()):
        assert float(f"{r['value']:.15g}") == float(w), (k, r["value"], w)
