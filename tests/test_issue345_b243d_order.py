"""Issue #345, build_b243d.py: what decides the order in which SUMX /
AVERAGEX add the values the formula engine computes over one column's values.

What was wrong (rel148, the third round's rule):
- inside an IF branch the members added in data-ID order with BLANK last;
  Desktop puts the blank member FIRST (its data ID is the lowest);
- an IF whose condition is a constant counted as a branch, and a SWITCH
  branch did not;
- every FILTER of a column's values added in data-ID order, also where its
  condition reads nothing but the column;
- VALUES of a hash-encoded column with a measure kept the rows' order.

Power BI Desktop 2.152 (build_b243d.py, the Desktop-saved Agents file, no
refresh; each probe minus a constant so that 15 digits pin one double):
- i_sumx_if IF([_ShowValueForDates], SUMX(ALLSELECTED(DimEmployee[EmployeeKey]),
  [MTD Total Sales])) - 19260876 = 0.561099998652935: ascending keys with the
  blank member first (blank last 0.561100002378225, storage order
  0.561100009828806); i_avgx_if, i_values_if, i_values_else, i_pm_sumx_if
  likewise; i_switch (SWITCH(TRUE(), [_ShowValueForDates], ...)) is
  i_sumx_if's value;
- i_avgx_iftrue IF(1 = 1, [Employees Avg MTD Sales]) - 73514 =
  0.796034732862609, storage order: a constant condition is no branch;
- v_key_filter_t FILTER(VALUES(key), TRUE()) 0.797591596841812 and
  v_key_filter_k FILTER(VALUES(key), NOT ISBLANK(key)) 0.655902370810509:
  storage order, as VALUES itself;
- h_store_values SUMX(VALUES(DimStore[StoreName]), [MTD Total Sales]) -
  19260876 = 0.561099994927645, the dictionary's order (= its FILTER,
  h_store_filter); h_values_meas = h_filter_meas on DimEmployee[LastName]
  0.381036639213562 (dictionary order, blank member first); h_up_meas.

Expected values: Desktop's answers (the Agents test skips without the
community corpus), and on hand-made tables exact IEEE arithmetic: with
members 1, 2**53 and -2**53, 1 + 2**53 rounds to 2**53 (ties to even), so
the order 1, 2**53, -2**53 adds to 0 and the order 2**53, -2**53, 1 to 1.
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

P = float(2 ** 53)


def _col(starts, rle, enc="hash", **extra) -> dict:
    return {"starts": array("q", starts), "rle": bytes(rle), "enc": enc, "mag": 1.0, **extra}


def _model():
    # D's value-encoded key stored 2, 1; F[v] per key: 1 -> 2**53, 2 -> -2**53,
    # and the orphan key 9 (D's blank member) -> 1. Data-ID order, blank
    # first: 1 + 2**53 - 2**53 = 0; blank last: 2**53 - 2**53 + 1 = 1; the
    # storage order 2, 1, blank: -2**53 + 2**53 + 1 = 1.
    d = {"columns": ["k"], "rows": [[2], [1]],
         "storage": {"rows": 2, "segments": (2,), "columns": {"k": _col([0], [0], "value")}}}
    f = {"columns": ["k", "v"], "rows": [[2, -P], [1, P], [9, 1.0]]}
    rels = [{"FromTable": "F", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    return {"D": d, "F": f}, rels


MS = {"S": "SUM(F[v])",
      "values": "SUMX(VALUES(D[k]), [S])",
      "in_if": "IF(COUNTROWS(F) > 0, SUMX(VALUES(D[k]), [S]))",
      "in_if_avg": "IF(COUNTROWS(F) > 0, AVERAGEX(ALLSELECTED(D[k]), [S])) * 3",
      "in_switch": "SWITCH(TRUE(), COUNTROWS(F) > 0, SUMX(VALUES(D[k]), [S]))",
      "in_const_if": "IF(1 = 1, SUMX(VALUES(D[k]), [S]))",
      "filter_meas": "SUMX(FILTER(VALUES(D[k]), NOT ISBLANK([S])), [S])",
      "filter_true": "SUMX(FILTER(VALUES(D[k]), TRUE()), [S])",
      "filter_key": "SUMX(FILTER(VALUES(D[k]), NOT ISBLANK(D[k])), [S])"}


def _eval(names):
    tables, rels = _model()
    return de.evaluate_measures_batch(list(names), tables, MS, {}, relationships=rels)


def test_values_keep_storage_order_with_the_blank_member_last():
    assert _eval(["values"])["values"] == 1.0


def test_inside_an_if_or_switch_branch_the_blank_member_comes_first():
    got = _eval(["in_if", "in_if_avg", "in_switch"])
    assert got == {"in_if": 0.0, "in_if_avg": 0.0, "in_switch": 0.0}


def test_an_if_with_a_constant_condition_is_no_branch():
    # D's keys stored 3, 1, 2 worth -2**53, 2**53, 1 and no blank member:
    # storage order 1, ascending 0
    d = {"columns": ["k"], "rows": [[3], [1], [2]],
         "storage": {"rows": 3, "segments": (3,), "columns": {"k": _col([0], [0], "value")}}}
    f = {"columns": ["k", "v"], "rows": [[3, -P], [1, P], [2, 1.0]]}
    rels = [{"FromTable": "F", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    got = de.evaluate_measures_batch(["in_const_if", "in_if"], {"D": d, "F": f}, MS, {}, relationships=rels)
    assert got == {"in_const_if": 1.0, "in_if": 0.0}


def test_a_filter_of_the_column_alone_keeps_the_order_it_filters():
    got = _eval(["filter_true", "filter_meas"])
    assert got["filter_true"] == 1.0
    # a condition that reads a measure: data-ID order, blank first
    assert got["filter_meas"] == 0.0
    # a condition of the key alone, which drops the blank member: D's keys
    # stored 3, 1, 2 worth -2**53, 2**53, 1 -- storage order -2**53 + 2**53 +
    # 1 = 1, ascending 2**53 + 1 - 2**53 = 0
    d = {"columns": ["k"], "rows": [[3], [1], [2]],
         "storage": {"rows": 3, "segments": (3,), "columns": {"k": _col([0], [0], "value")}}}
    f = {"columns": ["k", "v"], "rows": [[3, -P], [1, P], [2, 1.0]]}
    rels = [{"FromTable": "F", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    got = de.evaluate_measures_batch(["filter_key", "filter_meas"], {"D": d, "F": f}, MS, {},
                                     relationships=rels)
    assert got == {"filter_key": 1.0, "filter_meas": 0.0}


def test_a_hash_encoded_column_with_a_measure_adds_in_dictionary_order_blank_first():
    # D[n] hash-encoded, stored "b", "a", its dictionary "a", "b"; F rows
    # per name: "a" -> 2**53, "b" -> -2**53, an orphan (blank member) -> 1.
    # Dictionary order, blank first: 1 + 2**53 - 2**53 = 0; the rows' order
    # "b", "a", blank: 1; blank last: 1.
    d = {"columns": ["n"], "rows": [["b"], ["a"]],
         "storage": {"rows": 2, "segments": (2,), "columns": {
             "n": _col([0], [0], dict=["a", "b"], dtype="String")}}}
    f = {"columns": ["n", "v"], "rows": [["b", -P], ["a", P], ["zz", 1.0]]}
    rels = [{"FromTable": "F", "FromColumn": "n", "ToTable": "D", "ToColumn": "n", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"S": "SUM(F[v])", "m": "SUMX(VALUES(D[n]), [S])"}
    got = de.evaluate_measures_batch(["m"], {"D": d, "F": f}, ms, {}, relationships=rels)
    assert got["m"] == 0.0


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    ek = "DimEmployee[EmployeeKey]"
    want = {  # build_b243d.py, desk_b243d_agents.json
        "i_sumx_if": (f"IF([_ShowValueForDates], SUMX(ALLSELECTED({ek}), [MTD Total Sales])) - 19260876",
                      "0.561099998652935"),
        "i_avgx_if": ("IF([_ShowValueForDates], [Employees Avg MTD Sales]) - 73514", "0.796034732818953"),
        "i_avgx_iftrue": ("IF(1 = 1, [Employees Avg MTD Sales]) - 73514", "0.796034732862609"),
        "i_values_if": (f"IF([_ShowValueForDates], SUMX(VALUES({ek}), [MOMTD % Total Sales]) * 1E6) - 92500633",
                        "0.797591686248779"),
        "i_switch": (f"SWITCH(TRUE(), [_ShowValueForDates], SUMX(ALLSELECTED({ek}), [MTD Total Sales])) "
                     "- 19260876", "0.561099998652935"),
        "i_var_if": (f"VAR x = SUMX(ALLSELECTED({ek}), [MTD Total Sales]) RETURN IF([_ShowValueForDates], x) "
                     "- 19260876", "0.561100009828806"),
        "h_values_meas": ("SUMX(VALUES(DimEmployee[LastName]), [MOMTD % Total Sales]) * 1E6 - 91405906",
                          "0.381036639213562"),
        "h_store_values": ("SUMX(VALUES(DimStore[StoreName]), [MTD Total Sales]) - 19260876",
                           "0.561099994927645"),
        "h_up_meas": ("SUMX(VALUES(FactSales[UnitPrice]), [Total Sales]) - 729782919", "0.215399980545044"),
        "v_key_filter_t": (f"SUMX(FILTER(VALUES({ek}), TRUE()), [MOMTD % Total Sales]) * 1E6 - 92500633",
                           "0.797591596841812"),
        "v_key_filter_k": (f"SUMX(FILTER(VALUES({ek}), NOT ISBLANK({ek})), [MOMTD % Total Sales]) * 1E6 "
                           "- 93163158", "0.655902370810509"),
    }
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d9b243d"))["success"]
    try:
        ctx = S._get_dax_context("d9b243d")
        for k, (e, _w) in want.items():
            ctx["measure_defs"]["__" + k] = e
        got = json.loads(S.pbix_evaluate_dax("d9b243d", ",".join("__" + k for k in want),
                                             apply_default_filters=False))
    finally:
        S.pbix_close("d9b243d")
    for r, (k, (_e, w)) in zip(got["results"], want.items()):
        assert float(f"{r['value']:.15g}") == float(w), (k, r["value"], w)
