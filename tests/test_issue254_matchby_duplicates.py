"""#254: MATCHBY values that repeat in the relation.

MATCHBY lifts the duplicate-rows refusal of OFFSET / INDEX / WINDOW (#247),
but Power BI Desktop 2.152 then requires its values -- with the PARTITIONBY
values -- to name each row of the relation once, and fails INDEX, OFFSET,
ROWNUMBER and RANK with "Despite MatchBy columns being specified, duplicate
rows occur in the relation parameter of INDEX. This is not allowed." when
they repeat (its Norwegian message, translated): a check of the DATA --
S[Cat], unique in it though no relationship proves it, passes. IFERROR /
ISERROR catch it neither around the function nor around a reference to its
measure. 0.9.147 took the first row with the current row's MATCHBY values
and answered.

Expected values are Desktop's answers by probe id: build_b253.py (i_mb_*,
o_mb_*, rn_mb_reg, rk_mb_reg_rows, w_mb_cat), build_b253b.py (group I,
f_ife_mb) and build_b253c.py (a_ife_mb_ref, a_iserr_mb_ref).
"""
from __future__ import annotations

import pytest

from tests.test_issue247_window_duplicates import ev, ix, per, per_s, refused

pytestmark = pytest.mark.unit

MB_DUP = "Despite MatchBy columns being specified, duplicate rows occur"

REFUSED = [
    ("i_mb_reg", ix("ALL(S)", "S[V]", "S[Cat]", extra=", MATCHBY(S[Reg])"), "INDEX"),
    ("o_mb_reg", per_s("h_o_mb_reg"), "OFFSET"),
    ("rn_mb_reg", per_s("h_rn_mb_reg"), "ROWNUMBER"),
    ("rk_mb_reg_rows", per_s("h_rk_mb_reg"), "RANK"),
    ("f_ife_mb", 'IFERROR(CONCATENATEX(INDEX(1, ALL(S), ORDERBY(S[V]), MATCHBY(S[Reg])), '
                 'S[Cat]), "caught")', "INDEX"),
    # around a reference to its measure h_mb_dup (i_mb_reg's INDEX), which
    # Desktop's error names (0.9.147: 'A', 0)
    ("a_ife_mb_ref", 'IFERROR([h_mb_dup], "caught")', "INDEX"),
    ("a_iserr_mb_ref", "IF(ISERROR([h_mb_dup]), 1, 0)", "INDEX"),
]


@pytest.mark.parametrize("expr,fn", [c[1:] for c in REFUSED], ids=[c[0] for c in REFUSED])
def test_matchby_values_that_repeat_are_refused(expr, fn):
    err = refused(expr)
    assert MB_DUP in err and f"of {fn}." in err, err


ACCEPTED = [
    ("i_mb_cat", ix("ALL(S)", "S[V]", "S[Cat]", extra=", MATCHBY(S[Cat])"), "A"),
    ("o_mb_cat", per_s("h_o_mb_cat"), ",A,B,C"),
    ("o_mb_part", per_s("h_o_mb_part"), ",A,,C"),
    ("w_mb_cat", per_s("h_w_mb_cat"), "A,AB,ABC,ABCD"),
    # repeats across partitions only, and two columns unique together
    ("i_mb_part", per("QM", "V", "h_mb_part"), ",A,,A"),
    ("i_mb_rn_part", per("QM", "V", "h_mb_rn_part"), "1,2,1,2"),
    ("i_mb_two", per("QM", "V", "h_mb_two"), ",A,B,A"),
]


@pytest.mark.parametrize("expr,want", [c[1:] for c in ACCEPTED], ids=[c[0] for c in ACCEPTED])
def test_matchby_values_that_name_each_row_once(expr, want):
    assert ev(expr) == (want, None)
