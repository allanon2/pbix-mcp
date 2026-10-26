"""Issue #249, last Desktop round (build_b255d.py, 65 probes): every answer
of Power BI Desktop 2.152 for the points the third round left open.

* d: comparison chains group from left to right (d1-d8) and IN binds tighter
  than the comparison operators (d9-d11, #274); TRUE / FALSE
  against a number is an error (d5, d11, #279).
* f: in a row, the operands of && / AND() that read nothing the row changes
  go first and one that decides decides alone, even when another of them
  fails (f6) -- FILTER's conjuncts too (f1, f2) -- except under a filter on a
  table the operands read, where every operand runs (f9, f10).
* c: a FILTER over one column -- DISTINCT or ALL of it (c32, c33), or a
  CALCULATE boolean filter on one column (c36) -- has no first step; ALL of
  two columns, ALL / VALUES of the table have one; RELATED(...) IN V is a
  column predicate (c38).
* b: a branch omitted or written BLANK() in a TRUE / FALSE IF or SWITCH is
  FALSE (#276).
* r: RANKX's value and PERCENTILEX's k are caught by IFERROR, SAMPLE's N,
  CONCATENATEX's orderBy and a SUMMARIZECOLUMNS filter table not
  (#267).
* y: DIVIDE's alternate is a literal, a typed-literal call or a VAR bound to
  one; arithmetic, concatenation, a signed call and a VAR bound to a measure
  are refused, in a branch never taken too (#270).
* k: a refused CALCULATE boolean filter is refused in a branch never taken
  (#271); a bare text column fails to convert ("x" to
  True/False, #280).
* m: a table of several values in arithmetic is an error
  (#275).

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v",
[probe]) per probe, F:/_desktop_oracle/build_b255d.py; TM(n, d, z) = (7, 2,
1), (7, 0, 0), (-7, 2, 1), (7, BLANK, 0), TS(s, k) = ("1", 1), ("x", 0),
("3", 1), TC(n, d) = (1, 2), (2, 3), TD(key, lab) = (0, "zero"), (2, "two")
with TM[d] -> TD[key]. ERROR is an error answer; b8 is Desktop's FALSE.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]},
    "TS": {"columns": ["s", "k"], "rows": [["1", 1], ["x", 0], ["3", 1]]},
    "TC": {"columns": ["n", "d"], "rows": [[1, 2], [2, 3]]},
    "TD": {"columns": ["key", "lab"], "rows": [[0, "zero"], [2, "two"]]},
}
RELS = [{"FromTable": "TM", "FromColumn": "d", "ToTable": "TD", "ToColumn": "key", "IsActive": True}]
HOME = {
    "m_zero": "COUNTROWS(ALL(TM)) - 4",
    "m_dnz": "SELECTEDVALUE(TM[d]) <> 0",
    "m_cnt": "COUNTROWS(TM)",
    "m_and_d": "IF(SELECTEDVALUE(TM[d]) <> 0 && MOD(7, SELECTEDVALUE(TM[d])) = 1, 1)",
}
ERROR = "ERROR"


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {**HOME, "p": dax}, relationships=RELS))
    if "p" in eng.eval_errors:
        return ERROR
    return got


# (probe id, expression, Desktop's answer)
B255D = [
    ('d1_chain_eq', 'IF(0 = 0 = TRUE(), 1, 0)', 1),
    ('d2_chain_filter', 'CALCULATE(COUNTROWS(TM), TM[d] = 0 = TRUE())', 2),
    ('d3_chain_lt_eq', 'IF(1 < 2 = TRUE(), 1, 0)', 1),
    ('d4_chain_rows', 'COUNTROWS(FILTER(TM, TM[d] = 2 = TRUE()))', 2),
    ('d5_chain_int', 'IF(2 = 2 = 2, 1, 0)', ERROR),
    ('d6_chain_text', 'IF("a" = "A" = TRUE(), 1, 0)', 1),
    ('d7_chain_ne', 'IF(1 = 2 <> FALSE(), 1, 0)', 0),
    ('d8_chain_three', 'IF(1 = 1 = TRUE() = TRUE(), 1, 0)', 1),
    ('d9_chain_in_eq', 'IF(2 IN {1, 2} = TRUE(), 1, 0)', 1),
    ('d10_chain_in_filter', 'CALCULATE(COUNTROWS(TM), TM[d] IN {0, 2} = TRUE())', 3),
    ('d11_chain_eq_in', 'IF(1 = 1 IN {TRUE()}, 1, 0)', ERROR),
    ('f1_filter_free_right', 'COUNTROWS(FILTER(TM, MOD(TM[n], TM[d]) = 1 && [m_zero] <> 0))', None),
    ('f2_gs_free_right', 'COUNTROWS(FILTER(GENERATESERIES(-1, 2), MOD(7, [Value]) = 1 && [m_zero] <> 0))', None),
    ('f3_aggregate_operand', 'SUMX(TM, IF(MOD(TM[n], TM[d]) = 1 && COUNTROWS(TD) = 0, 1, 0))', 0),
    ('f4_and_fn_free_right', 'SUMX(TM, IF(AND(MOD(TM[n], TM[d]) = 1, [m_zero] <> 0), 1, 0))', 0),
    ('f5_free_err_bound_false', 'SUMX(TM, IF(TM[d] = 99 && MOD(1, [m_zero]) = 1, 1, 0))', ERROR),
    ('f6_two_free_first_err', 'SUMX(TM, IF(MOD(1, [m_zero]) = 1 && [m_zero] <> 0 && TM[d] <> 0, 1, 0))', 0),
    ('f7_paren_free', 'SUMX(TM, IF(([m_zero] <> 0 || FALSE()) && MOD(TM[n], TM[d]) = 1, 1, 0))', 0),
    ('f8_countx_free_right', 'COUNTX(TM, IF(MOD(TM[n], TM[d]) = 1 && [m_zero] <> 0, 1))', None),
    ('f9_var_filtered_outside',
     'CALCULATE(VAR a = SELECTEDVALUE(TM[d]) <> 0 RETURN SUMX(TM, IF(a && MOD(TM[n], TM[d]) = 1, 1, 0)), TM[z] = 0)', ERROR),
    ('f10_inline_filtered',
     'CALCULATE(SUMX(TM, IF(SELECTEDVALUE(TM[d]) <> 0 && MOD(TM[n], TM[d]) = 1, 1, 0)), TM[z] = 0)', ERROR),
    ('c31_table_one_col', 'COUNTROWS(FILTER(TM, MOD(7, TM[d]) = 1 && TM[d] <> 0))', 2),
    ('c32_distinct_col', 'COUNTROWS(FILTER(DISTINCT(TM[d]), MOD(7, TM[d]) = 1 && TM[d] <> 0))', ERROR),
    ('c33_all_col', 'COUNTROWS(FILTER(ALL(TM[d]), MOD(7, TM[d]) = 1 && TM[d] <> 0))', ERROR),
    ('c34_all_table', 'COUNTROWS(FILTER(ALL(TM), MOD(TM[n], TM[d]) = 1 && TM[d] <> 0))', 2),
    ('c35_values_table', 'COUNTROWS(FILTER(VALUES(TM), MOD(TM[n], TM[d]) = 1 && TM[d] <> 0))', 2),
    ('c36_calc_one_col', 'CALCULATE(COUNTROWS(TM), MOD(7, TM[d]) = 1 && TM[d] <> 0)', ERROR),
    ('c37_calc_filter_one_col', 'CALCULATE(COUNTROWS(TM), FILTER(TM, MOD(7, TM[d]) = 1 && TM[d] <> 0))', 2),
    ('c38_related_in', 'COUNTROWS(FILTER(TM, MOD(TM[n], TM[d]) = 1 && RELATED(TD[key]) IN {2}))', 2),
    ('c39_all_two_cols', 'COUNTROWS(FILTER(ALL(TM[n], TM[d]), MOD(TM[n], TM[d]) = 1 && TM[d] <> 0))', 2),
    ('b1_true_then', 'IF(ISBLANK(IF(FALSE(), TRUE())), 1, 0)', 0),
    ('b2_explicit_blank', 'IF(ISBLANK(IF(FALSE(), FALSE(), BLANK())), 1, 0)', 0),
    ('b3_switch', 'IF(ISBLANK(SWITCH(1, 2, TRUE())), 1, 0)', 0),
    ('b4_comparison', 'IF(ISBLANK(IF(FALSE(), 1 = 1)), 1, 0)', 0),
    ('b5_measure', 'IF(ISBLANK(IF(FALSE(), [m_dnz])), 1, 0)', 0),
    ('b6_if_eager', 'IF(ISBLANK(IF.EAGER(FALSE(), TRUE())), 1, 0)', 0),
    ('b7_nested_if', 'IF(ISBLANK(IF(FALSE(), IF(TRUE(), FALSE()))), 1, 0)', 0),
    ('b8_value', 'IF(FALSE(), TRUE())', False),
    ('b9_strict_false', 'COUNTROWS(FILTER(TM, IF(TM[z] = 1, TRUE()) == FALSE()))', 2),
    ('r29_rankx_value', 'IFERROR(RANKX(ALL(TM), TM[n], MOD(1, [m_zero])), -1)', -1),
    ('r30_percentilex_k', 'IFERROR(PERCENTILEX.INC(TM, TM[n], MOD(1, [m_zero])), -1)', -1),
    ('r31_sample_n', 'IFERROR(COUNTROWS(SAMPLE(MOD(1, [m_zero]), TM, TM[n])), -1)', ERROR),
    ('r32_concatx_orderby', 'IFERROR(LEN(CONCATENATEX(TM, TM[n], ",", MOD(1, [m_zero]))), -1)', ERROR),
    ('r34_scol_filter_once',
     'IFERROR(COUNTROWS(SUMMARIZECOLUMNS(TM[d], TREATAS({MOD(1, [m_zero])}, TM[d]), "v", COUNTROWS(TM))), -1)', ERROR),
    ('r33_concatx_in_row', 'SUMX(TM, IFERROR(LEN(CONCATENATEX(TM, TM[n], FORMAT(MOD(1, [m_zero]), "0"))), -1))', -4),
    ('y19_untaken_refusal', 'IF([m_zero] = 1, DIVIDE(1, 1, MOD(1, [m_zero])), 0)', ERROR),
    ('y20_alt_arith', 'DIVIDE(1, 0, 1 + 1)', ERROR),
    ('y21_alt_neg_currency', 'DIVIDE(1, 0, -CURRENCY(2))', ERROR),
    ('y22_alt_time', 'DIVIDE(1, 0, TIME(6, 0, 0))', 0.25),
    ('y23_alt_var_measure', 'VAR k = [m_zero] RETURN DIVIDE(1, 0, k)', ERROR),
    ('y24_alt_concat', 'DIVIDE(1, 0, "7" & "")', ERROR),
    ('y25_alt_paren', 'DIVIDE(1, 0, (-3))', -3),
    ('y26_alt_leading_dot', 'DIVIDE(1, 0, .5)', 0.5),
    ('y27_alt_int_plus', 'DIVIDE(1, 0, INT(2.5) + 0)', ERROR),
    ('k38_untaken_refusal', 'IF([m_zero] = 1, CALCULATE(COUNTROWS(TM), TM[d] < [m_cnt]), 0)', ERROR),
    ('k39_calc_in_aggregate', 'CALCULATE(COUNTROWS(TM), TM[d] < MAXX(ALL(TM), CALCULATE(MAX(TM[d]))))', 2),
    ('k40_bare_measure', 'CALCULATE(COUNTROWS(TM), [m_dnz])', ERROR),
    ('k41_text_column', 'CALCULATE(COUNTROWS(TS), TS[s])', ERROR),
    ('k45_text_column_inner', 'CALCULATE(CALCULATE(COUNTROWS(TS), TS[s]), TS[s] = "x")', ERROR),
    ('k42_not_column', 'CALCULATE(COUNTROWS(TM), NOT TM[d])', 2),
    ('k43_keep_column', 'CALCULATE(CALCULATE(COUNTROWS(TM), KEEPFILTERS(TM[d])), TM[n] = 7)', 1),
    ('k44_calctable_in', 'CALCULATE(COUNTROWS(TM), TM[d] IN CALCULATETABLE(VALUES(TD[key]), TD[lab] = "two"))', 2),
    ('m1_values_eq', 'IF(VALUES(TM[d]) = 7, 1, 0)', ERROR),
    ('m2_iferror_values_eq', 'IFERROR(IF(VALUES(TM[d]) = 7, 1, 0), -1)', -1),
    ('m3_values_plus', 'VALUES(TM[d]) + 0', ERROR),
]


@pytest.mark.parametrize("probe,expr,want", B255D, ids=[p for p, _e, _w in B255D])
def test_matches_desktop(probe, expr, want):
    got = _engine(expr)
    if want is None or want is ERROR or isinstance(want, bool):
        assert got is want or got == want and type(got) is type(want)
    else:
        assert got == pytest.approx(want) and not isinstance(got, bool)


def test_desktop_error_messages():
    """The refusals and errors name what Desktop's messages name (in its
    Norwegian, here in English)."""
    def err(dax):
        eng = de.DAXEngine()
        eng.evaluate_measure("p", de.DAXContext(TABLES, {**HOME, "p": dax}, relationships=RELS))
        return str(eng.eval_errors.get("p", ""))
    assert "constant numeric value" in err("IF([m_zero] = 1, DIVIDE(1, 1, MOD(1, [m_zero])), 0)")   # y19
    assert "constant numeric value" in err("VAR k = [m_zero] RETURN DIVIDE(1, 0, k)")               # y23
    assert "PLACEHOLDER" in err("IF([m_zero] = 1, CALCULATE(COUNTROWS(TM), TM[d] < [m_cnt]), 0)")  # k38
    assert "Cannot convert value 'x' of type Text to type True/False" in err(
        "CALCULATE(COUNTROWS(TS), TS[s])")                                                         # k41
    assert "multiple values" in err("VALUES(TM[d]) + 0")                                            # m3
    assert "CONTAINSROW" in err("IF(1 = 1 IN {TRUE()}, 1, 0)")                                      # d11
