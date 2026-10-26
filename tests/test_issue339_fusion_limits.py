"""Issue #339: which aggregations Desktop's storage engine scans
together -- the limits build_b243c.py measured.

What was wrong (Power BI Desktop 2.152, build_b243c.py, Agents FactSales;
SUM(SalesAmount) - 729782919 alone is 0.215398073196411, cut at TotalCost's
runs 0.215394020080566):
- COUNT(TotalCost) beside SUM(SalesAmount) reads TotalCost in the same scan
  (f_count 0.215394020080566); it was left out. MIN, MAX and DISTINCTCOUNT
  beside it do not cut (f_min, f_max, f_dc), as before.
- IF(1 = 1, SUM(SA), SUM(TC)) and IF(1 = 0, SUM(TC), SUM(SA)) scan the sum
  alone (f_if_true, f_if_false 0.215398073196411): a constant condition
  leaves the other branch unscanned; both branches were read.
- The sums in SELECTEDVALUE's alternate result are scanned together, inline
  (s_sel_alt 0.445394039154053 = fu_sa_tc) and through measures (s_sel_meas
  0.111997961997986 = fu_meas); SELECTEDVALUE was not looked into.
- The extension columns of one SUMMARIZECOLUMNS are one scan: channel 1's
  SUM(SA) beside SUM(TC) (f_sc_two) is 0.541100740432739, alone (gb_ch)
  0.541100263595581; an extension column opened no scope at all.

Where the expected values come from: Desktop's answers (the Agents file,
skipped when the community corpus is not present), and on hand-made layouts
exact IEEE arithmetic of the storage-engine rule: T[v] is 2**54 then ones, one
bit-packed run, so v's sum minus 2**54 counts the ones outside the first
chunk of its first piece -- 23,616 scanned alone, 30,000 cut at T[w]'s run
boundary at row 10,000.
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
    # g: one RLE run (one group)
    rows = [[1.0, 1.0 if i < 10000 else 2.0, 0] for i in range(N)]
    rows[0][0] = BIG
    t = {"columns": ["v", "w", "g"], "rows": rows,
         "storage": _layout(N, {"v": [(0, False)], "w": [(0, True), (10000, True)], "g": [(0, True)]})}
    return {"T": t, "S": {"columns": ["s"], "rows": [["a"], ["b"]]}}


def _eval(measures: dict, name: str):
    return de.evaluate_measures_batch([name], _tables(), measures, {})[name]


def test_count_beside_a_sum_is_scanned_with_it():
    assert _eval({"m": f"SUM(T[v]) + COUNT(T[w]) * 0 - {B}"}, "m") == CUT
    for other in ("MIN", "MAX", "DISTINCTCOUNT", "COUNTROWS"):
        arg = "T" if other == "COUNTROWS" else "T[w]"
        assert _eval({"m": f"SUM(T[v]) + {other}({arg}) * 0 - {B}"}, "m") == ALONE, other


def test_a_constant_condition_scans_one_branch():
    assert _eval({"m": f"IF(1 = 1, SUM(T[v]), SUM(T[w])) - {B}"}, "m") == ALONE
    assert _eval({"m": f"IF(1 = 0, SUM(T[w]), SUM(T[v])) - {B}"}, "m") == ALONE
    assert _eval({"m": f"IF(TRUE(), SUM(T[v]) + SUM(T[w]) * 0) - {B}"}, "m") == CUT


def test_selectedvalues_alternate_result_is_scanned_together():
    ms = {"inline": f"SELECTEDVALUE(S[s], SUM(T[v]) + SUM(T[w]) * 0) - {B}",
          "sv": "SUM(T[v])", "sw": "SUM(T[w])",
          "meas": f"SELECTEDVALUE(S[s], [sv] + [sw] * 0) - {B}"}
    assert _eval(ms, "inline") == CUT
    assert _eval(ms, "meas") == CUT


def test_summarizecolumns_extension_columns_are_one_scan():
    one = f'SUMMARIZECOLUMNS(T[g], "v", SUM(T[v]) - {B})'
    two = f'SUMMARIZECOLUMNS(T[g], "v", SUM(T[v]) - {B}, "w", SUM(T[w]))'
    inline = f'SUMMARIZECOLUMNS(T[g], "v", SUM(T[v]) + SUM(T[w]) * 0 - {B})'
    assert _eval({"m": f"MAXX({one}, [v])"}, "m") == ALONE
    assert _eval({"m": f"MAXX({two}, [v])"}, "m") == CUT
    assert _eval({"m": f"MAXX({inline}, [v])"}, "m") == CUT


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    sa, tc = "FactSales[SalesAmount]", "FactSales[TotalCost]"
    sc = (f'SUMMARIZECOLUMNS(FactSales[channelKey], "v", VAR s = SUM({sa}) RETURN s - TRUNC(s), '
          f'"w", VAR t = SUM({tc}) RETURN t - TRUNC(t))')
    want = {  # build_b243c.py
        "f_count": (f"SUM({sa}) + COUNT({tc}) * 0 - 729782919", "0.215394020080566"),
        "f_dc": (f"SUM({sa}) + DISTINCTCOUNT({tc}) * 0 - 729782919", "0.215398073196411"),
        "f_if_true": (f"IF(1 = 1, SUM({sa}), SUM({tc})) - 729782919", "0.215398073196411"),
        "f_if_false": (f"IF(1 = 0, SUM({tc}), SUM({sa})) - 729782919", "0.215398073196411"),
        "s_sel_alt": (f"SELECTEDVALUE(DimChannel[ChannelName], SUM({sa}) + SUM({tc})) - 1044948443",
                      "0.445394039154053"),
        "s_sel_meas": ("SELECTEDVALUE(DimChannel[ChannelName], [Total Sales] + [Total Discount]) - 741839272",
                       "0.111997961997986"),
        # f_sc_two's rows, the query evaluated as one
        "f_sc_two_v1": (f"MAXX(FILTER({sc}, FactSales[channelKey] = 1), [v])", "0.541100740432739"),
        "f_sc_two_w1": (f"MAXX(FILTER({sc}, FactSales[channelKey] = 1), [w])", "0.479999989271164"),
        "f_sc_two_v4": (f"MAXX(FILTER({sc}, FactSales[channelKey] = 4), [v])", "0.952100068330765"),
    }
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d2lim"))["success"]
    try:
        ctx = S._get_dax_context("d2lim")
        for k, (e, _w) in want.items():
            ctx["measure_defs"]["__" + k] = e
        got = json.loads(S.pbix_evaluate_dax("d2lim", ",".join("__" + k for k in want),
                                             apply_default_filters=False))
    finally:
        S.pbix_close("d2lim")
    for r, (k, (_e, w)) in zip(got["results"], want.items()):
        assert float(f"{r['value']:.15g}") == float(w), (k, r["value"], w)
