"""Issue #342: AVERAGEX over values the formula engine computes adds
them left to right in the iteration's order.

What was wrong: AVERAGEX added its values correctly rounded (math.fsum, issue
#246), where SUMX added left to right. Power BI Desktop 2.152 adds them left
to right in the iteration's order for both: Agents' [Employees Avg MTD Sales]
= AVERAGEX(ALLSELECTED(DimEmployee[EmployeeKey]), [MTD Total Sales]) is the
fold of the 262 members' values in DimEmployee's storage order, the blank
member last, over their count (build_b243b.py gb_avgx_emp: 73514 +
0.796034732862609; fsum gave 0.7960347328189528, the fold in key order
0.7960347328335047), and SUMX the same fold (gb_sumx_emp 19260876 +
0.561100009828806). The members' values themselves are grouped scans
(#340).

Where the expected values come from: Desktop's answers by probe id (the
Agents file, skipped when the community corpus is not present), and exact
IEEE arithmetic of the rule on a hand-built model (the fold in the
dimension's order, compared with the correctly rounded sum it must not equal).
"""
from __future__ import annotations

import json
import math
import os
import warnings

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

# the corpus as the other corpus tests find it (test_cross_report._find_pbix):
# PBIX_TEST_SAMPLES, else ../test_samples; the name
# scripts/download_test_corpus.py gives a file, else its legacy path
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
RELS = [{"FromTable": "F", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
         "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]


def test_averagex_over_members_folds_in_their_order():
    d = {"columns": ["k"], "rows": [[3], [1], [2]]}
    f = {"columns": ["k", "v"], "rows": [[3, 1e16], [1, 1.0], [2, 1.0]]}
    ms = {"ax": "AVERAGEX(VALUES(D[k]), CALCULATE(SUM(F[v])))",
          "sx": "SUMX(VALUES(D[k]), CALCULATE(SUM(F[v])))",
          "M": "SUM(F[v])", "axm": "AVERAGEX(ALL(D[k]), [M])"}
    got = de.evaluate_measures_batch(list(ms), {"D": d, "F": f}, ms, {}, relationships=RELS)
    folded = (1e16 + 1.0) + 1.0                      # D's order: 3, 1, 2
    assert folded != math.fsum([1e16, 1.0, 1.0])
    assert got["sx"] == folded
    assert got["ax"] == got["axm"] == folded / 3


def test_averagex_over_a_constant_table():
    got = de.evaluate_measures_batch(["a"], {}, {"a": "AVERAGEX({1E16, 1, 1}, [Value])"}, {})
    assert got["a"] == ((1e16 + 1) + 1) / 3


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    exprs = {"gb_avgx_emp": ("[Employees Avg MTD Sales] - 73514", "0.796034732862609"),
             "gb_sumx_emp": ("SUMX(ALLSELECTED(DimEmployee[EmployeeKey]), [MTD Total Sales]) - 19260876",
                             "0.561100009828806")}
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d6ag"))["success"]
    try:
        ctx = S._get_dax_context("d6ag")
        for k, (e, _w) in exprs.items():
            ctx["measure_defs"]["__" + k] = e
        out = json.loads(S.pbix_evaluate_dax("d6ag", ",".join("__" + k for k in exprs),
                                             apply_default_filters=False))
    finally:
        S.pbix_close("d6ag")
    got = {x["name"][2:]: x.get("value") for x in out["results"]}
    for k, (_e, want) in exprs.items():
        assert float(f"{got[k]:.15g}") == float(want), f"{k}: {got[k]!r}, Desktop {want}"
