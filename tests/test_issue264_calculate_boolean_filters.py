"""#264: a CALCULATE boolean filter that is not one
`Table[Column] op value` was misparsed or silently dropped.

_CALC_PRED_RE (`T[c] op (.+)$`) read everything after the first operator as
the value, so CALCULATE(COUNTROWS(TM), TM[d] <> 0 && TM[n] = 7) filtered
TM[d] <> (0 && TM[n] = 7); a boolean that does not start with a column
(TC[n] + TC[d] = 3, MOD(TC[d], 2) = 1) matched no branch of the filter loop
and applied no filter at all. Microsoft Learn: a boolean filter is
FILTER(ALL(<the columns it references>), <boolean>); a column inside an
aggregation is none of them.

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v",
[probe]) per probe -- F:/_desktop_oracle/build_b255.py (k1-k5, c1, c2, c5,
c6) and build_b255b.py (k6, k8, k12, k14, k17-k20), over TM(n, d, z) = (7, 2,
1), (7, 0, 0), (-7, 2, 1), (7, BLANK, 0) and TC(n, d) = (1, 2), (2, 3). c2 and
c6 also need issue #249's FILTER conjuncts and eager ||. One spelling against
another: exact arithmetic (the same filter). build_b255c.py (k29-k37):
booleans whose top level is a function, a number, VAR ... RETURN or a bare
column -- CALCULATE(COUNTROWS(TM), TM[d]) is 2, the rows whose d is no 0 and
no BLANK (k37; the column's visible values were applied, 4).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]},
    "TC": {"columns": ["n", "d"], "rows": [[1, 2], [2, 3]]},
}
HOME = {"m_cnt": "COUNTROWS(TM)"}
ERROR = "ERROR"


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {**HOME, "p": dax}))
    if "p" in eng.eval_errors:
        return ERROR
    return got


DESKTOP = [
    ("k1", "CALCULATE(COUNTROWS(TM), TM[d] <> 0 && TM[n] = 7)", 1),            # 0.9.147: 3
    ("k2", "CALCULATE(COUNTROWS(TM), TM[n] = 7 && TM[d] <> 0)", 1),            # BLANK
    ("k3", "CALCULATE(COUNTROWS(TM), TM[d] = 0 || TM[n] = -7)", 3),            # 1
    ("k4", "CALCULATE(COUNTROWS(TC), TC[n] + TC[d] = 3)", 1),                  # 2: dropped
    ("k5", "CALCULATE(COUNTROWS(TC), MOD(TC[d], 2) = 1)", 1),                  # 2: dropped
    ("k6", "CALCULATE(COUNTROWS(TC), MOD(TC[n], TC[d] - TC[n]) = 0)", 2),
    ("k8", "CALCULATE(COUNTROWS(TM), TM[d] < COUNTROWS(TM))", 4),
    ("k12", "CALCULATE(CALCULATE(COUNTROWS(TC), TC[n] <= MAX(TC[d])), TC[d] = 2)", 1),
    ("k14", "CALCULATE(COUNTROWS(TM), NOT(TM[d] = 0 && TM[n] = 7))", 2),       # 3
    ("k17", "CALCULATE(CALCULATE(COUNTROWS(TM), KEEPFILTERS(TM[d] <> 0 && TM[n] = 7)), TM[n] = -7)",
     None),                                                                    # 1
    ("k18", "CALCULATE(CALCULATE(COUNTROWS(TM), TM[d] <> 0 && TM[n] = 7), TM[z] = 0)", None),   # 1
    ("k19", "CALCULATE(CALCULATE(COUNTROWS(TM), TM[d] <> 0 && TM[n] = 7), TM[n] = -7)", 1),
    ("k20", "CALCULATE(COUNTROWS(TM), TM[n] = TM[d] + 5)", 1),                 # BLANK
    ("c1", "CALCULATE(COUNTROWS(TM), FILTER(TM, TM[d] <> 0 && MOD(TM[n], TM[d]) = 1))", 2),
    ("c2", "CALCULATE(COUNTROWS(TM), TM[d] <> 0 && MOD(TM[n], TM[d]) = 1)", 2),  # 3
    ("c5", "CALCULATE(CALCULATE(COUNTROWS(TM), FILTER(TM, MOD(TM[n], TM[d]) = 1)), TM[d] <> 0)", 2),
    ("c6", "CALCULATE(COUNTROWS(TM), TM[d] = 0 || MOD(TM[n], TM[d]) = 1)", ERROR),  # 1
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _engine(expr) == want


@pytest.mark.parametrize("boolean,want", [
    ("TM[d] <> 0", 2),
    ("(TM[d] <> 0)", 2),
    ("NOT(TM[d] = 0)", 2),
    ("(TM[d] <> 0) && (TM[n] = 7)", 1),
    ("AND(TM[d] <> 0, TM[n] = 7)", 1),
    ("TM[n] = 7", 3),
    ("7 = TM[n]", 3),
    ("TM[d] > 0 && TM[d] < 5", 2),                 # two predicates on one column
    ("AND(TM[d] = 2, TM[n] = -7)", 1),
])
def test_spellings_of_one_filter(boolean, want):
    assert _engine(f"CALCULATE(COUNTROWS(TM), {boolean})") == want


@pytest.mark.parametrize("boolean,want", [
    ("IF(TM[d] = 0, TRUE(), FALSE())", 2),         # d = 0 and d = BLANK
    ("SWITCH(TM[d], 2, TRUE(), FALSE())", 2),
    ("COALESCE(TM[d] = 0, FALSE())", 2),
    ("CONTAINSROW({0}, TM[d])", 1),                # strict: not the BLANK row
    ("IFERROR(MOD(TM[n], TM[d]) = 1, FALSE())", 2),
    ("ISEVEN(TM[n])", None),                       # no row: BLANK
    ("VAR v = 2 RETURN TM[d] = v", 2),
    ("KEEPFILTERS(IF(TM[d] = 0, TRUE(), FALSE()))", 2),
])
def test_a_function_at_the_top_is_still_a_filter(boolean, want):
    """0.9.147 and the first version of this fix applied none of these: 4."""
    assert _engine(f"CALCULATE(COUNTROWS(TM), {boolean})") == want


B255C = [
    # build_b255c.py, Power BI Desktop 2.152; 0.9.147 applied no filter to
    # k29-k34 and k36 (4)
    ("k29", "CALCULATE(COUNTROWS(TM), IF(TM[d] = 0, TRUE(), FALSE()))", 2),
    ("k30", "CALCULATE(COUNTROWS(TM), SWITCH(TM[d], 0, TRUE(), FALSE()))", 1),   # SWITCH: BLANK is no 0
    ("k31", "CALCULATE(COUNTROWS(TM), COALESCE(TM[d] = 0, FALSE()))", 2),
    ("k32", "CALCULATE(COUNTROWS(TM), CONTAINSROW({0}, TM[d]))", 1),
    ("k33", "CALCULATE(COUNTROWS(TM), IFERROR(MOD(TM[n], TM[d]) = 1, FALSE()))", 2),
    ("k34", "CALCULATE(COUNTROWS(TM), TM[d] + 0)", 2),
    ("k36", "CALCULATE(COUNTROWS(TM), VAR v = 2 RETURN TM[d] = v)", 2),
    # a bare column is a True/False filter too: the rows whose d is no 0 and
    # no BLANK (the column's visible values were applied: 4)
    ("k37", "CALCULATE(COUNTROWS(TM), TM[d])", 2),
]


@pytest.mark.parametrize("probe,expr,want", B255C, ids=[p for p, _e, _w in B255C])
def test_matches_desktop_b255c(probe, expr, want):
    assert _engine(expr) == want


def test_k35_a_constant_is_refused():
    """build_b255c.py k35: CALCULATE(COUNTROWS(TM), 1) -- "The True/False
    expression does not specify a column." (Desktop's English message)."""
    eng = de.DAXEngine()
    eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": "CALCULATE(COUNTROWS(TM), 1)"}))
    assert "does not specify a column" in eng.eval_errors["p"]


@pytest.mark.parametrize("boolean,want", [
    # over T(k) = 1, 2, 3, 4: k = 3 and k = 4 pass each (exact arithmetic);
    # 0.9.147 applied none of them (4, reported by the currency unit)
    ("T[k] * 2 > 4", 2),
    ("T[k] ^ 2 > 4", 2),
    ("POWER(T[k], 2) > 4", 2),
    ("KEEPFILTERS(T[k] - 1 >= 2)", 2),
])
def test_arithmetic_booleans_filter(boolean, want):
    eng = de.DAXEngine()
    ctx = de.DAXContext({"T": {"columns": ["k"], "rows": [[1], [2], [3], [4]]}},
                        {"p": f"CALCULATE(COUNTROWS(T), {boolean})"})
    assert eng.evaluate_measure("p", ctx) == want


def test_a_spec_filter_is_analyzed_once(monkeypatch):
    """CALCULATE(COUNTROWS(T), T[g] = 1) in every row of a SUMX: the filter's
    text is never evaluated itself (it is applied as a column filter), and
    the boolean-filter checks analyzed it anew on every call (a quarter of
    the time of SUMX(VALUES(T[v]), CALCULATE(COUNTROWS(T), T[g] = 1)) over
    20,000 rows). The number of analyses must not grow with the rows."""
    calls = []
    real = de._analyze_expr
    monkeypatch.setattr(de, "_analyze_expr", lambda text: calls.append(text) or real(text))

    def analyses(n):
        de._PLAN_CACHE.clear()
        calls.clear()
        rows = [[i, i % 3] for i in range(n)]
        eng = de.DAXEngine()
        ctx = de.DAXContext({"T": {"columns": ["v", "g"], "rows": rows}},
                            {"p": "SUMX(VALUES(T[v]), CALCULATE(COUNTROWS(T), T[g] = 1))"})
        assert eng.evaluate_measure("p", ctx) == (n + 1) // 3
        return len(calls)

    assert analyses(60) == analyses(300)


DIM_FACT = {"Dim": {"columns": ["k", "cat"], "rows": [[1, "a"], [2, "b"], [3, "c"]]},
            "Fact": {"columns": ["k", "v"], "rows": [[1, 10], [1, 5], [2, 7], [3, 0], [3, 4], [9, 100]]}}
DIM_FACT_RELS = [{"FromTable": "Fact", "FromColumn": "k", "ToTable": "Dim", "ToColumn": "k",
                  "IsActive": True}]


@pytest.mark.parametrize("filt,want", [
    ('Dim[cat] <> "a" && Dim[k] <> 1', 111),
    ('Dim[cat] = "b" && Dim[k] = 2', 7),
    ('KEEPFILTERS(Dim[cat] <> "a" && Dim[k] <> 3)', 107),
])
def test_an_and_of_column_predicates_keeps_the_blank_row(filt, want):
    """An && of one-column predicates on one table is applied as one filter
    per predicate (_split_filter_specs; the combination filter it stands
    for built and tested every combination, 25 times slower over 300,000
    rows). The blank row the unmatched fact (k = 9) brings must pass exactly
    when it passes both: BLANK <> "a" and BLANK <> 1 are TRUE (BLANK = "a"
    and BLANK = 1 are FALSE), BLANK <> 3 too. Exact arithmetic over
    FILTER(ALL(Dim[cat], Dim[k]), ...) (Microsoft Learn's definition; ALL
    lists the blank row): 7 + 0 + 4 + 100 = 111; 7; 7 + 100 = 107."""
    eng = de.DAXEngine()
    ctx = de.DAXContext(DIM_FACT, {"p": f"CALCULATE(SUM(Fact[v]), {filt})"},
                        relationships=DIM_FACT_RELS)
    assert eng.evaluate_measure("p", ctx) == want
    assert "p" not in eng.eval_errors
