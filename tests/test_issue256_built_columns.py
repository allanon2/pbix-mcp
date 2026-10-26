"""#256: ROWNUMBER / RANK over a relation of table-function
columns only.

Power BI Desktop 2.152 refuses ROWNUMBER and RANK over a relation whose
every column was added by a DAX table function -- a table constructor,
GENERATESERIES, inline or through a variable -- with "The relation
parameter of ROWNUMBER contains only columns added by DAX table functions.
This is not supported." (its Norwegian message, translated), which IFERROR
catches neither around the function nor around a reference to its measure;
a SELECTCOLUMNS of a model column keeps that column's lineage and is
accepted. (Under INDEX / OFFSET the same constructors are refused as
duplicate rows instead: #247.) 0.9.147 numbered and ranked them.

Expected values are Desktop's answers by probe id: build_b253.py (rn_ctor,
rn_ctor_dup, rk_ctor), build_b253b.py (b_gs_rn, b_ctor_rn_inline,
f_ife_built, b_selcols_rn) and build_b253c.py (a_ife_built_ref).
"""
from __future__ import annotations

import pytest

from tests.test_issue247_window_duplicates import ev, refused

pytestmark = pytest.mark.unit

BUILT = "contains only columns added by DAX table functions. This is not supported."

REFUSED = [
    ("rn_ctor", 'VAR T = {"b", "a", "c"} RETURN CONCATENATEX(T, ROWNUMBER(T, ORDERBY([Value])), '
                '",", [Value])', "ROWNUMBER"),
    ("rn_ctor_dup", 'VAR T = {"b", "a", "a"} RETURN CONCATENATEX(T, ROWNUMBER(T, '
                    'ORDERBY([Value])), ",", [Value])', "ROWNUMBER"),
    ("rk_ctor", 'VAR T = {"b", "a", "a"} RETURN CONCATENATEX(T, RANK(DENSE, T, '
                'ORDERBY([Value])), ",", [Value])', "RANK"),
    ("b_gs_rn", 'CONCATENATEX(GENERATESERIES(1, 3), ROWNUMBER(GENERATESERIES(1, 3), '
                'ORDERBY([Value])), ",", [Value], ASC)', "ROWNUMBER"),
    ("b_ctor_rn_inline", 'CONCATENATEX({"b", "a", "c"}, ROWNUMBER({"b", "a", "c"}, '
                         'ORDERBY([Value])), ",", [Value], ASC)', "ROWNUMBER"),
    ("f_ife_built", 'VAR T = {"b", "a"} RETURN IFERROR(CONCATENATEX(T, ROWNUMBER(T, '
                    'ORDERBY([Value]))), "caught")', "ROWNUMBER"),
]


@pytest.mark.parametrize("expr,fn", [c[1:] for c in REFUSED], ids=[c[0] for c in REFUSED])
def test_a_relation_of_built_columns_only_is_refused(expr, fn):
    err = refused(expr)
    assert BUILT in err and f"of {fn} " in err, err


def test_iferror_around_a_reference_does_not_catch_it():
    # build_b253c.py a_ife_built_ref: Desktop fails the query naming h_built
    # (0.9.147: '21'); unlike the duplicate-rows refusal, which a reference
    # lets IFERROR catch (z_iferror -1)
    err = refused('IFERROR([h_built], "caught")')
    assert BUILT in err and "of ROWNUMBER " in err, err


def test_a_selected_model_column_keeps_its_lineage():
    # b_selcols_rn
    assert ev('VAR T = SELECTCOLUMNS(ALL(S[Cat]), "c", S[Cat]) RETURN CONCATENATEX(T, '
              'ROWNUMBER(T, ORDERBY([c])), ",", [c], ASC)') == ("1,2,3,4", None)
