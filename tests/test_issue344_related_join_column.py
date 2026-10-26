"""Issue #344: what a scan reads of another table, it reads through
its join column -- RELATED in a SUMX expression or a FILTER condition, and the
rows of a CALCULATE table filter FILTER(T, <condition>).

What was wrong:
- SUMX(T, T[v] * RELATED(D[m])) had no storage-engine plan (another table's
  column) and was folded row by row; Power BI Desktop 2.152 adds it over the
  pieces of T[v] and T's join column toward D (build_b243c.py x_related,
  Agents: SUMX(FactSales, SalesAmount * RELATED(DimChannel[ChannelKey])) -
  1317665990 is 0.972300052642822; row by row 0.9724409580230713).
- A FILTER condition's RELATED(D[c]) cut nothing; Desktop cuts at the join
  column as a CALCULATE filter on D does (r_filter_related 0.215399503707886
  = af_dimstore; uncut 0.21539807319641113).
- CALCULATE(SUM(T[v]), FILTER(T, <condition>)) cut the scan at every column
  of T and of the tables T's rows expand to -- the table filter writes them
  all; Desktop cuts at what the condition reads (r_filter_related_calc
  0.215399503707886; every column 0.2154003381729126).

Where the expected values come from: Desktop's answers (the Agents file,
skipped when the community corpus is not present), and on hand-made layouts
exact IEEE arithmetic of the storage-engine rule: T[v] is 2**54 then ones, one
bit-packed run; the ones in the first chunk of the first piece round away
(2**54 + 1 is 2**54), the others count. So the result minus 2**54 is 40,000
less the rows of that first chunk: 23,616 uncut (16,384), 30,000 cut at
row 10,000 (T[k]'s runs), 35,000 cut at row 5,000 too (T[w]'s).
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


B = int(BIG)


def test_related_in_a_sumx_expression_reads_the_join_column():
    assert _eval(f"SUMX(T, T[v] * RELATED(D[m])) - {B}") == 30000
    # the expression of T's own columns alone: no cut
    assert _eval(f"SUMX(T, T[v] * 1) - {B}") == 23616


def test_related_in_a_filter_condition_cuts_at_the_join_column():
    assert _eval(f"SUMX(FILTER(T, RELATED(D[m]) <> 0), T[v]) - {B}") == 30000


def test_a_table_filter_cuts_at_what_its_condition_reads():
    # its rows filter every column of T (and D): read so, the scan was cut at
    # T[w] too (35000)
    assert _eval(f"CALCULATE(SUM(T[v]), FILTER(T, RELATED(D[m]) <> 0)) - {B}") == 30000
    assert _eval(f"CALCULATE(SUM(T[v]), FILTER(ALL(T), RELATED(D[m]) <> 0)) - {B}") == 30000
    # a condition of its own column and one through RELATED: cut at both
    # (Desktop agrees: build_b243d.py r_two_cond 0.215399742126465)
    assert _eval(f"CALCULATE(SUM(T[v]), FILTER(T, T[w] >= 0 && RELATED(D[m]) <> 0)) - {B}") == 35000


def test_table_filters_cutting_at_different_columns_do_not_share_memo_entries():
    # Found in review: two FILTER table filters that keep the same rows (all
    # of them here) but cut at different columns were keyed by their rows
    # alone, so the aggregate memo (model-wide, it outlives a call) and the
    # measure memo answered the second with the first one's value. Every
    # order of one batch now gives each measure its own value.
    import itertools
    ms = {"a": f"CALCULATE(SUM(T[v]), FILTER(T, RELATED(D[m]) <> 0)) - {B}",
          "b": f"CALCULATE(SUM(T[v]), FILTER(T, T[w] >= 0 && RELATED(D[m]) <> 0)) - {B}",
          "c": f"CALCULATE(SUM(T[v]), FILTER(T, TRUE())) - {B}",
          "d": f"CALCULATE(SUM(T[v]), FILTER(T, T[w] >= 0)) - {B}"}
    alone = {}
    for k in ms:
        tables, rels = _model()
        alone[k] = de.evaluate_measures_batch([k], tables, ms, {}, relationships=rels)[k]
    assert alone["a"] == 30000          # cut at the join column (r_filter_related_calc)
    assert len({alone["a"], alone["b"], alone["c"]}) == 3
    for order in itertools.permutations(ms):
        tables, rels = _model()
        got = de.evaluate_measures_batch(list(order), tables, ms, {}, relationships=rels)
        assert got == alone, order


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    want = {  # build_b243c.py
        "x_related": ("SUMX(FactSales, FactSales[SalesAmount] * RELATED(DimChannel[ChannelKey])) - 1317665990",
                      "0.972300052642822"),
        "r_filter_related": ("SUMX(FILTER(FactSales, RELATED(DimStore[StoreKey]) <> 0), "
                             "FactSales[SalesAmount]) - 729782919", "0.215399503707886"),
        "r_filter_related_calc": ("CALCULATE(SUM(FactSales[SalesAmount]), FILTER(FactSales, "
                                  "RELATED(DimStore[StoreKey]) <> 0)) - 729782919", "0.215399503707886"),
    }
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d8ag"))["success"]
    try:
        ctx = S._get_dax_context("d8ag")
        for k, (e, _w) in want.items():
            ctx["measure_defs"]["__" + k] = e
        got = json.loads(S.pbix_evaluate_dax("d8ag", ",".join("__" + k for k in want),
                                             apply_default_filters=False))
    finally:
        S.pbix_close("d8ag")
    for r, (k, (_e, w)) in zip(got["results"], want.items()):
        assert float(f"{r['value']:.15g}") == float(w), (k, r["value"], w)
