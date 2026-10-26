"""#260: ALLEXCEPT used as a table.

As a table, ALLEXCEPT(T, T[c]) is the distinct rows of T's OTHER columns
(Power BI Desktop 2.152): COUNTROWS(ALLEXCEPT(T3, T3[Cat])) is 3, T3's
distinct (Reg, V), and INDEX over ALLEXCEPT(S, S[Cat]) ordered by S[V]
answers its S[Reg]. 0.9.147 returned, per distinct full row, a row of the
table's first column alone: 4, the column markers as text
("('T3', 'Reg')('T3', 'V')"), and ''.

Expected values are Desktop's answers by probe id: build_b253b.py (group J)
and build_b253.py (i_allexcept_s).
"""
from __future__ import annotations

import pytest

from tests.test_issue247_window_duplicates import ev, ix

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("expr,want", [
    ("COUNTROWS(ALLEXCEPT(T3, T3[Cat]))", 3),                                   # j_allexcept_count
    ('CONCATENATEX(ALLEXCEPT(T3, T3[Cat]), T3[Reg] & T3[V], ",", T3[V], ASC)',
     "N1,S2,S3"),                                                               # j_allexcept_cols
    (ix("ALLEXCEPT(S, S[Cat])", "S[V]", "S[Reg]"), "N"),                         # i_allexcept_s
])
def test_allexcept_is_the_other_columns(expr, want):
    assert ev(expr) == (want, None)


@pytest.mark.xfail(strict=False, reason=(
    "Desktop refuses S[Cat] where no row context holds it (j_allexcept_dropped: "
    "'A single value for column Cat in table S cannot be determined'); the engine "
    "reads such a column as BLANK wherever it meets one, not only here"))
def test_the_excepted_column_is_no_column_of_the_table():
    _value, err = ev('CONCATENATEX(ALLEXCEPT(S, S[Cat]), S[Cat], ",")')
    assert err and "single value" in err
