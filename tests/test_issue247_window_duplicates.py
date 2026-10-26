"""Issue #247: OFFSET / INDEX / WINDOW over a relation that may have
duplicate rows.

Power BI Desktop 2.152 refuses an explicit relation of OFFSET, INDEX or
WINDOW that it cannot PROVE duplicate-free when no MATCHBY is given: "The
relation parameter of INDEX may have duplicate rows. This is not allowed."
(its Norwegian message, translated). The refusal is static: it does not
depend on the data -- S's rows are all distinct and ORDERBY(S[Cat]) sorts
them without a tie, yet ALL(S) is refused -- nor on the context: an empty
relation, an empty context, a total, a filter and a row all fail alike, and
so does a query's own expression. A model table is proven duplicate-free by
a key column only (the one side of a relationship, active or not, both
sides of a 1:1, never a many-to-many side); column lists, DISTINCT,
SUMMARIZE, GROUPBY, SUMMARIZECOLUMNS, ALLEXCEPT, an EXCEPT / INTERSECT of a
set, a CROSSJOIN of sets, TREATAS of a set and a SELECTCOLUMNS keeping the
key are sets; FILTER, TOPN, ADDCOLUMNS, CALCULATETABLE, VALUES, ALLSELECTED,
ALLNOBLANKROW, EXCEPT and INTERSECT keep the kind they wrap; UNION, table
constructors, DATATABLE, ROW, TREATAS of a constructor and a CROSSJOIN with
a keyed table are not, nor are GENERATESERIES / CALENDAR where a current row
is needed. 0.9.147 answered every one of these relations.

The refusal fails the whole query, also from a measure the query only
references (0.9.147 degraded that measure to BLANK). IFERROR / ISERROR around
the function itself do not catch it; around a reference to its measure they
do. SUMX, AVERAGEX, MINX, MAXX, COUNTX and FILTER over an empty table refuse
it too, outside IFERROR / IF (0.9.147 evaluated nothing over zero rows), each
call checked with the variables of the measure it is written in, whatever
ran before.

Expected values are Desktop's answers, by probe id: build_b253.py (Desktop
answers F:\\_tmp_pbix\\open1\\b247\\desk.json), build_b253b.py (group A for the
conformance fixture's F, B / C for the relation forms, E for empty
iterators, F for IFERROR), build_b253c.py (B for the conformance fixture,
C / D for the relation forms, E for empty iterators), build_b253d.py (B for
the other empty iterators, D / E / F for the relation forms; Desktop answers
F:\\_tmp_pbix\\rel148\\desk\\desk_b253d.json) and build_b247.py
(b247). The other defects these
batteries exposed have their own tests, which import this model:
MATCHBY duplicates, ROWNUMBER ties, built columns, the appended key, the
current row, ORDERBY's order argument, ALLEXCEPT as a table and a relation
narrower than the row.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

_D = [datetime(2024, m, 15) for m in range(1, 7)]
_F = [[1, 100.0, datetime(2024, 1, 15)], [2, 200.0, datetime(2024, 2, 15)],
      [1, 50.0, datetime(2024, 4, 15)], [3, 300.0, datetime(2024, 5, 15)]]

# build_b253.py's and build_b253b.py's tables (shared ones are identical)
TABLES = {
    "S": {"columns": ["Cat", "Reg", "V"],
          "rows": [["A", "N", 30], ["B", "N", 35], ["C", "S", 40], ["D", "S", 75]]},
    "SD": {"columns": ["a", "b"], "rows": [[1, "x"], [1, "x"], [2, "y"]]},
    "T3": {"columns": ["Cat", "Reg", "V"],
           "rows": [["A", "N", 1], ["B", "N", 1], ["C", "S", 2], ["D", "S", 3]]},
    "Fact": {"columns": ["Region", "Value"],
             "rows": [["N", 10], ["S", 20], ["N", 5], ["W", 7]]},
    "Dim": {"columns": ["Region", "Name"],
            "rows": [["N", "North"], ["S", "South"], ["W", "West"]]},
    "Dim2": {"columns": ["K", "Grp"], "rows": [[2, "g1"], [1, "g1"], [3, "g2"]]},
    "F2": {"columns": ["K", "Amt"], "rows": [[1, 100], [2, 200], [3, 300], [3, 1]]},
    "D5": {"columns": ["K", "N"], "rows": [[1, "a"], [2, "b"], [3, "c"]]},
    "F5": {"columns": ["K", "Amt"], "rows": [[1, 10], [1, 20], [2, 30]]},
    "A1": {"columns": ["k", "x"], "rows": [[1, "p"], [2, "q"]]},
    "B1": {"columns": ["k", "y"], "rows": [[1, "r"], [2, "s"]]},
    "MA": {"columns": ["k", "x"], "rows": [[1, "p"], [2, "q"]]},
    "MB": {"columns": ["k", "y"], "rows": [[1, "r"], [2, "s"]]},
    "Dim3": {"columns": ["Z", "Grp", "K"], "rows": [["b", "g", 1], ["a", "g", 2], ["c", "h", 3]]},
    "F3": {"columns": ["K", "Amt"], "rows": [[1, 1], [2, 2], [3, 3], [3, 4]]},
    "Q": {"columns": ["Reg", "X", "Y"], "rows": [["N", 1, "b"], ["N", 2, "a"], ["S", 3, "c"]]},
    "Q2": {"columns": ["Reg", "Y", "X"], "rows": [["N", "b", 1], ["N", "a", 2], ["S", "c", 3]]},
    "QM": {"columns": ["Cat", "Reg", "V"],
           "rows": [["A", "N", 1], ["B", "N", 2], ["A", "S", 3], ["B", "S", 4]]},
    "F": {"columns": ["k", "v", "d"], "rows": [list(r) for r in _F]},
    "K": {"columns": ["k", "grp"], "rows": [[1, "X"], [2, "X"], [3, "Y"], [4, "Z"]]},
    "D": {"columns": ["Date", "M", "Q"],
          "rows": [[d, i + 1, 1 if i < 3 else 2] for i, d in enumerate(_D)]},
    "FJ": {"columns": ["k", "v", "d"], "rows": [list(r) for r in _F]},
    "DJ": {"columns": ["Date", "M", "Q"],
           "rows": [[d, i + 1, 1 if i < 3 else 2] for i, d in enumerate(_D)]},
    "P": {"columns": ["z"], "rows": [[1]]},
}


def _rel(ft, fc, tt, tc, active=1, fcard=2, tcard=1, cross=1):
    return {"FromTable": ft, "FromColumn": fc, "ToTable": tt, "ToColumn": tc,
            "IsActive": active, "FromCardinality": fcard, "ToCardinality": tcard,
            "CrossFilteringBehavior": cross}


RELS = [
    _rel("Fact", "Region", "Dim", "Region"),
    _rel("F2", "K", "Dim2", "K"),
    _rel("F5", "K", "D5", "K", active=0),                  # inactive
    _rel("A1", "k", "B1", "k", fcard=1, tcard=1, cross=2),  # 1:1
    _rel("MA", "k", "MB", "k", fcard=2, tcard=2),           # many-to-many
    _rel("F3", "K", "Dim3", "K"),
    _rel("F", "k", "K", "k"),
    _rel("F", "d", "D", "Date"),                            # today's fixture: F many
    _rel("DJ", "Date", "FJ", "d"),                          # July's: FJ the one side
]

# the batteries' helper measures, verbatim
HELPERS = {
    "h_sv": "SUM(S[V])",
    "h_off_all": "CALCULATE(SUM(S[V]), OFFSET(-1, ALL(S), ORDERBY(S[Cat])))",
    "h_rn_reg": "ROWNUMBER(ALL(S), ORDERBY(S[Reg]))",
    "h_o_allcols": "CONCATENATEX(OFFSET(-1, ALL(S[Reg], S[Cat]), ORDERBY(S[Cat])), S[Cat])",
    "h_o_allcols_p": "CONCATENATEX(OFFSET(-1, ALL(S[Reg], S[Cat]), ORDERBY(S[Cat]), "
                     "PARTITIONBY(S[Reg])), S[Cat])",
    "h_o_distinct": "CONCATENATEX(OFFSET(-1, DISTINCT(ALL(S)), ORDERBY(S[V])), S[Cat])",
    "h_o_summarize": "CONCATENATEX(OFFSET(-1, SUMMARIZE(ALL(S), S[Reg], S[Cat]), "
                     "ORDERBY(S[Cat])), S[Cat])",
    "h_o_mb_cat": "CONCATENATEX(OFFSET(-1, ALL(S), ORDERBY(S[V]), MATCHBY(S[Cat])), S[Cat])",
    "h_o_mb_reg": "CONCATENATEX(OFFSET(-1, ALL(S), ORDERBY(S[V]), MATCHBY(S[Reg])), S[Cat])",
    "h_o_mb_part": "CONCATENATEX(OFFSET(-1, ALL(S), ORDERBY(S[V]), PARTITIONBY(S[Reg]), "
                   "MATCHBY(S[V])), S[Cat])",
    "h_o_dim2": "CALCULATE(SUM(Dim2[K]), OFFSET(-1, ALL(Dim2), ORDERBY(Dim2[Grp])))",
    "h_o_dim_name": "CONCATENATEX(OFFSET(-1, ALL(Dim), ORDERBY(Dim[Name])), Dim[Name])",
    "h_o_inactive": "CONCATENATEX(OFFSET(-1, ALL(D5), ORDERBY(D5[N])), D5[N])",
    "h_o_m2m": "CONCATENATEX(OFFSET(-1, ALL(MB), ORDERBY(MB[y])), MB[y])",
    "h_o_sd_distinct": "CONCATENATEX(OFFSET(-1, DISTINCT(ALL(SD)), ORDERBY(SD[a])), SD[b])",
    "h_w_allcols": 'CONCATENATEX(WINDOW(1, ABS, 0, REL, ALL(S[Reg], S[Cat]), ORDERBY(S[Cat])), '
                   'S[Cat], "", S[Cat], ASC)',
    "h_w_mb_cat": 'CONCATENATEX(WINDOW(1, ABS, 0, REL, ALL(S), ORDERBY(S[V]), MATCHBY(S[Cat])), '
                  'S[Cat], "", S[Cat], ASC)',
    "h_rn_v": "ROWNUMBER(ALL(S), ORDERBY(S[V]))",
    "h_rn_reg_pv": "ROWNUMBER(ALL(S), ORDERBY(S[Reg]), PARTITIONBY(S[V]))",
    "h_rn_t3_p": "ROWNUMBER(ALL(T3), ORDERBY(T3[V]), PARTITIONBY(T3[Reg]))",
    "h_rn_t3": "ROWNUMBER(ALL(T3), ORDERBY(T3[V]))",
    "h_rn_regcat": "ROWNUMBER(ALL(S[Reg], S[Cat]), ORDERBY(S[Reg]))",
    "h_rn_dim2": "ROWNUMBER(ALL(Dim2), ORDERBY(Dim2[Grp]))",
    "h_rn_sd": "ROWNUMBER(ALL(SD), ORDERBY(SD[a], SD[b]))",
    "h_rn_sd_distinct": "ROWNUMBER(DISTINCT(ALL(SD)), ORDERBY(SD[a]))",
    "h_rn_regcol": "ROWNUMBER(ALL(S[Reg]), ORDERBY(S[Reg]))",
    "h_rn_mb_reg": "ROWNUMBER(ALL(S), ORDERBY(S[V]), MATCHBY(S[Reg]))",
    "h_rk_reg": "RANK(DENSE, ALL(S), ORDERBY(S[Reg]))",
    "h_rk_reg_skip": "RANK(SKIP, ALL(S), ORDERBY(S[Reg]))",
    "h_rk_v": "RANK(DENSE, ALL(S), ORDERBY(S[V]))",
    "h_rk_regcat": "RANK(DENSE, ALL(S[Reg], S[Cat]), ORDERBY(S[Reg]))",
    "h_rk_col": "RANK(DENSE, ALL(S[Cat]), ORDERBY(S[Cat]))",
    "h_rk_regcol": "RANK(DENSE, ALL(S[Reg]), ORDERBY(S[Reg]))",
    "h_rk_dim2": "RANK(DENSE, ALL(Dim2), ORDERBY(Dim2[Grp]))",
    "h_rk_dim2_skip": "RANK(SKIP, ALL(Dim2), ORDERBY(Dim2[Grp]))",
    "h_rk_sd": "RANK(DENSE, ALL(SD), ORDERBY(SD[b]))",
    "h_rk_mb_reg": "RANK(DENSE, ALL(S), ORDERBY(S[V]), MATCHBY(S[Reg]))",
    "h_rk_mb_cat": "RANK(DENSE, ALL(S), ORDERBY(S[Reg]), MATCHBY(S[Cat]))",
    "h_rk_dim_name": "RANK(DENSE, ALL(Dim), ORDERBY(Dim[Name]))",
    # build_b253b.py
    "h_bad_order": "CONCATENATEX(INDEX(1, ALL(S[Cat], S[V]), ORDERBY(S[V], S[Cat])), S[Cat])",
    "h_rn_dim3": "ROWNUMBER(ALL(Dim3), ORDERBY(Dim3[Grp]))",
    "h_rn_q_yx": "ROWNUMBER(ALL(Q[Reg], Q[Y], Q[X]), ORDERBY(Q[Reg]))",
    "h_rn_q_xy": "ROWNUMBER(ALL(Q[Reg], Q[X], Q[Y]), ORDERBY(Q[Reg]))",
    "h_rn_q2_xy": "ROWNUMBER(ALL(Q2[Reg], Q2[X], Q2[Y]), ORDERBY(Q2[Reg]))",
    "h_rn_dim2_desc": "ROWNUMBER(ALL(Dim2), ORDERBY(Dim2[Grp], DESC))",
    "h_rn_q_sum": "ROWNUMBER(SUMMARIZE(Q, Q[Reg], Q[Y]), ORDERBY(Q[Reg]))",
    "h_rk_added": 'RANK(DENSE, ADDCOLUMNS(ALL(S[Reg]), "t", CALCULATE(SUM(S[V]))), '
                  'ORDERBY([t], DESC))',
    "h_rn_omit_reg": "ROWNUMBER(ORDERBY(S[Reg]))",
    "h_off_omit": "CALCULATE(SUM(S[V]), OFFSET(-1, , ORDERBY(S[Cat])))",
    "h_rn_sd2": "ROWNUMBER(ALL(SD), ORDERBY(SD[a], ASC, SD[b], ASC))",
    "h_mb_part": "CONCATENATEX(OFFSET(-1, ALL(QM), ORDERBY(QM[V]), PARTITIONBY(QM[Reg]), "
                 "MATCHBY(QM[Cat])), QM[Cat])",
    "h_mb_rn_part": "ROWNUMBER(ALL(QM), ORDERBY(QM[V]), PARTITIONBY(QM[Reg]), MATCHBY(QM[Cat]))",
    "h_mb_rn_tie": "ROWNUMBER(ALL(S), ORDERBY(S[Reg]), MATCHBY(S[Cat]))",
    "h_mb_two": "CONCATENATEX(OFFSET(-1, ALL(QM), ORDERBY(QM[V]), MATCHBY(QM[Cat], QM[Reg])), "
                "QM[Cat])",
    # build_b247.py
    "off_p": "CALCULATE(SUM(S[V]), OFFSET(-1, ALL(S), ORDERBY(S[V]), PARTITIONBY(S[Reg])))",
    # build_b253c.py
    "h_mb_dup": "CONCATENATEX(INDEX(1, ALL(S), ORDERBY(S[V]), MATCHBY(S[Reg])), S[Cat])",
    "h_built": 'VAR T = {"b", "a"} RETURN CONCATENATEX(T, ROWNUMBER(T, ORDERBY([Value])))',
    "h_space": "CONCATENATEX(INDEX(1, ALL(S[Cat], S[V]), ORDERBY(S[V] DESC)), S[Cat])",
    "h_var_idx": "VAR r = ALL(S) RETURN CONCATENATEX(INDEX(1, r, ORDERBY(S[V])), S[Cat])",
    "h_var_set": "VAR t = ALL(S[Cat]) RETURN CALCULATE(SUM(S[V]), OFFSET(-1, t, "
                 "ORDERBY(S[Cat])))",
    # build_b253d.py
    "h_part_built": 'CONCATENATEX(GENERATESERIES(1, 3), CONCATENATEX(INDEX(1, GENERATESERIES(1, 3), '
                    'ORDERBY([Value]), PARTITIONBY([Value])), [Value]), ",", [Value], ASC)',
}


def ev(expr: str, filters: dict | None = None):
    """(value, error) of ``expr`` as a measure of P beside the helpers: the
    error is the message the engine reports for the measure, else None."""
    de._engine.eval_errors.clear()
    measures = dict(HELPERS, m=expr)
    homes = {k: "P" for k in measures}
    kw = {"filter_context": filters} if filters else {}
    value = de.evaluate_measures_batch(["m"], TABLES, measures, relationships=RELS,
                                       measure_tables=homes, **kw)["m"]
    return value, de._engine.eval_errors.get("m")


def refused(expr: str, filters: dict | None = None) -> str:
    """The error of a measure Desktop fails; asserts it fails."""
    value, err = ev(expr, filters)
    assert value is None and err, f"{expr}: answered {value!r}"
    return err


def ix(rel, order, col, pos=1, extra=""):
    """build_b253.py's INDEX at the top level (no current row needed)."""
    return f"CONCATENATEX(INDEX({pos}, {rel}, ORDERBY({order}){extra}), {col})"


def per(t, k, m):
    return f'CONCATENATEX({t}, [{m}], ",", {t}[{k}], ASC)'


def per_s(m):
    return per("S", "Cat", m)


DUP = "may have duplicate rows"

# ---- refused: (probe, expression, function) -- Desktop ERR_DUP ----

REFUSED = [
    # build_b253.py: model tables without a key, and what keeps their kind
    ("i_all_s", ix("ALL(S)", "S[V]", "S[Cat]"), "INDEX"),
    ("i_s", ix("S", "S[V]", "S[Cat]"), "INDEX"),
    ("i_values_s", ix("VALUES(S)", "S[V]", "S[Cat]"), "INDEX"),
    ("i_allsel_s", ix("ALLSELECTED(S)", "S[V]", "S[Cat]"), "INDEX"),
    ("i_filter_all_s", ix("FILTER(ALL(S), S[V] > 0)", "S[V]", "S[Cat]"), "INDEX"),
    ("i_topn_s", ix("TOPN(3, ALL(S), S[V], ASC)", "S[V]", "S[Cat]"), "INDEX"),
    ("i_addcols_s", ix('ADDCOLUMNS(ALL(S), "x", 1)', "S[V]", "S[Cat]"), "INDEX"),
    ("i_selcols_s", ix('SELECTCOLUMNS(ALL(S), "c", S[Cat], "v", S[V])', "[v]", "[c]"), "INDEX"),
    ("i_selcols_dim_nokey", ix('SELECTCOLUMNS(ALL(Dim), "n", Dim[Name])', "[n]", "[n]"), "INDEX"),
    ("i_union_cols", ix("UNION(ALL(S[Cat]), ALL(S[Cat]))", "S[Cat]", "S[Cat]"), "INDEX"),
    ("i_ctor", ix('{"b", "a", "c"}', "[Value]", "[Value]"), "INDEX"),
    ("i_ctor_dup", ix('{"b", "a", "a"}', "[Value]", "[Value]"), "INDEX"),
    ("i_datatable", ix('DATATABLE("k", STRING, {{"b"}, {"a"}})', "[k]", "[k]"), "INDEX"),
    ("i_row", ix('ROW("x", 7)', "[x]", "[x]"), "INDEX"),
    ("i_all_fact", ix("ALL(Fact)", "Fact[Value]", "Fact[Region]"), "INDEX"),
    ("i_m2m_a", ix("ALL(MA)", "MA[x]", "MA[x]"), "INDEX"),
    ("i_m2m_b", ix("ALL(MB)", "MB[y]", "MB[y]"), "INDEX"),
    ("i_sd_all", ix("ALL(SD)", "SD[a]", "SD[b]"), "INDEX"),
    ("o_ctor", 'VAR T = {"b", "a", "c"} RETURN CONCATENATEX(T, CONCATENATEX(OFFSET(-1, T, '
               'ORDERBY([Value])), [Value]), ",", [Value])', "OFFSET"),
    ("o_genseries", 'VAR T = GENERATESERIES(1, 3) RETURN CONCATENATEX(T, CONCATENATEX('
                    'OFFSET(-1, T, ORDERBY([Value])), [Value]), ",", [Value])', "OFFSET"),
    ("o_m2m", per("MB", "k", "h_o_m2m"), "OFFSET"),
    ("w_abs_all_s", 'CONCATENATEX(WINDOW(1, ABS, 2, ABS, ALL(S), ORDERBY(S[V])), S[Cat], "", '
                    'S[Cat], ASC)', "WINDOW"),
    # statically: an empty relation, an empty context
    ("z_empty_rel", ix("FILTER(ALL(S), S[V] > 1000)", "S[V]", "S[Cat]"), "INDEX"),
    ("z_empty_ctx", 'CALCULATE([h_off_all], S[Cat] = "ZZZ")', "OFFSET"),
    # build_b253b.py group A: the conformance fixture's F, the many side today
    ("a_cf_index", "SUMX(INDEX(1, F, ORDERBY(F[v])), F[v])", "INDEX"),
    ("a_cf_offset", "SUMX(F, SUMX(OFFSET(-1, F, ORDERBY(F[v])), F[v]))", "OFFSET"),
    ("a_cf_window", "SUMX(F, SUMX(WINDOW(1, ABS, 2, ABS, F, ORDERBY(F[v])), F[v]))", "WINDOW"),
    # (q_s_index / qd_s_index: the same refusal as a query and a query's measure)
    ("hq_s_index", "CONCATENATEX(INDEX(1, ALL(S), ORDERBY(S[V])), S[Cat])", "INDEX"),
    # groups B / C
    ("b_gs_off_inline", 'CONCATENATEX(GENERATESERIES(1, 3), CONCATENATEX(OFFSET(-1, '
                        'GENERATESERIES(1, 3), ORDERBY([Value])), [Value]), ",", [Value], ASC)',
     "OFFSET"),
    ("b_ctor_num_idx", ix("{3, 1, 2}", "[Value]", "[Value]"), "INDEX"),
    ("b_treatas_idx", ix('TREATAS({"S", "N"}, S[Reg])', "S[Reg]", "S[Reg]"), "INDEX"),
    ("c_cj_bag", ix("CROSSJOIN(ALL(S), ALL(Dim[Name]))", "S[V], ASC, Dim[Name], ASC",
                    "S[Cat] & Dim[Name]"), "INDEX"),
    ("c_cj_keyed", ix("CROSSJOIN(ALL(Dim), ALL(Dim2))", "Dim[Name], ASC, Dim2[K], ASC",
                      "Dim[Name] & Dim2[K]"), "INDEX"),
    ("c_except_bag", ix('EXCEPT(ALL(S), FILTER(ALL(S), S[Cat] = "A"))', "S[V]", "S[Cat]"),
     "INDEX"),
    ("c_selcols_drop", ix('SELECTCOLUMNS(ALL(S[Cat], S[Reg]), "c", S[Cat])', "[c]", "[c]"),
     "INDEX"),
    ("c_selcols_key_expr", ix('SELECTCOLUMNS(ALL(Dim), "r", Dim[Region] & "", "n", Dim[Name])',
                              "[n]", "[n]"), "INDEX"),
    ("c_calctable_all", ix('CALCULATETABLE(ALL(S), S[Reg] = "N")', "S[V]", "S[Cat]"), "INDEX"),
    ("c_fullorder", ix("ALL(S)", "S[V], ASC, S[Cat], ASC, S[Reg], ASC", "S[Cat]"), "INDEX"),
    # build_b253c.py group C: GENERATESERIES / CALENDAR where a current row is
    # needed (0.9.147: '1,12,23', ',1,2')
    ("c_gs_win_rel", 'CONCATENATEX(GENERATESERIES(1, 3), CONCATENATEX(WINDOW(-1, REL, 0, REL, '
                     'GENERATESERIES(1, 3), ORDERBY([Value])), [Value], ""), ",", [Value], ASC)',
     "WINDOW"),
    ("c_cal_off", 'VAR T = CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 3)) RETURN CONCATENATEX(T, '
                  'CONCATENATEX(OFFSET(-1, T, ORDERBY([Date])), DAY([Date])), ",", [Date], ASC)',
     "OFFSET"),
    # group D: a keyed table in a CROSSJOIN, INTERSECT of a bag, ALLNOBLANKROW
    # of a keyless table (0.9.147: 'NorthA', 'A', 'A')
    ("d_cj_keyed_set", ix("CROSSJOIN(ALL(Dim), ALL(S[Cat]))", "Dim[Name], ASC, S[Cat], ASC",
                          "Dim[Name] & S[Cat]"), "INDEX"),
    ("d_intersect_bag", ix("INTERSECT(ALL(S), ALL(S))", "S[V]", "S[Cat]"), "INDEX"),
    ("d_anbr_s", ix("ALLNOBLANKROW(S)", "S[V]", "S[Cat]"), "INDEX"),
]


@pytest.mark.parametrize("expr,fn", [c[1:] for c in REFUSED], ids=[c[0] for c in REFUSED])
def test_a_relation_that_may_have_duplicate_rows_is_refused(expr, fn):
    err = refused(expr)
    assert DUP in err and f"of {fn} " in err, err


# ---- accepted: (probe, expression, Desktop's answer) ----

ACCEPTED = [
    # build_b253.py: sets
    ("i_distinct_s", ix("DISTINCT(S)", "S[V]", "S[Cat]"), "A"),
    ("i_distinct_all_s", ix("DISTINCT(ALL(S))", "S[V]", "S[Cat]"), "A"),
    ("i_allcols_s", ix("ALL(S[Cat], S[Reg], S[V])", "S[V]", "S[Cat]"), "A"),
    ("i_all2_s", ix("ALL(S[Reg], S[V])", "S[V]", "S[Reg]"), "N"),
    ("i_summarize_s", ix("SUMMARIZE(S, S[Reg], S[Cat])", "S[Cat]", "S[Cat]"), "A"),
    ("i_summarize_add", ix('ADDCOLUMNS(SUMMARIZE(S, S[Reg]), "t", CALCULATE(SUM(S[V])))',
                           "[t], DESC", "S[Reg]"), "S"),
    ("i_groupby_s", ix("GROUPBY(S, S[Reg])", "S[Reg]", "S[Reg]"), "N"),
    ("i_sumcols_s", ix('SUMMARIZECOLUMNS(S[Reg], "t", SUM(S[V]))', "[t], DESC", "S[Reg]"), "S"),
    ("i_filter_cols_s", ix('FILTER(ALL(S[Cat], S[Reg]), S[Reg] = "S")', "S[Cat]", "S[Cat]"), "C"),
    ("i_values_col", ix("VALUES(S[Cat])", "S[Cat]", "S[Cat]"), "A"),
    ("i_topn_col", ix("TOPN(3, ALL(S[Cat]), S[Cat], DESC)", "S[Cat]", "S[Cat]"), "B"),
    ("i_addcols_col", ix('ADDCOLUMNS(ALL(S[Cat]), "x", 1)', "S[Cat]", "S[Cat]"), "A"),
    ("i_calctable_col", ix('CALCULATETABLE(VALUES(S[Cat]), S[Reg] = "S")', "S[Cat]", "S[Cat]"),
     "C"),
    ("i_selcols_col", ix('SELECTCOLUMNS(ALL(S[Cat]), "c", S[Cat])', "[c]", "[c]"), "A"),
    ("i_distinct_selcols", ix('DISTINCT(SELECTCOLUMNS(ALL(S), "r", S[Reg]))', "[r]", "[r]"), "N"),
    ("i_selcols_dim_key", ix('SELECTCOLUMNS(ALL(Dim), "r", Dim[Region], "n", Dim[Name])',
                             "[n]", "[n]"), "North"),
    ("i_except_cols", ix('EXCEPT(ALL(S[Cat]), {"A"})', "S[Cat]", "S[Cat]"), "B"),
    ("i_genseries", ix("GENERATESERIES(1, 3)", "[Value], DESC", "[Value]"), "3"),
    ("i_allcols_fact", ix("ALL(Fact[Region], Fact[Value])", "Fact[Value]", "Fact[Region]"), "N"),
    ("i_sd_distinct", ix("DISTINCT(SD)", "SD[a]", "SD[b]"), "x"),
    ("i_sd_allcols", ix("ALL(SD[a], SD[b])", "SD[a]", "SD[b]"), "x"),
    # model tables with a key: the one side, inactive too, both sides of a 1:1
    ("i_all_dim_name", ix("ALL(Dim)", "Dim[Name]", "Dim[Name]"), "North"),
    ("i_values_dim", ix("VALUES(Dim)", "Dim[Name]", "Dim[Name]"), "North"),
    ("i_filter_dim", ix('FILTER(ALL(Dim), Dim[Region] <> "N")', "Dim[Name]", "Dim[Name]"),
     "South"),
    ("i_inactive", ix("ALL(D5)", "D5[N]", "D5[N]"), "a"),
    ("i_11_a", ix("ALL(A1)", "A1[x]", "A1[x]"), "p"),
    ("i_11_b", ix("ALL(B1)", "B1[y]", "B1[y]"), "r"),
    ("o_allcols", per_s("h_o_allcols"), ",A,B,C"),
    ("o_allcols_p", per_s("h_o_allcols_p"), ",A,,C"),
    ("o_distinct", per_s("h_o_distinct"), ",A,B,C"),
    ("o_summarize", per_s("h_o_summarize"), ",A,B,C"),
    ("o_selcols_col", 'VAR T = SELECTCOLUMNS(ALL(S[Cat]), "c", S[Cat]) RETURN CONCATENATEX(T, '
                      'CONCATENATEX(OFFSET(-1, T, ORDERBY([c])), [c]), ",", [c])', ",A,B,C"),
    ("o_dim_name", per("Dim", "Region", "h_o_dim_name"), ",North,South"),
    ("o_inactive", per("D5", "K", "h_o_inactive"), ",a,b"),
    ("o_sd_distinct", 'CONCATENATEX(DISTINCT(SD), [h_o_sd_distinct], ",", SD[a], ASC)', ",x"),
    ("w_allcols", per_s("h_w_allcols"), "A,AB,ABC,ABCD"),
    # build_b253b.py group A: July's orientation, FJ the one side
    ("a_cfj_index", "SUMX(INDEX(1, FJ, ORDERBY(FJ[v])), FJ[v])", 50),
    ("a_cfj_offset", "SUMX(FJ, SUMX(OFFSET(-1, FJ, ORDERBY(FJ[v])), FJ[v]))", 350),
    ("a_cfj_window", "SUMX(FJ, SUMX(WINDOW(1, ABS, 2, ABS, FJ, ORDERBY(FJ[v])), FJ[v]))", 600),
    # group B: GENERATESERIES / CALENDAR where no current row is needed
    ("b_gs_idx_asc", ix("GENERATESERIES(1, 3)", "[Value]", "[Value]"), "1"),
    ("b_gs_idx_var", "VAR T = GENERATESERIES(1, 3) RETURN CONCATENATEX(INDEX(1, T, "
                     "ORDERBY([Value], DESC)), [Value])", "3"),
    ("b_gs_idx_perrow_var", 'VAR T = GENERATESERIES(1, 3) RETURN CONCATENATEX(T, CONCATENATEX('
                            'INDEX(1, T, ORDERBY([Value])), [Value]), ",", [Value], ASC)',
     "1,1,1"),
    ("b_gs_win_inline", 'CONCATENATEX(WINDOW(1, ABS, 2, ABS, GENERATESERIES(1, 3), '
                        'ORDERBY([Value])), [Value], ",", [Value], ASC)', "1,2"),
    ("b_gs_win_var", 'VAR T = GENERATESERIES(1, 3) RETURN CONCATENATEX(WINDOW(1, ABS, 2, ABS, T, '
                     'ORDERBY([Value])), [Value], ",", [Value], ASC)', "1,2"),
    ("b_cal_idx", 'CONCATENATEX(INDEX(1, CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 3)), '
                  'ORDERBY([Date], DESC)), FORMAT([Date], "yyyy-mm-dd"))', "2024-01-03"),
    ("b_ctor_distinct_idx", ix('DISTINCT({"b", "a", "c"})', "[Value]", "[Value]"), "a"),
    # group C
    ("c_cj_sets", ix("CROSSJOIN(ALL(S[Cat]), ALL(Dim[Name]))", "S[Cat], ASC, Dim[Name], ASC",
                     "S[Cat] & Dim[Name]"), "ANorth"),
    ("c_intersect_set", ix('INTERSECT(ALL(S[Cat]), {"B", "C"})', "S[Cat]", "S[Cat]"), "B"),
    ("c_selcols_keep", ix('SELECTCOLUMNS(ALL(S[Cat], S[Reg]), "r", S[Reg], "c", S[Cat])',
                          "[c]", "[c]"), "A"),
    ("c_topn_distinct", ix("TOPN(2, DISTINCT(S), S[V], ASC)", "S[V]", "S[Cat]"), "A"),
    ("c_calctable_kf", ix('CALCULATETABLE(DISTINCT(S), KEEPFILTERS(S[Reg] = "S"))', "S[V]",
                          "S[Cat]"), "C"),
    # build_b253c.py group D: DISTINCT in a CROSSJOIN, ALLNOBLANKROW of a keyed table
    ("d_cj_distinct_set", ix("CROSSJOIN(DISTINCT(S), ALL(Dim[Name]))",
                             "S[V], ASC, Dim[Name], ASC", "S[Cat] & Dim[Name]"), "ANorth"),
    ("d_anbr_dim", ix("ALLNOBLANKROW(Dim)", "Dim[Name]", "Dim[Name]"), "North"),
    # group H: the omitted relation (a visual's axis) is never refused
    ("hp_off_omit", per_s("h_off_omit"), ",,,"),
    # the baselines (build_b253.py group 7)
    ("c_all_sd", "COUNTROWS(ALL(SD))", 3),
    ("c_distinct_sd", "COUNTROWS(DISTINCT(SD))", 2),
    ("c_values_sd", "COUNTROWS(VALUES(SD))", 3),
    ("c_allcols_sd", "COUNTROWS(ALL(SD[a], SD[b]))", 2),
]


@pytest.mark.parametrize("expr,want", [c[1:] for c in ACCEPTED], ids=[c[0] for c in ACCEPTED])
def test_a_relation_desktop_proves_duplicate_free(expr, want):
    value, err = ev(expr)
    assert err is None, err
    assert value == want


# ---- the refusal fails the query; IFERROR catches it through a reference ----

@pytest.mark.parametrize("expr,want", [
    ("IFERROR([h_off_all], -1)", -1),                                   # z_iferror
    ("IF(ISERROR([h_off_all]), 1, 0)", 1),                              # z_iserror
    ("IF([h_sv] < 0, [h_off_all], 1)", 1),                              # z_if_dyn
    ("SUMX(S, IF(FALSE(), [h_off_all], 1))", 4),                        # e_if_false_iter
    ("SUMX(S, IF(S[V] < 0, [h_off_all], 1))", 4),                       # e_if_row_iter
])
def test_caught_through_a_reference_and_not_reached(expr, want):
    assert ev(expr) == (want, None)


@pytest.mark.parametrize("expr,fn", [
    # f_ife_dup_inline: IFERROR around the function itself does not catch it
    ('IFERROR(CONCATENATEX(INDEX(1, ALL(S), ORDERBY(S[V])), S[Cat]), "caught")', "INDEX"),
    # a referenced measure's refusal fails its caller, per row (o_m2m, above),
    # under a filter (z_empty_ctx) and per row of S (b247 w_partition_offset)
    ('CALCULATE([h_off_all], S[Cat] = "ZZZ")', "OFFSET"),
    ('CONCATENATEX(S, [off_p], ",", S[Cat], ASC)', "OFFSET"),
])
def test_the_refusal_fails_the_query(expr, fn):
    err = refused(expr)
    assert DUP in err and f"of {fn} " in err, err


# ---- the conformance fixture (tests/conformance/golden.json) ----

@pytest.mark.parametrize("expr,fn", [
    ("SUMX(INDEX(1, F, ORDERBY(F[v])), F[v])", "INDEX"),                # q_cf_index
    ("SUMX(F, SUMX(OFFSET(-1, F, ORDERBY(F[v])), F[v]))", "OFFSET"),    # a_cf_offset
    ("SUMX(F, SUMX(WINDOW(1, ABS, 2, ABS, F, ORDERBY(F[v])), F[v]))", "WINDOW"),  # a_cf_window
    ("SUMX(INDEX(-1, F, ORDERBY(F[v])), F[v])", "INDEX"),               # b_cf_index_neg
    ("SUMX(F, SUMX(WINDOW(-1, REL, 0, REL, F, ORDERBY(F[v])), F[v]))", "WINDOW"),  # b_cf_window_rel
])
def test_the_conformance_fixtures_window_probes_are_refused(expr, fn):
    # F is the many side of F -> K and F -> D there, as it is today: Desktop
    # refuses all five of golden.json's window probes (build_b253b.py
    # q_cf_index, a_cf_offset, a_cf_window; build_b253c.py b_cf_index_neg,
    # b_cf_window_rel). Their golden values (50, 350, 600, 300, 1000) were
    # captured on a fixture built with F as the one side of D -> F (a_cfj_*
    # answer the first three over July's FJ); 0.9.147 answered them.
    from tests.conformance.fixture_def import (
        FIXTURE_MEASURES,
        FIXTURE_RELATIONSHIPS,
        FIXTURE_TABLES,
    )
    ctx = de.DAXContext(
        {k: {"columns": v["columns"], "rows": [list(r) for r in v["rows"]]}
         for k, v in FIXTURE_TABLES.items()},
        dict({"__probe__": expr}, **{n: e for n, (_t, e) in FIXTURE_MEASURES.items()}),
        None, None, None, [dict(r) for r in FIXTURE_RELATIONSHIPS])
    eng = de.DAXEngine()
    assert eng.evaluate_measure("__probe__", ctx) is None
    err = eng.eval_errors.get("__probe__")
    assert err and DUP in err and f"of {fn} " in err, err


# ---- iterators over an empty table (build_b253b.py / build_b253c.py group E) ----

@pytest.mark.parametrize("expr,fn", [
    ("SUMX(FILTER(S, S[V] > 1000), [h_off_all])", "OFFSET"),           # z_sumx_empty
    ("SUMX(FILTER(S, S[V] > 1000), CALCULATE(SUM(S[V]), OFFSET(-1, ALL(S), "
     "ORDERBY(S[Cat]))))", "OFFSET"),                                   # e_sumx_empty_inline
    ("COUNTROWS(FILTER(FILTER(S, S[V] > 1000), [h_off_all] > 0))", "OFFSET"),  # e_filter_empty
    # build_b253c.py: the other iterators Desktop answered, which 0.9.147
    # answered BLANK
    ("AVERAGEX(FILTER(S, S[V] > 1000), [h_off_all])", "OFFSET"),       # e_averagex_empty
    ("MINX(FILTER(S, S[V] > 1000), [h_off_all])", "OFFSET"),           # e_minx_empty
    ("MAXX(FILTER(S, S[V] > 1000), [h_off_all])", "OFFSET"),           # e_maxx_empty
    ("COUNTX(FILTER(S, S[V] > 1000), [h_off_all])", "OFFSET"),         # e_countx_empty
    ("SUMX(FILTER(S, S[V] > 1000), [h_off_all] + 1)", "OFFSET"),       # e_sumx_empty_plus
    # the relation in the referenced measure's own VAR r = ALL(S)
    ("SUMX(FILTER(S, S[V] > 1000), [h_var_idx])", "INDEX"),            # e_sumx_empty_var_ref
])
def test_an_empty_iteration_still_refuses(expr, fn):
    err = refused(expr)
    assert DUP in err and f"of {fn} " in err, err


@pytest.mark.parametrize("expr", [
    "SUMX(FILTER(S, S[V] > 1000), IFERROR([h_off_all], 0))",           # e_sumx_empty_iferror
    "CONCATENATEX(FILTER(S, S[V] > 1000), [h_off_all])",                # e_concatx_empty
    'COUNTROWS(ADDCOLUMNS(FILTER(S, S[V] > 1000), "x", [h_off_all]))',  # e_addcols_empty
    # build_b253c.py: under IF; the measure's own t = ALL(S[Cat]), a set,
    # whatever the caller's t
    "SUMX(FILTER(S, S[V] > 1000), IF(S[V] > 0, [h_off_all], 1))",       # e_sumx_empty_if
    "VAR t = ALL(S) RETURN SUMX(FILTER(t, S[V] > 1000), [h_var_set])",  # e_sumx_empty_var_scope
])
def test_an_empty_iteration_desktop_answers_blank(expr):
    assert ev(expr) == (None, None)


# ---- variables: each call is checked in its own scope, whatever ran before ----
#
# The relation's shape is decided where the call is written, and a variable is
# its expression's shape (build_b253.py o_ctor, o_genseries; build_b253b.py
# b_gs_idx_var). The scan of an empty SUMX / FILTER once checked a referenced
# measure's calls with the CALLER's variables: a measure's own VAR r was then
# unknown, that None was memoized for the text "r" in the model's analysis
# memo, which outlives a query, and every later evaluation of the measure
# skipped its refusal and its appended key; a caller's VAR t = ALL(S) made a
# measure's own VAR t = ALL(S[Cat]) a bag and refused it.

_SCOPED = {
    # h_rn_dim2 (build_b253.py rn_dim2_cat '1,2,3') with its relation in a VAR
    "v_rn_dim2": "VAR r = ALL(Dim2) RETURN ROWNUMBER(r, ORDERBY(Dim2[Grp]))",
    # i_all_s (refused) with its relation in a VAR
    "v_idx_all_s": "VAR r = ALL(S) RETURN CONCATENATEX(INDEX(1, r, ORDERBY(S[V])), S[Cat])",
    # i_values_col ('A') with its relation in a VAR of the same name
    "v_idx_values_col": "VAR r = VALUES(S[Cat]) RETURN CONCATENATEX(INDEX(1, r, "
                        "ORDERBY(S[Cat])), S[Cat])",
    # o_allcols' column-list relation, a set, in a VAR named t
    "v_off_cat": "VAR t = ALL(S[Cat]) RETURN CALCULATE(SUM(S[V]), OFFSET(-1, t, "
                 "ORDERBY(S[Cat])))",
    "q_dim2": 'CONCATENATEX(Dim2, [v_rn_dim2], ",", Dim2[K], ASC)',
    "q_empty_dim2": "SUMX(FILTER(Dim2, Dim2[K] > 100), [v_rn_dim2])",
    "q_empty_all_s": "SUMX(FILTER(S, S[V] > 1000), [v_idx_all_s])",
    "q_empty_off_cat": "SUMX(FILTER(S, S[V] > 1000), [v_off_cat])",
    "q_caller_t": "VAR t = ALL(S) RETURN SUMX(FILTER(t, S[V] > 1000), [v_off_cat])",
}


def _model_ev(names: list) -> list:
    """(value, error) of each measure in ``names``, one call each, in order,
    over ONE model (the same measures dict): the calls share the model's
    analysis memo, as a server's calls on an open model do."""
    measures = dict(HELPERS, **_SCOPED)
    homes = {k: "P" for k in measures}
    out = []
    for n in names:
        de._engine.eval_errors.clear()
        value = de.evaluate_measures_batch([n], TABLES, measures, relationships=RELS,
                                           measure_tables=homes)[n]
        out.append((value, de._engine.eval_errors.get(n)))
    return out


def test_a_measures_variable_keeps_its_key_after_an_empty_scan():
    # rn_dim2_cat '1,2,3' (Dim2's key K breaks the g1 tie) before and after an
    # empty SUMX scanned the measure; the empty SUMX itself sums no row
    before, empty, after = _model_ev(["q_dim2", "q_empty_dim2", "q_dim2"])
    assert before == ("1,2,3", None)
    assert empty == (None, None)
    assert after == ("1,2,3", None)


def test_a_measures_variable_keeps_its_refusal_after_an_empty_scan():
    # i_all_s through a VAR: refused, alone and after an empty SUMX of it
    first, _empty, again = _model_ev(["v_idx_all_s", "q_empty_all_s", "v_idx_all_s"])
    for value, err in (first, again):
        assert value is None and err and DUP in err and "of INDEX " in err, (value, err)


def test_one_variable_name_in_two_measures():
    # "r" is VALUES(S[Cat]) in one measure (i_values_col 'A') and ALL(S) in the
    # other (i_all_s refused), in either order
    a1, r1, a2, r2 = _model_ev(["v_idx_values_col", "v_idx_all_s",
                                "v_idx_values_col", "v_idx_all_s"])
    assert a1 == a2 == ("A", None)
    for value, err in (r1, r2):
        assert value is None and err and DUP in err, (value, err)


def test_a_callers_variable_does_not_reach_a_measures_own():
    # v_off_cat's own t is a column list, a set (i_allcols_s, o_allcols): the
    # empty SUMX sums no row whatever the caller calls t
    assert _model_ev(["q_caller_t", "q_empty_off_cat"]) == [(None, None), (None, None)]


# ---- build_b253d.py (Desktop 2.152 answers desk_b253d.json) ----

EMPTY_D = "FILTER(S, S[V] > 1000)"


@pytest.mark.parametrize("expr", [
    f"COUNTAX({EMPTY_D}, [h_off_all])",                                  # b_countax_empty
    f"PRODUCTX({EMPTY_D}, [h_off_all])",                                 # b_productx_empty
    f"STDEVX.P({EMPTY_D}, [h_off_all])",                                 # b_stdevxp_empty
    f"GEOMEANX({EMPTY_D}, [h_off_all])",                                 # b_geomeanx_empty
], ids=["b_countax_empty", "b_productx_empty", "b_stdevxp_empty", "b_geomeanx_empty"])
def test_the_other_iterators_over_an_empty_table_refuse(expr):
    # as SUMX does (z_sumx_empty); 0.9.147 and the round-3 engine: BLANK
    err = refused(expr)
    assert DUP in err and "of OFFSET " in err, err


@pytest.mark.parametrize("expr,want", [
    (f"MEDIANX({EMPTY_D}, [h_off_all])", None),                          # b_medianx_empty
    (f"PERCENTILEX.INC({EMPTY_D}, [h_off_all], 0.5)", None),             # b_percentilex_empty
    (f"RANKX({EMPTY_D}, [h_off_all], 1)", 1),                            # b_rankx_empty
    (f"MINX({EMPTY_D}, IF(S[V] > 0, [h_off_all], 1))", None),            # b_minx_empty_if
], ids=["b_medianx_empty", "b_percentilex_empty", "b_rankx_empty", "b_minx_empty_if"])
def test_iterators_desktop_answers_over_an_empty_table(expr, want):
    assert ev(expr) == (want, None)


REFUSED_D = [
    # group D: WINDOW with one REL bound over GENERATESERIES
    ("d_gs_win_absrel", 'CONCATENATEX(GENERATESERIES(1, 3), CONCATENATEX(WINDOW(1, ABS, 0, REL, '
                        'GENERATESERIES(1, 3), ORDERBY([Value])), [Value], ""), ",", [Value], ASC)',
     "WINDOW"),
    # group E: TREATAS of a bag onto a column that is no key, of a keyed table
    # onto columns that are none (0.9.147: BLANK; the round-3 engine 'N', 'North')
    ("e_treatas_bag_nokey", ix('TREATAS(SELECTCOLUMNS(ALL(S), "c", S[Reg]), Fact[Region])',
                               "Fact[Region]", "Fact[Region]"), "INDEX"),
    ("e_treatas_keyed", ix("TREATAS(ALL(Dim), S[Reg], S[Cat])", "S[Cat]", "S[Cat]"), "INDEX"),
    # group F: CALENDAR under WINDOW with REL bounds, a keyed table beside
    # GENERATESERIES in a CROSSJOIN
    ("f_cal_win_rel", 'VAR T = CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 3)) RETURN CONCATENATEX(T, '
                      'CONCATENATEX(WINDOW(-1, REL, 0, REL, T, ORDERBY([Date])), DAY([Date]), ""), '
                      '",", [Date], ASC)', "WINDOW"),
    ("f_cj_keyed_series", ix("CROSSJOIN(ALL(Dim), GENERATESERIES(1, 2))",
                             "Dim[Name], ASC, [Value], ASC", "Dim[Name] & [Value]"), "INDEX"),
]


@pytest.mark.parametrize("expr,fn", [c[1:] for c in REFUSED_D], ids=[c[0] for c in REFUSED_D])
def test_b253d_relations_that_may_have_duplicate_rows(expr, fn):
    err = refused(expr)
    assert DUP in err and f"of {fn} " in err, err


ACCEPTED_D = [
    # DISTINCT of a keyed table is a set in a CROSSJOIN
    ("e_cj_distinct_keyed", ix("CROSSJOIN(DISTINCT(ALL(Dim)), ALL(S[Cat]))",
                               "Dim[Name], ASC, S[Cat], ASC", "Dim[Name] & S[Cat]"), "NorthA"),
    ("f_cj_distinct_keyed2", ix("CROSSJOIN(DISTINCT(ALL(Dim)), DISTINCT(ALL(Dim2)))",
                                "Dim[Name], ASC, Dim2[K], ASC", "Dim[Name] & Dim2[K]"), "North1"),
]


@pytest.mark.parametrize("expr,want", [c[1:] for c in ACCEPTED_D], ids=[c[0] for c in ACCEPTED_D])
def test_b253d_relations_desktop_accepts(expr, want):
    assert ev(expr) == (want, None)
