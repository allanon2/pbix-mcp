"""Issue #344, build_b243d.py: a KEEPFILTERS table filter cuts the
scan where its condition reads, as the plain table filter does.

What was wrong (rel148): CALCULATE(SUM(T[v]), KEEPFILTERS(FILTER(T,
<condition>))) wrote its rows as an In-set filter on every column of T and of
the tables T expands to, and a scan of T was cut at all of them. Power BI
Desktop 2.152 (build_b243d.py r_keep, the Desktop-saved Agents file):
CALCULATE(SUM(FactSales[SalesAmount]), KEEPFILTERS(FILTER(FactSales,
RELATED(DimStore[StoreKey]) <> 0))) - 729782919 is 0.215399503707886, cut
at FactSales[StoreKey] alone -- the plain table filter's value
(build_b243c.py r_filter_related_calc); rel148 gave 0.2154003381729126 (cut
at every column).

The same battery confirmed the rest of the rule: RELATED across two
relationships reads the fact's join column toward the first (r_two_hop
0.951499819755554), and a condition of its own column and one through
RELATED cuts at both (r_two_cond 0.215399742126465).

Where the expected values come from: Desktop's answers (the Agents test skips
without the community corpus), and on hand-made layouts exact IEEE
arithmetic of the storage-engine rule: T[v] is 2**54 then ones, one
bit-packed run; the ones in the first chunk of the first piece round away.
So the result minus 2**54 is 40,000 less the rows of that first chunk:
30,000 cut at row 10,000 (T[k]'s runs), 35,000 cut at row 5,000 too (T[w]'s).
"""
from __future__ import annotations

import json
import os
import warnings
from array import array

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

BIG = 2.0 ** 54
B = int(BIG)
N = 40000
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


def _layout(n: int, columns: dict) -> dict:
    return {"rows": n, "segments": (n,),
            "columns": {c: {"starts": array("q", [s for s, _r in runs]),
                            "rle": bytes(1 if r else 0 for _s, r in runs), "enc": "hash", "mag": 1.0}
                        for c, runs in columns.items()}}


def _model():
    # T: v = 2**54 then ones (bit-packed); k = 1 for rows < 10000, else 2 (two
    # RLE runs); w = 0 / 1 cut at row 5000 (two RLE runs)
    rows = [[1.0, 1 if i < 10000 else 2, 0 if i < 5000 else 1] for i in range(N)]
    rows[0][0] = BIG
    t = {"columns": ["v", "k", "w"], "rows": rows,
         "storage": _layout(N, {"v": [(0, False)], "k": [(0, True), (10000, True)],
                                "w": [(0, True), (5000, True)]})}
    d = {"columns": ["k", "m"], "rows": [[1, 1.0], [2, 1.0]]}
    rels = [{"FromTable": "T", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    return {"T": t, "D": d}, rels


def _eval(expr: str):
    tables, rels = _model()
    return de.evaluate_measures_batch(["m"], tables, {"m": expr}, {}, relationships=rels)["m"]


def test_a_keepfilters_table_filter_cuts_at_what_its_condition_reads():
    assert _eval(f"CALCULATE(SUM(T[v]), KEEPFILTERS(FILTER(T, RELATED(D[m]) <> 0))) - {B}") == 30000
    # the plain table filter, as before
    assert _eval(f"CALCULATE(SUM(T[v]), FILTER(T, RELATED(D[m]) <> 0)) - {B}") == 30000


def test_under_a_filter_in_force_it_still_keeps_only_the_rows_both_keep():
    # T[w] = 1 in force, intersected: the rows from 5000 on, 35,000 ones
    # (the 2**54 row is not among them) -- KEEPFILTERS' semantics unchanged
    got = _eval("CALCULATE(CALCULATE(SUM(T[v]), KEEPFILTERS(FILTER(T, RELATED(D[m]) <> 0))), T[w] = 1)")
    assert got == 35000.0


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    sa = "FactSales[SalesAmount]"
    want = {  # build_b243d.py, desk_b243d_agents.json
        "r_keep": (f"CALCULATE(SUM({sa}), KEEPFILTERS(FILTER(FactSales, RELATED(DimStore[StoreKey]) <> 0))) "
                   "- 729782919", "0.215399503707886"),
        "r_two_hop": (f"SUMX(FactSales, {sa} * RELATED(DimEmployee[PayFrequency])) - 1013055310",
                      "0.951499819755554"),
        "r_two_cond": (f"CALCULATE(SUM({sa}), FILTER(FactSales, FactSales[SalesQuantity] > 0 "
                       "&& RELATED(DimChannel[ChannelKey]) <> 0)) - 729782919", "0.215399742126465"),
    }
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d8b243d"))["success"]
    try:
        ctx = S._get_dax_context("d8b243d")
        for k, (e, _w) in want.items():
            ctx["measure_defs"]["__" + k] = e
        got = json.loads(S.pbix_evaluate_dax("d8b243d", ",".join("__" + k for k in want),
                                             apply_default_filters=False))
    finally:
        S.pbix_close("d8b243d")
    for r, (k, (_e, w)) in zip(got["results"], want.items()):
        assert float(f"{r['value']:.15g}") == float(w), (k, r["value"], w)
