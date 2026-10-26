"""#255: ROWNUMBER over a relation with tied rows.

ROWNUMBER has no static check (it answers over the keyless ALL(S) where
nothing ties), but Power BI Desktop 2.152 fails it with "Duplicate rows
detected in the relation parameter of the ROWNUMBER function." (its
Norwegian message, translated) when two rows of a relation that may have
duplicate rows tie on PARTITIONBY and ORDERBY -- ANYWHERE in the relation,
whatever the current row: rn_t3_c fails though its tie is in the other
partition, rn_t3_d though its own value is unique, z_rn_iferror at a total.
Neither IFERROR / ISERROR around ROWNUMBER nor around a reference to its
measure catch it. 0.9.147 numbered tied rows in storage order, or told them
apart by the caller's filters on other columns, and answered BLANK where
Desktop fails.

Expected values are Desktop's answers by probe id: build_b253.py (rn_*,
z_rn_iferror), build_b253b.py (hp_rn_sd2, group F, e_sumx_empty_rn,
e_if_row_rn) and build_b247.py (w_rownumber_ties, f_rn_reg_n35, u_rn_reg_s;
also in tests/test_issue236_window_current_row.py).
"""
from __future__ import annotations

import pytest

from tests.test_issue247_window_duplicates import ev, per, per_s, refused

pytestmark = pytest.mark.unit

TIE = "Duplicate rows detected in the relation parameter of the ROWNUMBER function."

REFUSED = [
    ("rn_t3_c", 'CALCULATE([h_rn_t3_p], T3[Cat] = "C")'),
    ("rn_t3_d", 'CALCULATE([h_rn_t3], T3[Cat] = "D")'),
    ("rn_t3_a", 'CALCULATE([h_rn_t3], T3[Cat] = "A")'),
    ("z_rn_iferror", "IFERROR([h_rn_reg], -1)"),
    ("hp_rn_sd2", per("SD", "a", "h_rn_sd2")),
    ("f_ife_rn_inline", "IFERROR(ROWNUMBER(ALL(S), ORDERBY(S[Reg])), -1)"),
    ("f_iserr_rn_inline", "IF(ISERROR(ROWNUMBER(ALL(S), ORDERBY(S[Reg]))), 1, 0)"),
    ("f_ife_rn_perrow", 'CONCATENATEX(S, IFERROR([h_rn_reg], -1), ",", S[Cat], ASC)'),
    ("e_sumx_empty_rn", "SUMX(FILTER(S, S[V] > 1000), [h_rn_reg])"),
]


@pytest.mark.parametrize("expr", [c[1] for c in REFUSED], ids=[c[0] for c in REFUSED])
def test_a_tie_anywhere_in_the_relation_is_refused(expr):
    assert TIE in refused(expr)


ACCEPTED = [
    ("rn_v", per_s("h_rn_v"), "1,2,3,4"),
    # PARTITIONBY tells the ORDERBY ties apart
    ("rn_reg_pv", per_s("h_rn_reg_pv"), "1,1,1,1"),
    # an untaken branch of a non-empty iteration does not fail
    ("e_if_row_rn", "SUMX(S, IF(S[V] < 0, [h_rn_reg], 1))", 4),
]


@pytest.mark.parametrize("expr,want", [c[1:] for c in ACCEPTED], ids=[c[0] for c in ACCEPTED])
def test_no_tie(expr, want):
    assert ev(expr) == (want, None)
