"""Issue #340: a group's sum adds as Desktop's GROUPED scan adds it.

What was wrong: a group of a SUMMARIZECOLUMNS, a visual's row (group_by) or an
iteration's member (SUMX / AVERAGEX over VALUES, ALLSELECTED, ...) summed its
rows like any filtered sum. Power BI Desktop 2.152 adds a grouped scan into
one accumulator per group (build_b243b.py, Agents FactSales): a piece of the
scan where the group column is RLE belongs to one group and adds as an
ungrouped scan does (value x rows for an RLE run of the summed column, 16,384-row
chunks otherwise); in a piece where the group column is bit-packed every row
is added to its group's accumulator on its own, the summed column's RLE rows
included. gb_ch 4/4 (channel 1: 0.541100263595581, where the same rows
under a filter, af_ch1, are 0.541100084781647), gb_chname 4/4 through the
relationship, gb_store 306/306, gb_emp_mtd 262/262 (DimEmployee through
DimStore: the fact's StoreKey decides). The many other models tried matched
none of gb_ch and at most 50 stores.

Where the expected values come from: Desktop's answers by probe id (the
Agents file, skipped when the community corpus is not present), and on
hand-made layouts exact IEEE arithmetic of the rule (2**54 then ones: a one
added to 2**54 on its own rounds away; a chunk's ones added among themselves
count).
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


def _layout(n: int, columns: dict) -> dict:
    return {"rows": n, "segments": (n,),
            "columns": {c: {"starts": array("q", [s for s, _r in runs]),
                            "rle": bytes(1 if r else 0 for _s, r in runs), "enc": "hash", "mag": 1.0}
                        for c, runs in columns.items()}}


def _tables(group_rle: bool, n: int = 40000) -> dict:
    rows = [[1.0, 0] for _ in range(n)]
    rows[0][0] = BIG
    t = {"columns": ["v", "g"], "rows": rows,
         "storage": _layout(n, {"v": [(0, False)], "g": [(0, group_rle)]})}
    return {"T": t, "D": {"columns": ["g"], "rows": [[0]]}}


RELS = [{"FromTable": "T", "FromColumn": "g", "ToTable": "D", "ToColumn": "g", "IsActive": 1,
         "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
M = {"m": f"SUM(T[v]) - {int(BIG)}"}
# the same 40,000 rows as an ungrouped scan: chunks [0, 16384) (2**54),
# [16384, 32768) and [32768, 40000)
CHUNKED = 16384 + 7232


def test_a_group_of_a_bit_packed_group_column_adds_row_by_row():
    # every one added to 2**54 on its own rounds away
    got = de.evaluate_measures_batch(["m"], _tables(False), M, {"T.g": de.GroupByValues([0])})
    assert got["m"] == 0
    # the same rows under a filter: an ungrouped scan
    got = de.evaluate_measures_batch(["m"], _tables(False), M, {"T.g": [0]})
    assert got["m"] == CHUNKED


def test_a_group_of_an_rle_group_column_adds_as_a_scan():
    got = de.evaluate_measures_batch(["m"], _tables(True), M, {"T.g": de.GroupByValues([0])})
    assert got["m"] == CHUNKED


def test_through_a_relationship_and_an_iteration():
    tables = _tables(False)
    ms = {"grouped": f"SUM(T[v]) - {int(BIG)}",
          "iterated": f"SUMX(VALUES(D[g]), CALCULATE(SUM(T[v]))) - {int(BIG)}",
          "filtered": f"CALCULATE(SUM(T[v]), D[g] = 0) - {int(BIG)}"}
    got = de.evaluate_measures_batch(["grouped"], tables, ms, {"D.g": de.GroupByValues([0])},
                                     relationships=RELS)
    assert got["grouped"] == 0                # gb_chname: grouped through the join column
    got = de.evaluate_measures_batch(["iterated", "filtered"], tables, ms, {}, relationships=RELS)
    assert got["iterated"] == 0               # gb_sumx_emp: each member a group
    assert got["filtered"] == CHUNKED         # af_chname: a filter, an ungrouped scan


def test_the_grouped_tool_groups():
    tables = _tables(False)
    got = de.evaluate_measures_batch(["m"], tables, M, {"T.g": [0]}, group_keys={"T.g"})
    assert got["m"] == 0


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    # gb_ch: SUMMARIZECOLUMNS(FactSales[channelKey], "v", VAR s = SUM(SA) RETURN s - TRUNC(s))
    gb_ch = {1: "0.541100263595581", 2: "0.54379940032959", 3: "0.17840001732111", 4: "0.952100068330765"}
    # gb_store, a few of the 306 stores, among them the seven of more than 7,000 rows
    gb_store = {199: "0.133700042963028", 200: "0.17840001732111", 306: "0.0287999957799911",
                307: "0.38129997998476", 308: "0.647799998521805", 309: "0.243399940431118",
                310: "0.0609000027179718"}
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d3ag"))["success"]
    try:
        ctx = S._get_dax_context("d3ag")
        ctx["measure_defs"]["__frac"] = "VAR s = SUM(FactSales[SalesAmount]) RETURN s - TRUNC(s)"
        contexts = ([{"filter_context": {"FactSales.channelKey": [k]}, "group_by": ["FactSales.channelKey"]}
                     for k in gb_ch]
                    + [{"filter_context": {"FactSales.StoreKey": [k]}, "group_by": ["FactSales.StoreKey"]}
                       for k in gb_store])
        out = json.loads(S.pbix_evaluate_dax_batch("d3ag", "__frac", json.dumps(contexts)))
    finally:
        S.pbix_close("d3ag")
    got = [r["results"][0]["value"] for r in out["results"]]
    for v, want in zip(got, list(gb_ch.values()) + list(gb_store.values())):
        assert float(f"{v:.15g}") == float(want), (v, want)
