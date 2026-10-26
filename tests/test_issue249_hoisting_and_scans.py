"""Issue #249, third Desktop round (build_b255c.py): which operands a logical
operator in a row evaluates first, which filters make a scalar && evaluate
every operand, and which FILTER scans try their column predicates first.

* In a row, the operands that read nothing the row changes are evaluated
  first, as a scalar && evaluates them, and one of them that decides decides
  for every row: SUMX(TM, IF([m_zero] <> 0 && MOD(TM[n], TM[d]) = 1, 1, 0)) is
  0 whichever side the row-free operand is on (h1, h2), and so with FALSE()
  (h3), with || (h4) and with a VAR defined outside the iteration (w2).
  The fix for #249 evaluated every operand once one of them read the row, so
  each failed with MOD's division by zero. VARs defined in the row read the
  row (w1).
* A scalar && evaluates every operand under a filter that reaches a table its
  operands read -- another column of the table (v7), a related dimension
  (v10), ALL(TM) beside the filter (v12) -- and stops early under a filter on
  an unrelated table (v8) or none (v9).
* FILTER over VALUES of a column tries its conjuncts as written (c24 fails),
  over a model table and CALCULATETABLE of one its column predicates first
  (c27), and RELATED(TD[lab]) = "two" is a column predicate (c28; the fix
  for #249 failed it).

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v",
[probe]) per probe, F:/_desktop_oracle/build_b255c.py; TM(n, d, z) = (7, 2,
1), (7, 0, 0), (-7, 2, 1), (7, BLANK, 0), TD(key, lab) = (0, "zero"), (2,
"two") with TM[d] -> TD[key]. ERROR is an error answer.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]},
    "TC": {"columns": ["n", "d"], "rows": [[1, 2], [2, 3]]},
    "TD": {"columns": ["key", "lab"], "rows": [[0, "zero"], [2, "two"]]},
}
RELS = [{"FromTable": "TM", "FromColumn": "d", "ToTable": "TD", "ToColumn": "key", "IsActive": True}]
HOME = {
    "m_zero": "COUNTROWS(ALL(TM)) - 4",
    "m_and_d": "IF(SELECTEDVALUE(TM[d]) <> 0 && MOD(7, SELECTEDVALUE(TM[d])) = 1, 1)",
}
ERROR = "ERROR"


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {**HOME, "p": dax}, relationships=RELS))
    if "p" in eng.eval_errors:
        return ERROR, eng.eval_errors["p"]
    return got, None


# (probe id, expression, Desktop's answer, a word its error names)
B255C = [
    ("v7", "CALCULATE([m_and_d], TM[z] = 0)", ERROR, "MOD"),
    ("v8", "CALCULATE([m_and_d], TC[n] = 1)", None, None),
    ("v9", "CALCULATE([m_and_d])", None, None),
    ("v10", 'CALCULATE([m_and_d], TD[lab] = "zero")', ERROR, "MOD"),
    ("v11", "CALCULATE(IF(SELECTEDVALUE(TM[d]) <> 0 && MOD(7, SELECTEDVALUE(TM[d])) = 1, 1), TM[d] = 0)",
     ERROR, "MOD"),
    ("v12", "CALCULATE([m_and_d], TM[d] = 0, ALL(TM))", ERROR, "MOD"),
    ("v13", "CALCULATE(IF(HASONEVALUE(TM[z]) && VALUES(TM[z]) = 1, 1), TM[d] = 2)", 1, None),
    ("v14", "CALCULATE(IF(HASONEVALUE(TM[n]) && VALUES(TM[n]) = 7, 1), TM[z] = 0)", 1, None),
    # needs #275 too: VALUES(TM[d]) has two values there
    ("v15", "CALCULATE(IF(HASONEVALUE(TM[d]) && VALUES(TM[d]) = 7, 1), TM[z] = 0)", ERROR, "multiple values"),
    ("h1", "SUMX(TM, IF([m_zero] <> 0 && MOD(TM[n], TM[d]) = 1, 1, 0))", 0, None),
    ("h2", "SUMX(TM, IF(MOD(TM[n], TM[d]) = 1 && [m_zero] <> 0, 1, 0))", 0, None),
    ("h3", "SUMX(TM, IF(FALSE() && MOD(TM[n], TM[d]) = 1, 1, 0))", 0, None),
    ("h4", "SUMX(TM, IF([m_zero] = 0 || MOD(TM[n], TM[d]) = 1, 1, 0))", 4, None),
    ("w1", "SUMX(TM, VAR a = TM[d] <> 0 VAR b = MOD(TM[n], TM[d]) = 1 RETURN IF(a && b, 1))", ERROR, "MOD"),
    ("w2", "VAR a = [m_zero] <> 0 RETURN SUMX(TM, IF(a && MOD(TM[n], TM[d]) = 1, 1, 0))", 0, None),
    ("w3", "CALCULATE(VAR a = SELECTEDVALUE(TM[d]) <> 0 VAR b = MOD(7, SELECTEDVALUE(TM[d])) = 1 "
           "RETURN IF(a && b, 1), TM[d] = 0)", ERROR, "MOD"),
    ("c24", "COUNTROWS(FILTER(VALUES(TM[d]), MOD(7, TM[d]) = 1 && TM[d] <> 0))", ERROR, "MOD"),
    ("c25", 'COUNTROWS(FILTER(ADDCOLUMNS(TM, "q", 1), MOD(TM[n], TM[d]) = 1 && TM[d] <> 0))', ERROR, "MOD"),
    ("c27", "COUNTROWS(FILTER(CALCULATETABLE(TM, TM[z] >= 0), MOD(TM[n], TM[d]) = 1 && TM[d] <> 0))", 2, None),
    ("c28", 'COUNTROWS(FILTER(TM, MOD(TM[n], TM[d]) = 1 && RELATED(TD[lab]) = "two"))', 2, None),
]


@pytest.mark.parametrize("probe,expr,want,word", B255C, ids=[p for p, _e, _w, _x in B255C])
def test_matches_desktop(probe, expr, want, word):
    got, err = _engine(expr)
    assert got == want
    if word is not None:
        assert word in err


def test_the_function_forms_hoist_too():
    """AND() / OR() are && / || (B255 x5, f13): the row-free operand decides
    them alone in a row as well -- h2 and h4 written with the functions."""
    assert _engine("SUMX(TM, IF(AND(MOD(TM[n], TM[d]) = 1, [m_zero] <> 0), 1, 0))") == (0, None)
    assert _engine("SUMX(TM, IF(OR(MOD(TM[n], TM[d]) = 1, [m_zero] = 0), 1, 0))") == (4, None)


def test_the_pushdown_keeps_the_first_step():
    """CALCULATE(<aggregate>, FILTER(T, c)) over one column is rewritten as a
    FILTER over VALUES(T[c]) (issue #203), which has no first step (c24): the
    rewrite writes c's conjuncts in FILTER(T)'s order, column predicates
    first. FILTER(TM, MOD(7, TM[d]) = 1 && TM[d] <> 0) keeps the two d = 2
    rows (its column predicate first, as f1 / c27), so CALCULATE counts 2:
    Power BI Desktop 2.152, build_b255d.py c37 and c31 (rel148 and 0.9.147
    answer 2 too)."""
    assert _engine("CALCULATE(COUNTROWS(TM), FILTER(TM, MOD(7, TM[d]) = 1 && TM[d] <> 0))") == (2, None)
    assert _engine("COUNTROWS(FILTER(TM, MOD(7, TM[d]) = 1 && TM[d] <> 0))") == (2, None)


def test_the_pushdown_is_kept_when_a_column_predicate_comes_last():
    """The rewrite stays where the conjuncts' order differs over T and over
    VALUES(T[c]) -- written in FILTER(T)'s order -- instead of giving up the
    column-index path for a scan of T row by row (10 to 170 times slower on
    50,000 rows). Engine-internal: the rewritten filter argument."""
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, dict(HOME), relationships=RELS)
    got = eng._pushdown_table_filter(
        ["COUNTROWS(TM)", "FILTER(TM, MOD(7, TM[d]) = 1 && TM[d] <> 0)"], ctx)
    assert got == ["COUNTROWS(TM)", "KEEPFILTERS(FILTER(VALUES('TM'[d]), (TM[d] <> 0) && (MOD(7, TM[d]) = 1)))"]
    same = eng._pushdown_table_filter(["COUNTROWS(TM)", "FILTER(TM, TM[d] <> 0 && MOD(7, TM[d]) = 1)"], ctx)
    assert same == ["COUNTROWS(TM)", "KEEPFILTERS(FILTER(VALUES('TM'[d]), TM[d] <> 0 && MOD(7, TM[d]) = 1))"]


def test_a_var_operand_is_judged_as_written_inline():
    """A VAR defined outside the iteration reads no row of it (w2), so it is
    hoisted as the same operand written inline is; a VAR defined in the row
    reads the row (w1). Under a filter on a table the operands read every
    operand runs in both spellings: Power BI Desktop 2.152 fails the first
    pair (build_b255d.py f10 inline, f9 the VAR). The other pairs are
    compared with themselves."""
    for inline, var in [
        ("CALCULATE(SUMX(TM, IF(SELECTEDVALUE(TM[d]) <> 0 && MOD(TM[n], TM[d]) = 1, 1, 0)), TM[z] = 0)",
         "CALCULATE(VAR a = SELECTEDVALUE(TM[d]) <> 0 RETURN "
         "SUMX(TM, IF(a && MOD(TM[n], TM[d]) = 1, 1, 0)), TM[z] = 0)"),
        ("CALCULATE(SUMX(TM, IF(COUNTROWS(TM) > 5 && MOD(TM[n], TM[d]) = 1, 1, 0)), TM[z] = 0)",
         "CALCULATE(VAR a = COUNTROWS(TM) > 5 RETURN SUMX(TM, IF(a && MOD(TM[n], TM[d]) = 1, 1, 0)), TM[z] = 0)"),
        ("SUMX(TM, IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1))",
         "SUMX(TM, VAR a = TM[d] <> 0 RETURN IF(a && MOD(TM[n], TM[d]) = 1, 1))"),
    ]:
        a, b = _engine(inline), _engine(var)
        assert a[0] == b[0], (inline, a, b)
    assert _engine("CALCULATE(SUMX(TM, IF(SELECTEDVALUE(TM[d]) <> 0 && MOD(TM[n], TM[d]) = 1, 1, 0)), "
                   "TM[z] = 0)")[0] == ERROR
