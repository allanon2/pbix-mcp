"""Issue #339, build_b243d.py: which aggregations Desktop's storage
engine scans together -- the limits the fourth round measured.

What was wrong (rel148; Power BI Desktop 2.152, build_b243d.py, the
Desktop-saved Agents file; SUM(SalesAmount) - 729782919 alone is
0.215398073196411, cut at TotalCost's runs 0.215394020080566):
- COUNTA(TotalCost) beside SUM(SalesAmount) reads TotalCost in the scan, as
  COUNT does (f_counta 0.215394020080566); it was left out.
- MIN / MAX(TotalCost) beside it under a filter read TotalCost too (f_min_f,
  f_max_f: CALCULATE(..., FactSales[StoreKey] <> 0) is 0.215400099754333, cut
  at StoreKey and TotalCost); without a filter they do not (build_b243c.py
  f_min, f_max: a column statistic answers them), and DISTINCTCOUNT does not
  under a filter either (f_dc_f 0.215399503707886, cut at StoreKey alone).
- An IF whose condition reads the data scans its branch on its own:
  IF([_ShowValueForDates], SUM(SA), SUM(TC)) (f_if_dyn), IF(SUM(DA) > 0,
  SUM(SA), 0) (f_if_cond) and SUM(DA) * 0 + IF([_ShowValueForDates], SUM(SA),
  0) (f_if_sib) are all the sum alone, 0.215398073196411; both branches, the
  condition and the sum beside were read with it.
- The columns of one ROW are one query: ROW("v", [Total Sales] - C, "w",
  [Total Discount] - C) gives v 0.215398192405701, cut at DiscountAmount's
  runs (f_row_two); each was scanned alone.
- ADDCOLUMNS' extension columns of one row are scanned together where they
  transition it: ADDCOLUMNS(VALUES(FactSales[channelKey]), "v",
  CALCULATE(SUM(SA)) ..., "w", CALCULATE(SUM(TC)) ...) gives channel 1's v
  0.541100740432739 (f_addcols, = build_b243c.py f_sc_two); each was a scan
  of its own (0.541100263595581).

Where the expected values come from: Desktop's answers (the Agents test skips
without the community corpus), and on hand-made layouts exact IEEE arithmetic
of the storage-engine rule: T[v] is 2**54 then ones, one bit-packed run, so
v's sum minus 2**54 counts the ones outside the first chunk of its first
piece -- 23,616 scanned alone, 30,000 cut at T[w]'s run boundary at row
10,000.
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
ALONE, CUT = 23616, 30000
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


def _tables() -> dict:
    # v: 2**54 then ones (bit-packed); w: 1.0 / 2.0 cut at row 10000 (RLE);
    # g: one RLE run (one group, a filter that cuts nothing)
    rows = [[1.0, 1.0 if i < 10000 else 2.0, 0] for i in range(N)]
    rows[0][0] = BIG
    t = {"columns": ["v", "w", "g"], "rows": rows,
         "storage": _layout(N, {"v": [(0, False)], "w": [(0, True), (10000, True)], "g": [(0, True)]})}
    return {"T": t}


def _eval(measures: dict, name: str):
    return de.evaluate_measures_batch([name], _tables(), measures, {})[name]


def test_counta_beside_a_sum_is_scanned_with_it():
    assert _eval({"m": f"SUM(T[v]) + COUNTA(T[w]) * 0 - {B}"}, "m") == CUT


def test_min_and_max_are_scanned_with_it_under_a_filter():
    for fn in ("MIN", "MAX"):
        assert _eval({"m": f"CALCULATE(SUM(T[v]) + {fn}(T[w]) * 0, T[g] = 0) - {B}"}, "m") == CUT, fn
        # no filter: a column statistic answers them (build_b243c.py f_min)
        assert _eval({"m": f"SUM(T[v]) + {fn}(T[w]) * 0 - {B}"}, "m") == ALONE, fn
    assert _eval({"m": f"CALCULATE(SUM(T[v]) + DISTINCTCOUNT(T[w]) * 0, T[g] = 0) - {B}"}, "m") == ALONE


def test_an_if_whose_condition_reads_the_data_scans_its_branch_alone():
    ms = {"dyn": f"IF(COUNTROWS(T) > 0, SUM(T[v]), SUM(T[w])) - {B}",
          "cond": f"IF(SUM(T[w]) > 0, SUM(T[v]), 0) - {B}",
          "sib": f"SUM(T[w]) * 0 + IF(COUNTROWS(T) > 0, SUM(T[v]), 0) - {B}",
          # the branch's own sums are still one scan
          "inside": f"IF(COUNTROWS(T) > 0, SUM(T[v]) + SUM(T[w]) * 0) - {B}"}
    got = de.evaluate_measures_batch(list(ms), _tables(), ms, {})
    assert got == {"dyn": ALONE, "cond": ALONE, "sib": ALONE, "inside": CUT}


def test_the_columns_of_one_row_are_one_scan():
    two = f'ROW("v", SUM(T[v]) - {B}, "w", SUM(T[w]))'
    one = f'ROW("v", SUM(T[v]) - {B})'
    ms = {"sv": "SUM(T[v])", "sw": "SUM(T[w])",
          "two": f"MAXX({two}, [v])", "one": f"MAXX({one}, [v])",
          "meas": f'MAXX(ROW("v", [sv] - {B}, "w", [sw]), [v])'}
    got = de.evaluate_measures_batch(["two", "one", "meas"], _tables(), ms, {})
    assert got == {"two": CUT, "one": ALONE, "meas": CUT}


def test_addcolumns_extension_columns_of_one_row_are_one_scan():
    two = f'ADDCOLUMNS(VALUES(T[g]), "v", CALCULATE(SUM(T[v])) - {B}, "w", CALCULATE(SUM(T[w])))'
    one = f'ADDCOLUMNS(VALUES(T[g]), "v", CALCULATE(SUM(T[v])) - {B})'
    ms = {"sv": "SUM(T[v])", "sw": "SUM(T[w])",
          "two": f"MAXX({two}, [v])", "one": f"MAXX({one}, [v])",
          "meas": f'MAXX(ADDCOLUMNS(VALUES(T[g]), "v", [sv] - {B}, "w", [sw]), [v])'}
    got = de.evaluate_measures_batch(["two", "one", "meas"], _tables(), ms, {})
    assert got == {"two": CUT, "one": ALONE, "meas": CUT}


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    sa, tc, da = "FactSales[SalesAmount]", "FactSales[TotalCost]", "FactSales[DiscountAmount]"
    ac = (f'ADDCOLUMNS(VALUES(FactSales[channelKey]), "v", VAR s = CALCULATE(SUM({sa})) RETURN s - TRUNC(s), '
          f'"w", VAR t = CALCULATE(SUM({tc})) RETURN t - TRUNC(t))')
    rw = 'ROW("v", [Total Sales] - 729782919, "w", [Total Discount] - 12056352)'
    want = {  # build_b243d.py, desk_b243d_agents.json
        "f_counta": (f"SUM({sa}) + COUNTA({tc}) * 0 - 729782919", "0.215394020080566"),
        "f_min_f": (f"CALCULATE(SUM({sa}) + MIN({tc}) * 0, FactSales[StoreKey] <> 0) - 729782919",
                    "0.215400099754333"),
        "f_max_f": (f"CALCULATE(SUM({sa}) + MAX({tc}) * 0, FactSales[StoreKey] <> 0) - 729782919",
                    "0.215400099754333"),
        "f_dc_f": (f"CALCULATE(SUM({sa}) + DISTINCTCOUNT({tc}) * 0, FactSales[StoreKey] <> 0) - 729782919",
                   "0.215399503707886"),
        "f_if_dyn": (f"IF([_ShowValueForDates], SUM({sa}), SUM({tc})) - 729782919", "0.215398073196411"),
        "f_if_cond": (f"IF(SUM({da}) > 0, SUM({sa}), 0) - 729782919", "0.215398073196411"),
        "f_if_sib": (f"SUM({da}) * 0 + IF([_ShowValueForDates], SUM({sa}), 0) - 729782919",
                     "0.215398073196411"),
        # the ROW and the ADDCOLUMNS evaluated as one query each
        "f_row_two_v": (f"MAXX({rw}, [v])", "0.215398192405701"),
        "f_row_two_w": (f"MAXX({rw}, [w])", "0.896599821746349"),
        "f_addcols_v1": (f"MAXX(FILTER({ac}, FactSales[channelKey] = 1), [v])", "0.541100740432739"),
        "f_addcols_w1": (f"MAXX(FILTER({ac}, FactSales[channelKey] = 1), [w])", "0.479999989271164"),
    }
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d2b243d"))["success"]
    try:
        ctx = S._get_dax_context("d2b243d")
        for k, (e, _w) in want.items():
            ctx["measure_defs"]["__" + k] = e
        got = json.loads(S.pbix_evaluate_dax("d2b243d", ",".join("__" + k for k in want),
                                             apply_default_filters=False))
    finally:
        S.pbix_close("d2b243d")
    for r, (k, (_e, w)) in zip(got["results"], want.items()):
        assert float(f"{r['value']:.15g}") == float(w), (k, r["value"], w)
