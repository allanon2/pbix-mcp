"""Issue #345: the order in which SUMX / AVERAGEX add the values the
formula engine computes over one column's values.

What was wrong: they were always added in the order the table came in -- a
column's values in storage order, first appearance. Power BI Desktop 2.152
(build_b243c.py, Agents):
- over FILTER(VALUES(DimEmployee[EmployeeKey]), ...) the members add in
  ascending key order -- the value-encoded column's data-ID order
  (o_filter_sumx 0.655902430415154, o_adjusted, the census's 'Employees Avg
  MTD % Change - Adjusted', 0.968030277523212);
- inside an IF branch, so do VALUES & co.: the census's 'Employees Avg MTD
  Sales PM', IF([_ShowValueForDates], CALCULATE([Employees Avg MTD Sales],
  <previous month>)) over AVERAGEX(ALLSELECTED(DimEmployee[EmployeeKey]),
  ...), is 72193.252866412251024 (pm_avgx), the ascending fold, where
  CALCULATE(SUMX(ALLSELECTED(...), ...), <previous month>) without the IF
  adds in storage order (pm_sumx);
- AVERAGEX(VALUES(FactSales[SalesAmount]), FactSales[SalesAmount]) adds the
  26,965 values in the hash-encoded column's dictionary order (x_values
  0.475217667357356);
- and VALUES / ALL / ALLSELECTED of the key itself, with a measure, add in
  storage order as before (o_values_sumx, o_all_sumx, o_values_avgx;
  build_b243b.py gb_sumx_emp, gb_avgx_emp).

Where the expected values come from: Desktop's answers (the Agents file,
skipped when the community corpus is not present), and on hand-made tables
exact IEEE arithmetic: members worth 1e16, 1 and -1e16 add to 0 in that
order (1e16 + 1 is 1e16) and to 1 in the order -1e16, 1e16, 1.
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


def _col(starts, rle, enc="hash", **extra) -> dict:
    return {"starts": array("q", starts), "rle": bytes(rle), "enc": enc, "mag": 1.0, **extra}


def _model():
    # D's key is value-encoded, stored 3, 1, 2; F[v] per key: 1 -> 1e16,
    # 2 -> 1, 3 -> -1e16. Ascending keys add 1e16 + 1 - 1e16 = 0; the storage
    # order -1e16 + 1e16 + 1 = 1.
    d = {"columns": ["k"], "rows": [[3], [1], [2]],
         "storage": {"rows": 3, "segments": (3,), "columns": {"k": _col([0], [0], "value")}}}
    f = {"columns": ["k", "v"], "rows": [[3, -1e16], [1, 1e16], [2, 1.0]]}
    rels = [{"FromTable": "F", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    return {"D": d, "F": f}, rels


MS = {"S": "SUM(F[v])",
      "values": "SUMX(VALUES(D[k]), [S])",
      "all": "SUMX(ALL(D[k]), [S])",
      "filter": "SUMX(FILTER(VALUES(D[k]), NOT ISBLANK([S])), [S])",
      "filter_avg": "AVERAGEX(FILTER(VALUES(D[k]), NOT ISBLANK([S])), [S])",
      "in_if": "IF(COUNTROWS(D) > 0, SUMX(VALUES(D[k]), [S]))",
      "in_if_avg": "IF(COUNTROWS(D) > 0, CALCULATE(AVERAGEX(ALLSELECTED(D[k]), [S])))"}


def _eval(name: str):
    tables, rels = _model()
    return de.evaluate_measures_batch([name], tables, MS, {}, relationships=rels)[name]


def test_values_of_the_column_add_in_storage_order():
    assert _eval("values") == 1.0
    assert _eval("all") == 1.0


def test_a_filter_of_them_adds_in_data_id_order():
    assert _eval("filter") == 0.0
    assert _eval("filter_avg") == 0.0


def test_inside_an_if_branch_they_add_in_data_id_order():
    # pm_avgx; build_b243d.py settled it: an IF (or SWITCH) whose condition
    # reads the data (i_sumx_if, i_avgx_if, i_switch; an IF(1 = 1, ...) is
    # none, i_avgx_iftrue) -- tests/test_issue345_b243d_order.py
    assert _eval("in_if") == 0.0
    assert _eval("in_if_avg") == 0.0


def test_the_if_branch_order_does_not_depend_on_evaluation_order():
    # Found in review: which order an iteration adds in depended on whether
    # it ran inside an IF branch, but the measure memo and a lazily evaluated
    # VAR did not know that, so the first reference fixed the order of every
    # later one. plain = 1 (storage order) and IF(<condition>, [plain]) = 0
    # (in the branch, data-ID order) now hold in every order of evaluation.
    # The condition reads the data: an IF(1 = 1, ...) is no branch
    # (build_b243d.py i_avgx_iftrue).
    import itertools
    c = "COUNTROWS(D) > 0"
    ms = {"S": "SUM(F[v])", "plain": "SUMX(VALUES(D[k]), [S])", "wrapped": f"IF({c}, [plain])",
          "both": f"[plain] + 10 * IF({c}, [plain])", "both2": f"10 * IF({c}, [plain]) + [plain]",
          # a VAR is evaluated as where it is defined, wherever it is first
          # used (build_b243d.py i_var_if 0.561100009828806: storage order)
          "var_out": f"VAR x = SUMX(VALUES(D[k]), [S]) RETURN IF({c}, x) + 10 * x",
          "var_out2": f"VAR x = SUMX(VALUES(D[k]), [S]) RETURN 10 * x + IF({c}, x)",
          "var_in": f"IF({c}, VAR x = SUMX(VALUES(D[k]), [S]) RETURN 10 * x + x)"}
    want = {"plain": 1.0, "wrapped": 0.0, "both": 1.0, "both2": 1.0,
            "var_out": 11.0, "var_out2": 11.0, "var_in": 0.0}
    for k, w in want.items():
        tables, rels = _model()
        assert de.evaluate_measures_batch([k], tables, ms, {}, relationships=rels)[k] == w, k
    for order in itertools.permutations(["plain", "wrapped", "both", "var_out"]):
        tables, rels = _model()
        got = de.evaluate_measures_batch(list(order), tables, ms, {}, relationships=rels)
        assert got == {k: want[k] for k in order}, order
    tables, rels = _model()
    got = de.evaluate_measures_batch(list(want), tables, ms, {}, relationships=rels)
    assert got == want


def test_a_rebuilt_dictionary_is_built_once(monkeypatch):
    # Found in review: a DateTime dictionary was rebuilt for every
    # iteration over the column's values, and the memo checked its entry
    # against the rebuilt list, so it never hit (Agents: 262 rebuilds of
    # FactSales[DateKey]'s, 1.29 times the time). It is rebuilt once now.
    import datetime as dt

    from pbix_mcp.dax import se_scan
    days = [45000.0, 45001.0, 45002.0]
    base = dt.datetime(1899, 12, 30)
    vals = [base + dt.timedelta(days=d) for d in days]
    f = {"columns": ["g", "d"], "rows": [[i % 4, vals[(2 * i) % 3]] for i in range(12)],
         "storage": {"rows": 12, "segments": (12,), "columns": {
             "g": _col([0], [0], "value"),
             "d": _col([0], [0], dict=list(days), dtype="DateTime")}}}
    calls = []
    real = se_scan.dictionary_values

    def counting(tbl, name):
        calls.append(name)
        return real(tbl, name)
    monkeypatch.setattr(se_scan, "dictionary_values", counting)
    ms = {"m": "SUMX(VALUES(F[g]), CALCULATE(SUMX(FILTER(VALUES(F[d]), TRUE()), 1.5)))"}
    got = de.evaluate_measures_batch(["m"], {"F": f}, ms, {})["m"]
    assert got == 4 * 3 * 1.5
    assert calls == ["d"]


def test_values_of_a_hash_encoded_column_add_in_its_dictionary_order():
    # F[v] stored -1e16, 1e16, 1 (first appearance), its dictionary 1e16, 1, -1e16
    f = {"columns": ["v"], "rows": [[-1e16], [1e16], [1.0]],
         "storage": {"rows": 3, "segments": (3,), "columns": {
             "v": _col([0], [0], dict=[1e16, 1.0, -1e16], dtype="Float64")}}}
    ms = {"avg": "AVERAGEX(VALUES(F[v]), F[v])", "sum": "SUMX(VALUES(F[v]), F[v])",
          "meas": "SUMX(VALUES(F[v]), [one] * F[v])", "one": "1"}
    got = de.evaluate_measures_batch(["avg", "sum", "meas"], {"F": f}, ms, {})
    assert got["sum"] == 0.0
    assert got["avg"] == 0.0
    # a measure in the expression: the dictionary's order too (build_b243d.py
    # h_store_values, h_values_meas, h_up_meas)
    assert got["meas"] == 0.0


def test_a_hash_encoded_columns_dictionary_is_kept_with_its_layout():
    from pbix_mcp.dax import se_scan
    from pbix_mcp.formats import vertipaq_decoder as vd
    st = vd._storage_layout({"c": ([(0, 3, False)], "hash", 1.0, [10.5, 2.5], "Float64"),
                             "d": ([(0, 3, False)], "hash", 1.0, [45000.0], "DateTime"),
                             "k": ([(0, 3, False)], "value", 1.0, None, "Int64")},
                            ["c", "d", "k"], 3, None)
    assert st["columns"]["c"]["dict"] == [10.5, 2.5]
    assert "dict" not in st["columns"]["k"]
    t = {"columns": ["c", "d", "k"], "rows": [[None, None, None]] * 3, "storage": st}
    assert se_scan.dictionary_values(t, "c") == [10.5, 2.5]
    assert se_scan.dictionary_values(t, "k") is None
    assert [str(v)[:10] for v in se_scan.dictionary_values(t, "d")] == ["2023-03-15"]


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    from pbix_mcp import server as S
    ek = "DimEmployee[EmployeeKey]"
    want = {  # build_b243c.py
        "o_values_sumx": (f"SUMX(VALUES({ek}), [MOMTD % Total Sales]) * 1E6 - 92500633", "0.797591596841812"),
        "o_filter_sumx": (f"SUMX(FILTER(VALUES({ek}), NOT ISBLANK([MTD Total Sales]) && NOT ISBLANK({ek})), "
                          "[MOMTD % Total Sales]) * 1E6 - 93163158", "0.655902430415154"),
        "o_values_avgx": (f"AVERAGEX(VALUES({ek}), [MOMTD % Total Sales]) * 1E6 - 353055", "0.854189280886203"),
        "o_adjusted": ("[Employees Avg MTD % Change - Adjusted] * 1E6 - 356946", "0.968030277523212"),
        "pm_avgx": ("[Employees Avg MTD Sales PM] - 72193", "0.252866412251024"),
        "pm_sumx": (f"CALCULATE(SUMX(ALLSELECTED({ek}), [MTD Total Sales]), CALCULATETABLE(DATEADD("
                    "'Date'[Date], -1, MONTH), 'Date'[DateWithTransactions] = TRUE)) - 18914632",
                    "0.251000005751848"),
        "x_values": ("AVERAGEX(VALUES(FactSales[SalesAmount]), FactSales[SalesAmount]) - 5421",
                     "0.475217667357356"),
    }
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(AGENTS, "d9ag"))["success"]
    try:
        ctx = S._get_dax_context("d9ag")
        for k, (e, _w) in want.items():
            ctx["measure_defs"]["__" + k] = e
        got = json.loads(S.pbix_evaluate_dax("d9ag", ",".join("__" + k for k in want),
                                             apply_default_filters=False))
    finally:
        S.pbix_close("d9ag")
    for r, (k, (_e, w)) in zip(got["results"], want.items()):
        assert float(f"{r['value']:.15g}") == float(w), (k, r["value"], w)
