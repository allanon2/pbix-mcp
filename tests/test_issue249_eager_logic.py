"""Issue #249: when Power BI Desktop evaluates a guarded, erroring operand
anyway -- && / || / AND() / OR() in an iteration's rows, and FILTER's
top-level conjunction.

0.9.147 stopped every && / || at the deciding side (issue #239, "not measured
on Desktop") and evaluated FILTER's condition as one expression, so the guard
had to come first. Desktop does neither:

* in a row of an iteration (SUMX, COUNTX, ADDCOLUMNS, FILTER below its top
  level, over model tables and GENERATESERIES / DATATABLE / { } tables alike),
  a logical operator evaluates every operand, and an operand's error is the
  row's error -- also in a measure the row reaches through its context
  transition and under a filter on what it reads; a scalar stops at the
  deciding side, and so do operands that read nothing that changes;
* FILTER over a model table first applies the conjuncts that are column
  predicates, wherever they stand; the other conjuncts run left to right and
  the first error raises; a FILTER over another table has no first step;
  FILTER(FILTER(T, c1), c2) applies c2's column predicates first;
* IF / SWITCH / IF.EAGER branches and VARs stay lazy; IFERROR / ISERROR inside
  the row catch the row's error.

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v",
[probe]) per probe -- F:/_desktop_oracle/build_b255.py (B255, probe ids a/s/x/
n/f/i/o/e/r/d) and build_b255b.py (B255b, probe ids g/x/v/y). ERROR is an
error answer; the MOD probes fail with "Division by zero ... 'MOD'", the
conversions with "Cannot convert value 'x' of type Text to type Number", the
ERROR("x") probes with "x". Probes that are other issues' (CALCULATE filters,
nested measure errors, IFERROR outside the iteration, ERROR's constant
argument) are in their own test files.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]},
    "TS": {"columns": ["s", "k"], "rows": [["1", 1], ["x", 0], ["3", 1]]},
    "TC": {"columns": ["n", "d"], "rows": [[1, 2], [2, 3]]},
}
HOME = {
    "m_zero": "COUNTROWS(ALL(TM)) - 4",
    "m_dnz": "SELECTEDVALUE(TM[d]) <> 0",
    "m_mod": "MOD(SELECTEDVALUE(TM[n]), SELECTEDVALUE(TM[d]))",
    "m_and": "IF(SELECTEDVALUE(TM[d]) <> 0 && [m_mod] = 1, 1)",
    "m_and_d": "IF(SELECTEDVALUE(TM[d]) <> 0 && MOD(7, SELECTEDVALUE(TM[d])) = 1, 1)",
    "m_s1": "IF([m_zero] <> 0 && MOD(1, [m_zero]) = 1, 1, 0)",
    "m_cnt": "COUNTROWS(TM)",
}
ERROR = "ERROR"


def _engine(dax):
    """(value, error message): the measure's answer, ERROR when the engine
    reports the measure failed."""
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {**HOME, "p": dax}))
    if "p" in eng.eval_errors:
        return ERROR, eng.eval_errors["p"]
    return got, None


# (probe id, expression, Desktop's answer, a word its error names)
B255 = [
    ("a1", "COUNTROWS(FILTER(TM, TM[d] <> 0 && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("a2", "COUNTROWS(FILTER(TM, TM[d] = 0 || ISBLANK(TM[d]) || MOD(TM[n], TM[d]) = 1))", ERROR, "MOD"),
    ("a3", "SUMX(TM, IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1))", ERROR, "MOD"),
    ("s1", "IF([m_zero] <> 0 && MOD(1, [m_zero]) = 1, 1, 0)", 0, None),
    ("s2", "IF([m_zero] = 0 || MOD(1, [m_zero]) = 1, 1, 0)", 1, None),
    ("s4", "IF([m_zero] <> 0, MOD(1, [m_zero]), 0)", 0, None),
    ("s5", "IF.EAGER([m_zero] <> 0, MOD(1, [m_zero]), 0)", 0, None),
    ("s6", "VAR x = MOD(1, [m_zero]) RETURN IF([m_zero] <> 0, x, 0)", 0, None),
    ("s7", 'VAR x = ERROR("unused") RETURN 1', 1, None),
    ("s8", "IF(COUNTROWS(TM) = 99 && MOD(7, COUNTROWS(TM) - 4) = 1, 1, 0)", 0, None),
    ("x1", "COUNTX(TM, IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1))", ERROR, "MOD"),
    ("x3", 'SUMX(ADDCOLUMNS(TM, "q", IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1)), [q])', ERROR, "MOD"),
    ("x4", 'COUNTROWS(FILTER(ADDCOLUMNS(TM, "q", TM[d] <> 0 && MOD(TM[n], TM[d]) = 1), [q]))', ERROR, "MOD"),
    ("x5", "SUMX(TM, IF(AND(TM[d] <> 0, MOD(TM[n], TM[d]) = 1), 1))", ERROR, "MOD"),
    ("x6", "SUMX(TM, IF(TM[d] <> 0 && TM[n] <> 99 && MOD(TM[n], TM[d]) = 1, 1))", ERROR, "MOD"),
    ("x8", "SUMX(TM, INT(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1))", ERROR, "MOD"),
    ("x9", "SUMX(TM, IF(TM[d] = 0 || MOD(TM[n], TM[d]) = 1, 1))", ERROR, "MOD"),
    ("x10", "SUMX(TM, IF(TM[z] = 1 && MOD(TM[n], TM[d]) = 1, 1))", ERROR, "MOD"),
    ("x12", "SUMX(VALUES(TM[d]), IF(TM[d] <> 0 && MOD(7, TM[d]) = 1, 1))", ERROR, "MOD"),
    ("x14", "SUMX(FILTER(TM, TM[d] <> 0), IF(MOD(TM[n], TM[d]) = 1, 1))", 2, None),
    ("x16", "CALCULATE(SUMX(TM, IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1)), TM[d] > 0)", 2, None),
    ("x17", "SUMX(FILTER(TM, [m_dnz]), IF(MOD(TM[n], TM[d]) = 1, 1))", 2, None),
    ("n1", "SUMX(GENERATESERIES(-1, 2), IF([Value] <> 0 && MOD(7, [Value]) = 1, 1))", ERROR, "MOD"),
    ("n2", "SUMX(GENERATESERIES(-1, 2), IF([Value] <> 0, MOD(7, [Value])))", 1, None),
    ("n3", 'SUMX(DATATABLE("n", INTEGER, "d", INTEGER, {{7, 2}, {7, 0}, {9, 2}}), '
           'IF([d] <> 0 && MOD([n], [d]) = 1, 1))', ERROR, "MOD"),
    ("n4", "SUMX({(7, 2), (7, 0), (9, 2)}, IF([Value2] <> 0 && MOD([Value1], [Value2]) = 1, 1))",
     ERROR, "MOD"),
    ("n5", 'SUMX(ADDCOLUMNS(TM, "d2", TM[d] + 0), IF([d2] <> 0 && MOD(TM[n], [d2]) = 1, 1))',
     ERROR, "MOD"),
    ("n6", 'SUMX(SELECTCOLUMNS(TM, "n2", TM[n], "d2", TM[d]), IF([d2] <> 0 && MOD([n2], [d2]) = 1, 1))',
     ERROR, "MOD"),
    ("n7", "COUNTROWS(FILTER(GENERATESERIES(-1, 2), [Value] <> 0 && MOD(7, [Value]) = 1))", 1, None),
    ("n8", "COUNTROWS(FILTER(GENERATESERIES(-1, 2), [Value] = 0 || MOD(7, [Value]) = 1))", ERROR, "MOD"),
    ("n9", 'COUNTROWS(FILTER(DATATABLE("n", INTEGER, "d", INTEGER, {{7, 2}, {7, 0}, {9, 2}}), '
           '[d] <> 0 && MOD([n], [d]) = 1))', 2, None),
    ("n10", 'COUNTROWS(FILTER(ADDCOLUMNS(TM, "d2", TM[d] + 0), [d2] <> 0 && MOD(TM[n], [d2]) = 1))', 2, None),
    ("n11", 'COUNTROWS(FILTER(SELECTCOLUMNS(TM, "n2", TM[n], "d2", TM[d]), '
            '[d2] <> 0 && MOD([n2], [d2]) = 1))', 2, None),
    ("f1", "COUNTROWS(FILTER(TM, MOD(TM[n], TM[d]) = 1 && TM[d] <> 0))", 2, None),
    ("f2", "COUNTROWS(FILTER(TM, AND(TM[d] <> 0, MOD(TM[n], TM[d]) = 1)))", 2, None),
    ("f4", "COUNTROWS(FILTER(TM, TM[d] > 0 && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f5", "COUNTROWS(FILTER(TM, NOT(ISBLANK(TM[d])) && TM[d] <> 0 && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f6", "COUNTROWS(FILTER(TM, TM[d] IN {2} && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f7", "COUNTROWS(FILTER(TM, TM[z] = 1 && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f8", "COUNTROWS(FILTER(TM, [m_dnz] && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f9", "COUNTROWS(FILTER(TM, ABS(TM[d]) > 0 && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f10", "COUNTROWS(FILTER(TM, IF(TM[d] <> 0, MOD(TM[n], TM[d]) = 1, FALSE())))", 2, None),
    ("f12", "COUNTROWS(FILTER(TM, TM[d] = 0 || MOD(TM[n], TM[d]) = 1))", ERROR, "MOD"),
    ("f13", "COUNTROWS(FILTER(TM, OR(TM[d] = 0, MOD(TM[n], TM[d]) = 1)))", ERROR, "MOD"),
    ("f14", "COUNTROWS(FILTER(TM, TM[d] <> 0 && (MOD(TM[n], TM[d]) = 1 || TM[n] = 99)))", 2, None),
    ("f15", "COUNTROWS(FILTER(TM, (TM[d] <> 0 && MOD(TM[n], TM[d]) = 1) || TM[n] = 99))", ERROR, "MOD"),
    ("f17", "COUNTROWS(FILTER(TM, VAR x = MOD(TM[n], TM[d]) RETURN TM[d] <> 0 && x = 1))", ERROR, "MOD"),
    ("f18", "VAR k = 0 RETURN COUNTROWS(FILTER(TM, TM[d] <> k && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f19", "COUNTROWS(FILTER(ALL(TM), TM[d] <> 0 && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f20", "COUNTROWS(FILTER(VALUES(TM[d]), TM[d] <> 0 && MOD(7, TM[d]) = 1))", 1, None),
    ("f22", "COUNTROWS(FILTER(FILTER(TM, TM[d] <> 0), TM[d] = 0 || MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f23", "COUNTROWS(FILTER(FILTER(TM, TM[d] <> 0), MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f24", "COUNTROWS(FILTER(FILTER(TM, [m_dnz]), MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f25", "COUNTROWS(FILTER(CALCULATETABLE(TM, TM[d] <> 0), MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f26", "COUNTROWS(FILTER(TM, (TM[d] = 2 || TM[d] = -2) && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f27", "COUNTROWS(FILTER(TM, (TM[d] = 2 || TM[n] = 99) && MOD(TM[n], TM[d]) = 1))", 2, None),
    ("f31", "COUNTROWS(FILTER(TM, TM[d] <> 0 && MOD(TM[n], TM[d] + [m_zero]) = 1))", 2, None),
    ("f32", "COUNTROWS(FILTER(TM, TM[d] <> 0 && [m_mod] = 1))", 2, None),
    ("f34", "COUNTROWS(FILTER(TM, [m_dnz] && [m_mod] = 1))", 2, None),
    ("i1", "SUMX(TM, IF(TM[d] <> 0, MOD(TM[n], TM[d])))", 2, None),
    ("i2", "SUMX(TM, IF(TM[d] <> 0, MOD(TM[n], TM[d]), 0))", 2, None),
    ("i3", "SUMX(TM, IF(TM[d] = 0, 0, MOD(TM[n], TM[d])))", 2, None),
    ("i4", "SUMX(TM, IF(TM[d] <> 0, IF(MOD(TM[n], TM[d]) = 1, 1)))", 2, None),
    ("i5", "SUMX(TM, IF(TM[d] = 0 || ISBLANK(TM[d]), 0, MOD(TM[n], TM[d])))", 2, None),
    ("i6", "SUMX(TM, SWITCH(TRUE(), TM[d] <> 0, MOD(TM[n], TM[d]), 0))", 2, None),
    ("i7", "SUMX(TM, SWITCH(TM[z], 0, 0, MOD(TM[n], TM[d])))", 2, None),
    ("i8", "SUMX(TM, IF.EAGER(TM[d] <> 0, MOD(TM[n], TM[d])))", 2, None),
    ("i9", "SUMX(TM, VAR x = MOD(TM[n], TM[d]) RETURN IF(TM[d] <> 0, x))", 2, None),
    ("i10", "SUMX(TM, IF(TM[d] <> 0, [m_mod]))", 2, None),
    ("i11", "SUMX(TM, IF(TM[d] <> 0, IF(MOD(TM[n], TM[d] + [m_zero]) = 1, 1)))", 2, None),
    ("i13", "SUMX(TM, IF(TM[d] <> 0 && MOD(TM[n], TM[d] + [m_zero]) = 1, 1))", ERROR, "MOD"),
    ("i14", "SUMX(TM, IF([m_dnz] && MOD(TM[n], TM[d]) = 1, 1))", ERROR, "MOD"),
    ("i15", "SUMX(TM, IF([m_dnz], MOD(TM[n], TM[d])))", 2, None),
    ("i16", "SUMX(TM, IF(TM[d] <> 0, DIVIDE(TM[n], TM[d])))", 0, None),
    ("i17", "SUMX(TM, IF(TM[d] <> 0 && DIVIDE(TM[n], TM[d]) > 0, 1))", 1, None),
    ("i19", "COUNTROWS(FILTER(TM, TM[d] = 0 || TM[n] / TM[d] > 0))", 3, None),
    ("i20", "SUMX(TM, TM[n] / TM[d])", float("inf"), None),
    ("o1", "SUMX(VALUES(TM[d]), IF(TM[d] <> 0, SUMX(VALUES(TM[n]), MOD(TM[n], TM[d]))))", 2, None),
    ("o2", "SUMX(VALUES(TM[d]), IF(TM[d] <> 0 && SUMX(VALUES(TM[n]), MOD(TM[n], TM[d])) = 2, 1))",
     ERROR, "MOD"),
    ("o3", "SUMX(VALUES(TM[d]), SUMX(VALUES(TM[n]), IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1)))",
     ERROR, "MOD"),
    ("e1", "SUMX(TS, IF(TS[k] = 1 && VALUE(TS[s]) > 0, 1))", ERROR, "Cannot convert value 'x'"),
    ("e2", "COUNTROWS(FILTER(TS, TS[k] = 1 && VALUE(TS[s]) > 0))", 2, None),
    ("e3", "COUNTROWS(FILTER(TS, TS[k] = 0 || VALUE(TS[s]) > 0))", ERROR, "Cannot convert value 'x'"),
    ("e4", "SUMX(TS, IF(TS[k] = 1, VALUE(TS[s])))", 4, None),
    ("e5", "SUMX(TS, IF(TS[k] = 1 && TS[s] * 1 > 0, 1))", ERROR, "Cannot convert value 'x'"),
    ("e6", "SUMX(TS, IF(TS[k] = 1, TS[s] * 1))", 4, None),
    ("e7", "COUNTROWS(FILTER(TS, TS[k] = 0 || TS[s] * 1 > 0))", ERROR, "Cannot convert value 'x'"),
    ("e8", "SUMX(TS, VALUE(TS[s]))", ERROR, "Cannot convert value 'x'"),
    ("e9", 'SUMX(TM, IF(TM[d] = 99 && ERROR("x"), 1))', ERROR, "x"),
    ("e10", 'SUMX(TM, IF(TM[d] = 99, ERROR("x")))', None, None),
    ("e11", 'COUNTROWS(FILTER(TM, TM[d] <> 99 || ERROR("x")))', ERROR, "x"),
    ("e12", 'COUNTROWS(FILTER(TM, TM[d] = 99 && ERROR("x")))', None, None),
    ("e14", "SUMX(TM, IF(TM[d] = 99, MOD(7, 0)))", None, None),
    ("e15", "SUMX(TM, IF(TM[d] = 99 && MOD(7, 0) = 1, 1))", ERROR, "MOD"),
    ("r1", "SUMX(TM, IFERROR(MOD(TM[n], TM[d]), 0))", 2, None),
    ("r2", "SUMX(TM, IFERROR(IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1), 100))", 202, None),
    ("r3", "COUNTROWS(FILTER(TM, IFERROR(TM[d] = 0 || MOD(TM[n], TM[d]) = 1, FALSE())))", 2, None),
    ("r4", "COUNTROWS(FILTER(TM, ISERROR(TM[d] = 0 || MOD(TM[n], TM[d]) = 1)))", 2, None),
    ("r6", "SUMX(TM, IF(TM[d] <> 0 && IFERROR(MOD(TM[n], TM[d]), 0) = 1, 1))", 2, None),
    ("r7", "SUMX(TM, IFERROR(IF(TM[d] <> 0, MOD(TM[n], TM[d])), 100))", 2, None),
    ("r8", "COUNTROWS(FILTER(TM, IFERROR(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, TRUE())))", 4, None),
    ("d1", "SUMX(TC, MOD(TC[n], TC[d] - TC[n]))", 0, None),
    ("d2", "COUNTROWS(FILTER(TC, MOD(TC[n], TC[d] - TC[n]) = 0))", 2, None),
]

B255B = [
    # FILTER's conjuncts: column predicates first, the rest left to right
    ("g1", "COUNTROWS(FILTER(TM, MOD(TM[n], TM[d]) = 1 && ABS(TM[d]) > 0))", ERROR, "MOD"),
    ("g2", "COUNTROWS(FILTER(TM, MOD(TM[n], TM[d]) = 1 && [m_dnz]))", 2, None),
    ("g3", "COUNTROWS(FILTER(TM, [m_mod] = 1 && [m_dnz]))", 2, None),
    ("g4", "COUNTROWS(FILTER(TM, [m_mod] = 1 && TM[d] <> 0))", 2, None),
    ("g5", "COUNTROWS(FILTER(GENERATESERIES(-1, 2), MOD(7, [Value]) = 1 && [Value] <> 0))", ERROR, "MOD"),
    ("g6", "COUNTROWS(FILTER(TM, [m_mod] = 1 && ABS(TM[d]) > 0))", ERROR, "MOD"),
    ("g8", "COUNTROWS(FILTER(TM, AND(MOD(TM[n], TM[d]) = 1, ABS(TM[d]) > 0)))", ERROR, "MOD"),
    ("g9", "COUNTROWS(FILTER(TM, MOD(TM[n], TM[d]) = 1 && IF(TM[d] <> 0, TRUE())))", ERROR, "MOD"),
    ("g11", "COUNTROWS(FILTER(TM, (ABS(TM[d]) > 0 && MOD(TM[n], TM[d]) = 1)))", 2, None),
    ("g12", "COUNTROWS(FILTER(TM, NOT(TM[d] = 0 || MOD(TM[n], TM[d]) <> 1)))", ERROR, "MOD"),
    ("g13", "COUNTROWS(FILTER(TM, (TM[d] <> 0 && MOD(TM[n], TM[d]) = 1) = TRUE()))", ERROR, "MOD"),
    ("g14", "COUNTROWS(FILTER(TM, VAR x = MOD(TM[n], TM[d]) RETURN TM[d] <> 0 && TM[n] = 7))", 1, None),
    ("g15", "COUNTROWS(FILTER(TM, VAR g = TM[d] <> 0 RETURN g && MOD(TM[n], TM[d]) = 1))", ERROR, "MOD"),
    ("g16", "COUNTROWS(FILTER(FILTER(TM, MOD(TM[n], TM[d]) = 1), TM[d] <> 0))", 2, None),
    ("g17", "COUNTROWS(FILTER(FILTER(TM, MOD(TM[n], TM[d]) = 1), [m_dnz]))", 2, None),
    ("g18", "COUNTROWS(FILTER(TM, TM[n] = 7 && MOD(TM[n], TM[d]) = 1))", ERROR, "MOD"),
    # what reads something that changes: the row, a measure there, a filter
    ("v1", 'COUNTROWS(SUMMARIZECOLUMNS(TM[d], "v", [m_and_d]))', ERROR, "MOD"),
    ("v2", "[m_and_d]", None, None),
    ("v3", "SUMX(VALUES(TM[d]), [m_and_d])", ERROR, "MOD"),
    ("v4", "CALCULATE([m_and_d], TM[d] = 0)", ERROR, "MOD"),
    ("v5", "[m_and]", None, None),
    ("v6", 'COUNTROWS(FILTER(ADDCOLUMNS(VALUES(TM[d]), "v", [m_and_d]), NOT ISBLANK([v])))', ERROR, "MOD"),
    ("x19", "SUMX(TM, IF([m_zero] <> 0 && MOD(1, [m_zero]) = 1, 1, 0))", 0, None),
    ("x20", "SUMX(TM, IF(FALSE() && MOD(1, 0) = 1, 1, 0))", 0, None),
    ("x21", "SUMX(TM, [m_s1])", 0, None),
    ("x22", "SUMX(TM, CALCULATE(IF(MAX(TM[d]) <> 0 && MOD(7, MAX(TM[d])) = 1, 1)))", ERROR, "MOD"),
    ("x23", "MAXX(TM, IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1))", ERROR, "MOD"),
    ("x25", 'COUNTROWS(FILTER(SELECTCOLUMNS(TM, "q", TM[d] <> 0 && MOD(TM[n], TM[d]) = 1), [q]))',
     ERROR, "MOD"),
    ("x26", "COUNTROWS(GENERATE(VALUES(TM[d]), FILTER(VALUES(TM[n]), "
            "TM[d] <> 0 && MOD(TM[n], TM[d]) = 1)))", 2, None),
    ("x27", "VAR b = IF([m_zero] <> 0 && MOD(1, [m_zero]) = 1, 1, 0) RETURN SUMX(TM, b)", 0, None),
    # COALESCE's later arguments are lazy in a row
    ("y1", "SUMX(TM, COALESCE(IF(TM[z] = 0, 0), MOD(TM[n], TM[d])))", 2, None),
]


@pytest.mark.parametrize("probe,expr,want,word", B255 + B255B,
                         ids=[p for p, _e, _w, _x in B255 + B255B])
def test_matches_desktop(probe, expr, want, word):
    got, err = _engine(expr)
    assert got == want
    if word is not None:
        assert word in err


@pytest.mark.parametrize("raw", ['SUMMARIZECOLUMNS(TM[d], "v", [m_and_d])',
                                 'SUMMARIZECOLUMNS(TM[z], "v", [m_and_d])'],
                         ids=["q_scol_d", "q_scol_z"])
def test_a_grouped_query_evaluates_every_operand(raw):
    """B255b's raw queries q_scol_d and q_scol_z (EVALUATE SUMMARIZECOLUMNS
    ...): Desktop fails both with m_and_d's MOD by 0 / BLANK -- the d = 0 and
    the z = 0 groups (where SELECTEDVALUE(TM[d]) is BLANK)."""
    got, err = _engine(f"COUNTROWS({raw})")
    assert got == ERROR and "MOD" in err


def test_the_operator_and_the_function_forms_agree():
    """AND() / OR() are && / || (B255: x5 and a3, f13 and f12 fail alike;
    f2 and a1 both 2)."""
    pairs = [
        ("SUMX(TM, IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1))",
         "SUMX(TM, IF(AND(TM[d] <> 0, MOD(TM[n], TM[d]) = 1), 1))"),
        ("COUNTROWS(FILTER(TM, TM[d] = 0 || MOD(TM[n], TM[d]) = 1))",
         "COUNTROWS(FILTER(TM, OR(TM[d] = 0, MOD(TM[n], TM[d]) = 1)))"),
        ("COUNTROWS(FILTER(TM, TM[d] <> 0 && MOD(TM[n], TM[d]) = 1))",
         "COUNTROWS(FILTER(TM, AND(TM[d] <> 0, MOD(TM[n], TM[d]) = 1)))"),
    ]
    for ops, funcs in pairs:
        assert _engine(ops)[0] == _engine(funcs)[0]
