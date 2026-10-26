"""#258: which row is current for RANK / ROWNUMBER where no
row context names one.

Power BI Desktop 2.152 names the current row of an explicit relation by the
relation's own columns -- a keyed table's key, a keyless table's columns, a
column list's columns, MATCHBY's columns when given, never a column a table
function added -- with the values they have in the filter context,
cross-filtered values included. Where the context leaves several distinct
rows, RANK and ROWNUMBER are BLANK, even when those rows share a rank;
identical rows count once; a row the relation lacks is no current row.
0.9.147 read only direct single-valued filters on the ORDERBY / PARTITIONBY
columns: CALCULATE([rn], S[Cat] = "C") was BLANK where Cat = C names V = 40,
a column list's rows were never told apart, ORDERBY over an added column
named nothing, and RANK returned the shared rank of several current rows.

Expected values are Desktop's answers by probe id: build_b253.py (rn_*,
rk_*), build_b253b.py (group H) and build_b247.py (u_rk_reg_s).
"""
from __future__ import annotations

import pytest

from tests.test_issue247_window_duplicates import ev, per, per_s

pytestmark = pytest.mark.unit

CASES = [
    # cross-filtered values name the row
    ("rn_s_cat_c", 'CALCULATE([h_rn_v], S[Cat] = "C")', 3),
    ("rn_regcol_cat_c", 'CALCULATE([h_rn_regcol], S[Cat] = "C")', 2),
    ("rk_reg_cat_c", 'CALCULATE([h_rk_reg], S[Cat] = "C")', 2),
    ("rk_regcol_cat_c", 'CALCULATE([h_rk_regcol], S[Cat] = "C")', 2),
    ("rk_dim_name_s", 'CALCULATE([h_rk_dim_name], Dim[Region] = "S")', 2),
    ("rk_dim2_g2", 'CALCULATE([h_rk_dim2], Dim2[Grp] = "g2")', 2),
    ("rk_reg_full", 'CALCULATE([h_rk_reg], S[Reg] = "S", S[Cat] = "C", S[V] = 40)', 2),
    ("rk_regcat_rs_cc", 'CALCULATE([h_rk_regcat], S[Reg] = "S", S[Cat] = "C")', 2),
    # several current rows: BLANK, even with equal ranks
    ("rk_dim2_g1", 'CALCULATE([h_rk_dim2], Dim2[Grp] = "g1")', None),
    ("rk_mb_cat_reg_s", 'CALCULATE([h_rk_mb_cat], S[Reg] = "S")', None),
    ("rn_s_reg_s", 'CALCULATE([h_rn_v], S[Reg] = "S")', None),
    ("u_rk_reg_s", 'CALCULATE([h_rk_reg], S[Reg] = "S")', None),
    ("rn_total", "ROWNUMBER(ALL(S[Cat]), ORDERBY(S[Cat]))", None),
    ("rk_col_total", "[h_rk_col]", None),
    ("rk_regcol_total", "[h_rk_regcol]", None),
    ("rk_col_reg_s", 'CALCULATE([h_rk_col], S[Reg] = "S")', None),
    # a measure called per row: the caller's row
    ("rk_reg_rows", per_s("h_rk_reg"), "1,1,2,2"),
    ("rk_reg_skip_rows", per_s("h_rk_reg_skip"), "1,1,3,3"),
    ("rk_v_rows", per_s("h_rk_v"), "1,2,3,4"),
    ("rk_dim2_rows", per("Dim2", "K", "h_rk_dim2"), "1,1,2"),
    ("rk_dim2_skip_rows", per("Dim2", "K", "h_rk_dim2_skip"), "1,1,3"),
    ("rn_sd_distinct", per("SD", "a", "h_rn_sd_distinct"), "1,1,2"),
    # identical rows count once
    ("rk_sd_rows", per("SD", "a", "h_rk_sd"), "1,1,2"),
    # ORDERBY over an added column; the relation's Reg names the row
    ("hp_rk_added", per_s("h_rk_added"), "1,1,1,1"),
    # a combination the relation lacks
    ("hp_rk_cur_missing", 'CALCULATE(RANK(DENSE, FILTER(ALL(S), S[V] > 35), ORDERBY(S[V])), '
                          'S[Cat] = "A")', None),
    # the omitted relation (a visual's axis) as before
    ("hp_rn_omit_reg", per_s("h_rn_omit_reg"), "1,1,2,2"),
]


@pytest.mark.parametrize("expr,want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_the_relation_names_the_current_row(expr, want):
    assert ev(expr) == (want, None)
