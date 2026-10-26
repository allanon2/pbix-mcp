"""#265: CALCULATE raised a FILTER argument's row error that
its other filters exclude; Power BI Desktop intersects the filters first.

CALCULATE evaluated each filter argument alone in the outer context, so
FILTER(TM, MOD(TM[n], TM[d]) = 1) failed on the d = 0 and d = BLANK rows even
beside TM[d] <> 0. Desktop raises an argument's row error only when a row of
the table with those values survives the filters without that argument -- the
outer filters it does not replace (all of them under KEEPFILTERS) and the
other arguments, on any column.

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v",
[probe]) per probe -- F:/_desktop_oracle/build_b255.py (c3, c4, c9, c10) and
build_b255b.py (c12-c21, c23), over TM(n, d, z) = (7, 2, 1), (7, 0, 0), (-7,
2, 1), (7, BLANK, 0), where MOD(TM[n], TM[d]) fails on the second and fourth
rows. The boolean forms also need #264.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]}}
ERROR = "ERROR"
MOD_EQ_1 = "MOD(TM[n], TM[d]) = 1"


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    if "p" in eng.eval_errors:
        assert "MOD" in eng.eval_errors["p"]
        return ERROR
    return got


DESKTOP = [
    ("c3", f"CALCULATE(COUNTROWS(TM), TM[d] <> 0, {MOD_EQ_1})", 2),
    ("c4", f"CALCULATE(COUNTROWS(TM), TM[d] <> 0, FILTER(TM, {MOD_EQ_1}))", 2),       # 0.9.147 ERROR
    ("c9", f"CALCULATE(CALCULATE(COUNTROWS(TM), {MOD_EQ_1}), TM[d] <> 0)", ERROR),
    ("c10", f"CALCULATE(CALCULATE(COUNTROWS(TM), KEEPFILTERS({MOD_EQ_1})), TM[d] <> 0)", 2),
    ("c12", f"CALCULATE(COUNTROWS(TM), {MOD_EQ_1})", ERROR),
    ("c13", f"CALCULATE(COUNTROWS(TM), FILTER(TM, {MOD_EQ_1}), TM[d] <> 0)", 2),
    ("c14", f"CALCULATE(COUNTROWS(TM), TM[d] <> 0, FILTER(ALL(TM), {MOD_EQ_1}))", 2),
    ("c15", f"CALCULATE(COUNTROWS(TM), FILTER(TM, {MOD_EQ_1}), FILTER(TM, TM[d] <> 0))", 2),
    ("c16", f"COUNTROWS(CALCULATETABLE(TM, TM[d] <> 0, FILTER(TM, {MOD_EQ_1})))", 2),
    ("c17", f"CALCULATE(COUNTROWS(TM), TM[d] = 2, TM[n] = 7 || {MOD_EQ_1})", 2),
    ("c18", f"CALCULATE(COUNTROWS(TM), TM[z] = 1, {MOD_EQ_1})", 2),
    ("c19", f"CALCULATE(CALCULATE(COUNTROWS(TM), {MOD_EQ_1}), TM[z] = 1)", 2),
    ("c20", f"CALCULATE(COUNTROWS(TM), TM[n] = -7, {MOD_EQ_1})", 1),
    ("c21", f"CALCULATE(CALCULATE(COUNTROWS(TM), KEEPFILTERS({MOD_EQ_1})), TM[z] = 1)", 2),
    ("c23", f"CALCULATE(CALCULATE(COUNTROWS(TM), KEEPFILTERS(FILTER(ALL(TM), {MOD_EQ_1}))), TM[d] <> 0)", 2),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _engine(expr) == want


@pytest.mark.parametrize("arg", [
    f"FILTER(FILTER(TM, {MOD_EQ_1}), TM[z] >= 0)",
    f"FILTER(SUMMARIZE(TM, TM[n], TM[d]), {MOD_EQ_1})",
], ids=["c30_nested_filter", "c29_summarize"])
def test_two_more_argument_shapes(arg):
    """Power BI Desktop 2.152 (build_b255c.py c30, c29) answers 2 for both,
    as the rule it answered c3-c23 with predicts: the inner FILTER's row
    errors (they bypassed the outer FILTER's collection) and SUMMARIZE's rows
    (the error was judged on their first column alone, where (7, BLANK)
    shares 7 with the surviving rows). The MOD errors fall on (7, 0) and (7,
    BLANK), which TM[d] <> 0 excludes (BLANK <> 0 is FALSE, Desktop k1)."""
    assert _engine(f"CALCULATE(COUNTROWS(TM), TM[d] <> 0, {arg})") == 2
