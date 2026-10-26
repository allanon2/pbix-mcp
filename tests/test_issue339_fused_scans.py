"""Issue #339: aggregations Desktop's storage engine scans together
cut each other's pieces.

What was wrong: every aggregation was evaluated on its own. Power BI Desktop
2.152 scans the aggregations of one table under one filter context in one
evaluation together (build_b243b.py, Agents FactSales): SUM(SalesAmount) +
SUM(TotalCost) cuts each sum at the other column's run boundaries too
(fu_sa_tc 0.445394039154053; two scans give ...398...), and so do the same
sums through measures (fu_meas), an Int64 SUM beside (fu_sa_sq), SUMX(SA - TC)
beside SUM(DiscountAmount) (fu_prof_da), the census's DIVIDE([Profit], [Total
Sales]) (fu_pm 74.9400541621981) and SUM(SA) - SUM(TC) (a_profit_split
0.985394060611725). COUNTROWS reads no column (fu_sa_cnt), and a SUM under
another filter is another scan (fu_sa_tc_f 0.445398092269897). The engine now
reads, per evaluation, which aggregations share a scan (DAXEngine._se_scope).

The scope is read with the home table of the measure (SUM([v]) in a measure
of T is SUM(T[v])), and a nested measure's memo entry is keyed by what of the
scope it inherits can change its value: read without the home table, the
scope missed SUM([v]), and a measure memoized under a fused scan was handed
to a later evaluation of it on its own (X = SUM([v]): Xm = [X] - 2**54 was
10000 after Y = [X] + SUM(T[w]) - 2**54, and Y was 3616 after Xm). A
measure evaluated where the scope in force does not hold (TOTALYTD's dates)
opens its own, as CALCULATE does: TOTALYTD(e, d) is CALCULATE(e,
DATESYTD(d)) in Microsoft's DAX reference.

Where the expected values come from: Desktop's answers by probe id (the
Agents file, skipped when the community corpus is not present), and on
hand-made layouts exact IEEE arithmetic of the rule (2**54 then ones: the
ones a chunk adds after 2**54 round away, so the answer counts the ones of
the chunks that start without it).
"""
from __future__ import annotations

import itertools
import json
import os
import warnings
from array import array
from datetime import datetime, timedelta

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


def _table(n: int = 20000) -> dict:
    # v: one bit-packed run, 2**54 then ones; w: two RLE runs of 0.0, the
    # second from row 10000
    rows = [[1.0, 0.0] for _ in range(n)]
    rows[0][0] = BIG
    return {"columns": ["v", "w"], "rows": rows,
            "storage": _layout(n, {"v": [(0, False)], "w": [(0, True), (10000, True)]})}


def _batch(measures: dict, tables: dict) -> dict:
    return de.evaluate_measures_batch(list(measures), tables, dict(measures), {})


def test_two_sums_of_one_table_cut_each_other():
    got = _batch({"alone": f"SUM(T[v]) - {int(BIG)}",
                  "fused": f"SUM(T[v]) + SUM(T[w]) - {int(BIG)}",
                  "divided": f"DIVIDE(SUM(T[v]) - {int(BIG)}, SUM(T[w]) + 1)",
                  "var": f"VAR a = SUM(T[v]) VAR b = SUM(T[w]) RETURN a + b - {int(BIG)}"},
                 {"T": _table()})
    # alone: chunks [0, 16384) (2**54) and [16384, 20000) (3616)
    assert got["alone"] == 3616
    # with w: cut at 10000 too, [0, 10000) and [10000, 20000)
    assert got["fused"] == got["divided"] == got["var"] == 10000


def test_through_measures_and_sumx():
    ms = {"V": "SUM(T[v])", "W": "SUM(T[w])", "both": f"[V] + [W] - {int(BIG)}",
          "x": f"SUMX(T, T[v] * 1) + [W] - {int(BIG)}", "V alone": f"[V] - {int(BIG)}"}
    got = _batch(ms, {"T": _table()})
    assert got["both"] == got["x"] == 10000          # fu_meas, fu_prof_da
    assert got["V alone"] == 3616                    # its own scan: the memo keeps them apart


def test_countrows_and_another_filter_do_not_fuse():
    n = 20000
    got = _batch({"cnt": f"SUM(T[v]) + COUNTROWS(T) - {int(BIG) + n}",
                  "apart": f"SUM(T[v]) + CALCULATE(SUM(T[w]), T[w] = 0) - {int(BIG)}",
                  "inner": f"CALCULATE(SUM(T[v]) + SUM(T[w]), T[w] = 0) - {int(BIG)}"},
                 {"T": _table(n)})
    assert got["cnt"] == 3616          # fu_sa_cnt: COUNTROWS reads no column
    assert got["apart"] == 3616        # fu_sa_tc_f: another filter, another scan
    assert got["inner"] == 10000       # one CALCULATE, one scan (w is also its filter)



def test_a_bare_column_of_the_home_table_is_read_in_the_scope():
    # SUM([v]) in a measure of T is SUM(T[v]); the scope read it as nothing
    b = int(BIG)
    ms = {"bare": f"SUM([v]) + SUM([w]) - {b}", "qualified": f"SUM(T[v]) + SUM(T[w]) - {b}",
          "bare alone": f"SUM([v]) - {b}"}
    got = de.evaluate_measures_batch(list(ms), {"T": _table()}, dict(ms), {},
                                     measure_tables={k: "T" for k in ms})
    assert got == {"bare": 10000, "qualified": 10000, "bare alone": 3616}


def test_the_measure_memo_does_not_depend_on_the_order():
    b = int(BIG)
    ms = {"X": "SUM([v])", "XW": f"SUM([v]) + SUM([w]) - {b}",
          "Xm": f"[X] - {b}",                       # a lone sum: its own scan (a_total)
          "Y": f"[X] + SUM(T[w]) - {b}",            # X's sum fused with w's (fu_meas)
          "Nest": "[Y] + [Xm]",                     # one scan of v and w: both cut
          "SelXW": "SELECTEDVALUE(G[g], [XW])",     # no group: XW, its own scope
          "Mix": f"[X] + [Ux] - {b}"}               # U's sum does not cut T's
    ms["Ux"] = "SUM(U[x])"
    want = {"Xm": 3616, "Y": 10000, "Nest": 20000, "SelXW": 10000, "Mix": 3716}
    homes = {k: "T" for k in ms}
    homes["Ux"] = homes["Mix"] = "U"
    tables = {"T": _table(), "U": {"columns": ["x"], "rows": [[1.0]] * 100},
              "G": {"columns": ["g"], "rows": [[0], [1]]}}
    for order in itertools.permutations(want):
        de._SHARED_FILTER_CACHES.clear()
        got = de.evaluate_measures_batch(list(order), tables, dict(ms), {}, measure_tables=homes)
        assert got == want, order
    # and pairwise, each pair in both orders on a fresh model cache
    for a, c in itertools.permutations(want, 2):
        de._SHARED_FILTER_CACHES.clear()
        got = de.evaluate_measures_batch([a, c], tables, dict(ms), {}, measure_tables=homes)
        assert got == {a: want[a], c: want[c]}, (a, c)


def test_totalytd_scans_as_calculate_with_datesytd():
    # TOTALYTD(e, d) is CALCULATE(e, DATESYTD(d)) (Microsoft's DAX
    # reference): the sums of its expression are one scan, as CALCULATE's
    b = int(BIG)
    n = 20000
    t = _table(n)
    d0 = datetime(2024, 1, 1)
    t["columns"].append("Date")
    for i, r in enumerate(t["rows"]):
        r.append(d0 + timedelta(days=i % 300))
    t["storage"]["columns"]["Date"] = {"starts": array("q", [0]), "rle": b"\x00", "enc": "hash", "mag": 1.0}
    cal = {"columns": ["Date"], "rows": [[d0 + timedelta(days=i)] for i in range(366)]}
    rels = [{"FromTable": "T", "FromColumn": "Date", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"XW": f"SUM([v]) + SUM([w]) - {b}",
          "ytd": "TOTALYTD([XW], Cal[Date])", "cal": "CALCULATE([XW], DATESYTD(Cal[Date]))",
          "ytd inline": f"TOTALYTD(SUM(T[v]) + SUM(T[w]), Cal[Date]) - {b}",
          "cal inline": f"CALCULATE(SUM(T[v]) + SUM(T[w]), DATESYTD(Cal[Date])) - {b}"}
    last = {"Cal.Date": [d0 + timedelta(days=299)]}       # every row up to it: all of T
    for order in (list(ms)[1:], list(ms)[:0:-1]):
        de._SHARED_FILTER_CACHES.clear()
        got = de.evaluate_measures_batch(order, {"T": t, "Cal": cal}, dict(ms), last,
                                         measure_tables={k: "T" for k in ms}, relationships=rels)
        assert got == {"ytd": 10000, "cal": 10000, "ytd inline": 10000, "cal inline": 10000}, order

@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    exprs = {
        "fu_sa_tc": ("SUM(FactSales[SalesAmount]) + SUM(FactSales[TotalCost]) - 1044948443",
                     "0.445394039154053"),
        "fu_meas": ("[Total Sales] + [Total Discount] - 741839272", "0.111997961997986"),
        "fu_sa_cnt": ("SUM(FactSales[SalesAmount]) + COUNTROWS(FactSales) - 729982918",
                      "0.215398073196411"),
        "fu_sa_tc_f": ("SUM(FactSales[SalesAmount]) + CALCULATE(SUM(FactSales[TotalCost]), "
                       "FactSales[StoreKey] <> 0) - 1044948443", "0.445398092269897"),
        "a_profit_split": ("SUM(FactSales[SalesAmount]) - SUM(FactSales[TotalCost]) - 414617394",
                           "0.985394060611725"),
        "fu_pm": ("(DIVIDE([Profit], [Total Sales]) - 0.568137981951) * 1E15", "74.9400541621981"),
    }
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d2ag"))["success"]
    try:
        ctx = S._get_dax_context("d2ag")
        for k, (e, _w) in exprs.items():
            ctx["measure_defs"]["__" + k] = e
        out = json.loads(S.pbix_evaluate_dax("d2ag", ",".join("__" + k for k in exprs),
                                             apply_default_filters=False))
    finally:
        S.pbix_close("d2ag")
    got = {x["name"][2:]: x.get("value") for x in out["results"]}
    for k, (_e, want) in exprs.items():
        assert float(f"{got[k]:.15g}") == float(want), f"{k}: {got[k]!r}, Desktop {want}"
