"""#259: ORDERBY's order argument.

In ORDERBY(<expression>, <order>, <expression>, <order>, ...) the argument
after an expression is always its order. Power BI Desktop 2.152 takes ASC /
1 / TRUE / TRUE() ascending and DESC / 0 / FALSE / FALSE() descending, and
refuses anything else -- "DESC" as text, 2, a column -- with "Unexpected value
for ORDER argument in ORDERBY function. Use 0/FALSE/DESC for descending
order or 1/TRUE/ASC for ascending order." (Desktop's own English text,
build_b257d.py e_orderby_text). That is an error of the measure's definition: it fails the
measure wherever the call is, in an IF branch never taken too; IFERROR
around the call does not catch it, IFERROR around a reference to the measure
does. The space form ORDERBY(S[V] DESC) is a syntax error, which IFERROR
around a reference catches as well. 0.9.147 read a second expression as the
next sort key, 0 / FALSE / FALSE() as ascending, and the space form as
descending.

A comment is no part of an argument: the measure is checked with its
comments stripped, as it is evaluated. Checked with them, the order after
ORDERBY(S[V], DESC -- latest first) was "DESC -- latest first" and
ORDERBY(S[V] -- asc) the space form, and either measure failed, in a branch
never taken too; so did one naming a column [Sales, Net] in a branch never
taken, its comma read as the end of ORDERBY's first argument.

Expected values are Desktop's answers by probe id: build_b253b.py (group G,
f_ife_order_inline, f_ife_order_ref), build_b253.py (i_all_s_fullorder,
i_crossjoin, rn_sd) and build_b253c.py (a_order_untaken, a_ife_space_ref).
Each group G probe is INDEX(1, ALL(S[Cat], S[V]), ORDERBY(S[V], <order>)):
ascending answers A, descending D. With a comment the expected value is the
answer of the same probe without it (g_desc D; an omitted order ascending,
g_true's A).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue247_window_duplicates import ev, ix, per, refused

pytestmark = pytest.mark.unit

ORDER_ARG = ("Unexpected value for ORDER argument in ORDERBY function. Use "
             "0/FALSE/DESC for descending order or 1/TRUE/ASC for ascending order.")


def ordarg(order):
    return f"CONCATENATEX(INDEX(1, ALL(S[Cat], S[V]), ORDERBY({order})), S[Cat])"


@pytest.mark.parametrize("order,want", [
    ("S[V], DESC", "D"),            # g_desc
    ("S[V], TRUE", "A"),            # g_true
    ("S[V], FALSE", "D"),           # g_false
    ("S[V], TRUE()", "A"),          # g_true_fn
    ("S[V], FALSE()", "D"),         # g_false_fn
    ("S[V], 1", "A"),               # g_one
    ("S[V], 0", "D"),               # g_zero
    ("S[V], DESC, S[Cat]", "D"),    # g_three
])
def test_the_order_words_desktop_accepts(order, want):
    assert ev(ordarg(order)) == (want, None)


REFUSED = [
    ("g_text", ordarg('S[V], "DESC"')),
    ("g_two", ordarg("S[V], 2")),
    ("i_all_s_fullorder", ix("ALL(S)", "S[V], S[Cat], S[Reg]", "S[Cat]")),
    ("i_crossjoin", ix("CROSSJOIN(ALL(S[Cat]), ALL(Dim[Name]))", "S[Cat], Dim[Name]",
                       "S[Cat] & Dim[Name]")),
    # through the measure h_rn_sd = ROWNUMBER(ALL(SD), ORDERBY(SD[a], SD[b]))
    ("rn_sd", per("SD", "a", "h_rn_sd")),
    ("f_ife_order_inline", 'IFERROR(CONCATENATEX(INDEX(1, ALL(S[Cat], S[V]), ORDERBY(S[V], '
                           'S[Cat])), S[Cat]), "caught")'),
    # in a branch never taken (0.9.147: "ok")
    ("a_order_untaken", 'IF(FALSE(), CONCATENATEX(INDEX(1, ALL(S[Cat], S[V]), ORDERBY(S[V], '
                        'S[Cat])), S[Cat]), "ok")'),
]


@pytest.mark.parametrize("expr", [c[1] for c in REFUSED], ids=[c[0] for c in REFUSED])
def test_any_other_order_is_refused(expr):
    assert ORDER_ARG in refused(expr)


def test_iferror_around_a_reference_to_the_measure_catches_it():
    # f_ife_order_ref; h_bad_order = INDEX(..., ORDERBY(S[V], S[Cat]))
    assert ev('IFERROR([h_bad_order], "caught")') == ("caught", None)


def test_the_space_form_is_a_syntax_error():
    # g_space: Desktop cannot parse ORDERBY(S[V] DESC)
    err = refused(ordarg("S[V] DESC"))
    assert "DESC" in err and "syntax" in err


def test_iferror_around_a_reference_catches_the_space_form():
    # build_b253c.py a_ife_space_ref; h_space = INDEX(..., ORDERBY(S[V] DESC))
    # (0.9.147: 'D')
    assert ev('IFERROR([h_space], "caught")') == ("caught", None)


@pytest.mark.parametrize("order,want", [
    ("S[V], DESC -- latest first\n", "D"),
    ("S[V], DESC // latest first\n", "D"),
    ("S[V], /* newest */ DESC", "D"),
    ("S[V], -- by value\n DESC", "D"),
    ("\n        S[V],\n        DESC  -- latest first\n    ", "D"),
    ("S[V] /* sort key */, DESC", "D"),
    ("S[V] -- asc\n", "A"),
], ids=["line_after", "slash_after", "block_before", "line_before", "multiline",
        "block_after_expr", "line_after_expr"])
def test_a_comment_is_no_part_of_an_order(order, want):
    assert ev(ordarg(order)) == (want, None)


def test_a_comment_fails_no_branch_never_taken():
    call = ordarg("S[V], DESC -- latest first\n")
    assert ev(f'IF(FALSE(), {call}, "ok")') == ("ok", None)


def test_a_comma_in_a_column_name_fails_no_branch_never_taken():
    tables = {"S": {"columns": ["Cat", "Sales, Net"], "rows": [["A", 1], ["B", 2]]}}
    expr = ('IF(FALSE(), CONCATENATEX(INDEX(1, ALL(S[Cat], S[Sales, Net]), '
            'ORDERBY(S[Sales, Net], DESC)), S[Cat]), "ok")')
    de._engine.eval_errors.clear()
    value = de.evaluate_measures_batch(["m"], tables, {"m": expr}, relationships=[],
                                       measure_tables={"m": "S"})["m"]
    assert (value, de._engine.eval_errors.get("m")) == ("ok", None)


def test_a_reference_never_taken_is_no_part_of_the_definition():
    # build_b253d.py c_order_ref_untaken: IF(FALSE(), [h_bad_order], "ok") is
    # "ok" (Desktop 2.152); only the measure's own expression is checked
    assert ev('IF(FALSE(), [h_bad_order], "ok")') == ("ok", None)
