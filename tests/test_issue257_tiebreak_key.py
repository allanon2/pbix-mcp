"""#257: the columns that break ORDERBY's ties.

Power BI Desktop 2.152 breaks the ties of ORDERBY in a relation it proves
duplicate-free by appending the relation's key, ascending: a keyed table's
key -- not its other columns, and ascending under ORDERBY DESC too -- a
column list's columns in the list's order, MATCHBY's columns. 0.9.147 kept
tied rows in storage order (Dim2 stores K = 2 before K = 1), and gave a
column list's rows no current row at all.

Expected values are Desktop's answers by probe id: build_b253.py (o_dim2_*,
rn_dim2_*, i_dim2_last, rn_regcat) and build_b253b.py (group D,
i_mb_rn_tie).
"""
from __future__ import annotations

import pytest

from tests.test_issue247_window_duplicates import ev, ix, per, per_s

pytestmark = pytest.mark.unit

CASES = [
    # the key K, ascending (storage order: K = 2 first)
    ("o_dim2_cat", per("Dim2", "K", "h_o_dim2"), ",1,2"),
    ("o_dim2_sum", "SUMX(Dim2, [h_o_dim2])", 3),
    ("rn_dim2_cat", per("Dim2", "K", "h_rn_dim2"), "1,2,3"),
    ("rn_dim2_sum", "SUMX(Dim2, [h_rn_dim2])", 6),
    ("i_dim2_last", ix("ALL(Dim2)", "Dim2[Grp]", "Dim2[K]", pos=-1), "3"),
    # the key only: Dim3's first column Z would order the tie the other way
    ("d_rn_dim3", per("Dim3", "K", "h_rn_dim3"), "1,2,3"),
    # ascending under ORDERBY DESC
    ("d_rn_dim2_desc", per("Dim2", "K", "h_rn_dim2_desc"), "2,3,1"),
    # a column list: its columns in the list's order
    ("rn_regcat", per_s("h_rn_regcat"), "1,2,3,4"),
    ("d_rn_q_yx", per("Q", "X", "h_rn_q_yx"), "2,1,3"),
    ("d_rn_q_xy", per("Q", "X", "h_rn_q_xy"), "1,2,3"),
    ("d_rn_q2_xy", per("Q2", "X", "h_rn_q2_xy"), "1,2,3"),
    # SUMMARIZE(Q, ...) in each row's context holds that one row
    ("d_rn_q_sum", per("Q", "X", "h_rn_q_sum"), "1,1,1"),
    # MATCHBY breaks the tie
    ("i_mb_rn_tie", per_s("h_mb_rn_tie"), "1,2,3,4"),
]


@pytest.mark.parametrize("expr,want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_ties_are_broken_by_the_key_ascending(expr, want):
    assert ev(expr) == (want, None)
