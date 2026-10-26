"""#330: an ORDER argument that is no order is refused with
Desktop's English text, and IFERROR / ISERROR cannot catch the refusal.

TOPN, TOPNSKIP, SAMPLE and CONCATENATEX take ASC / DESC / 1 / 0 / TRUE /
FALSE after an orderBy and refuse anything else. Power BI Desktop 2.152
answers IFERROR(CONCATENATEX(TS, TS[L], "", TS[L], 2), "caught") and
IFERROR(COUNTROWS(TOPN(1, TS, TS[L], 2)), -1) with an error, not with the
IFERROR's value, and there its text is English, though the Desktop is
Norwegian (without the IFERROR, build_b257b.py cx_bad printed the Norwegian
"Uventet verdi for ORDER-argumentet i funksjonen CONCATENATEX ..."):
"Unexpected value for ORDER argument in CONCATENATEX function. Use
0/FALSE/DESC for descending order or 1/TRUE/ASC for ascending order." (and
"... in TOPN function. ..."). The engine's text, "Unexpected value for the
ORDER argument in the function CONCATENATEX. ...", was a word-for-word
reading of the Norwegian message ("Uventet verdi for ORDER-argumentet i
funksjonen CONCATENATEX."). IFERROR did not catch the refusal only because
CONCATENATEX and TOPN mark every error that leaves them; around TOPNSKIP,
which they do not, it caught it. SAMPLE's refusal comes before the query
runs (docs/reverse-engineering/experiments/2026-10-24-sample.md), so no
IFERROR around it can catch it either.

Desktop raises these refusals when it reads the measure (the errors come
from ADOMD's Read, not ExecuteReader), as it raises ORDERBY's: IF(FALSE(),
CONCATENATEX(TS, TS[L], "", TS[L], 2), "ok") fails although the branch is
never taken, IFERROR around TOPNSKIP's refusal fails the query, and
ORDERBY's text is "Unexpected value for ORDER argument in ORDERBY function.
..." too (build_b257d.py e_cx_if_false, e_topnskip_iferror,
e_orderby_text). The engine checks a measure's text for them before
evaluating it (_order_definition_error), for all four functions; only
CONCATENATEX's is measured in a branch never taken.

Expected values: Power BI Desktop 2.152 over ADOMD, by probe id --
F:/_desktop_oracle/build_b257c.py group E (F:/_tmp_pbix/rel148/desk/
desk_b257c.json; TS = (1, p), (1, q), (2, r), (3, s)), build_b257d.py group
E (desk_b257d.json) and the SAMPLE record above.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"], [3, "s"]]}}
TAIL = " Use 0/FALSE/DESC for descending order or 1/TRUE/ASC for ascending order."


def _run(expr):
    """(value, error) of the measure."""
    eng = de.DAXEngine()
    value = eng.evaluate_measure("m", de.DAXContext(TABLES, {"m": expr}))
    return value, eng.eval_errors.get("m")


@pytest.mark.parametrize("probe,expr,fn", [
    ('e_cx_iferror', 'IFERROR(CONCATENATEX(TS, TS[L], "", TS[L], 2), "caught")', 'CONCATENATEX'),
    ('e_topn_iferror', 'IFERROR(COUNTROWS(TOPN(1, TS, TS[L], 2)), -1)', 'TOPN'),
])
def test_matches_desktop(probe, expr, fn):
    value, err = _run(expr)
    assert value is None
    assert err.endswith(f"Unexpected value for ORDER argument in {fn} function." + TAIL)


def test_sample_refusal_escapes_iferror():
    """SAMPLE refuses a bad order before the query runs (the SAMPLE record)."""
    value, err = _run('IFERROR(COUNTROWS(SAMPLE(1, TS, TS[L], 2)), -1)')
    assert value is None
    assert err.endswith("Unexpected value for ORDER argument in SAMPLE function." + TAIL)


def test_the_refusal_is_marked_uncatchable():
    """The refusal itself carries the mark, whichever function raises it."""
    with pytest.raises(de._DAXEvaluationError) as info:
        de.DAXEngine._order_pairs('TOPNSKIP', ['TS[L]', '2'])
    assert getattr(info.value, "_dax_uncatchable", False)
    assert str(info.value).endswith("Unexpected value for ORDER argument in TOPNSKIP function." + TAIL)


# build_b257d.py group E (F:/_tmp_pbix/rel148/desk/desk_b257d.json; TS adds the
# row (0, z), which changes none of these)
@pytest.mark.parametrize("probe,expr,fn", [
    # IFERROR around TOPNSKIP's refusal: Desktop fails the query (round 2
    # caught it)
    ('e_topnskip_iferror', 'IFERROR(COUNTROWS(TOPNSKIP(1, 0, TS, TS[L], 2)), -1)', 'TOPNSKIP'),
    # the refusal in a branch never taken: Desktop fails the measure, as
    # ORDERBY's (we answered "ok")
    ('e_cx_if_false', 'IF(FALSE(), CONCATENATEX(TS, TS[L], "", TS[L], 2), "ok")', 'CONCATENATEX'),
    # ORDERBY's text is Desktop's English too (we answered "Unexpected value
    # for the ORDER argument in the function ORDERBY. ...")
    ('e_orderby_text', 'COUNTROWS(WINDOW(1, ABS, 1, ABS, ALL(TS[G]), ORDERBY(TS[G], 2)))', 'ORDERBY'),
])
def test_b257d_matches_desktop(probe, expr, fn):
    value, err = _run(expr)
    assert value is None
    assert (err or "").endswith(f"Unexpected value for ORDER argument in {fn} function." + TAIL)


def test_the_refusal_is_read_off_the_measure_text():
    """Exact on the rule (_order_definition_error): the first refused order
    of TOPN, TOPNSKIP, SAMPLE or CONCATENATEX anywhere in the measure, past
    literals, brackets and comments; the orders _order_pairs takes refuse
    nothing."""
    f = de._order_definition_error
    assert f('IF(FALSE(), COUNTROWS(TOPN(1, TS, TS[L], 2)), 1)').startswith(
        "Unexpected value for ORDER argument in TOPN function.")
    assert f('IF(FALSE(), COUNTROWS(SAMPLE(1, TS, TS[L], "ASC")), 1)').startswith(
        "Unexpected value for ORDER argument in SAMPLE function.")
    assert f('1 + COUNTROWS(TOPNSKIP(1, 0, TS, TS[G], DESC, TS[L], 1 + 0))').startswith(
        "Unexpected value for ORDER argument in TOPNSKIP function.")
    assert f('CONCATENATEX(TS, TS[L], ",", TS[G], DESC, TS[L], ASC)') is None
    assert f('CONCATENATEX(TS, TS[L], ",", TS[G], desc -- 2\n)') is None
    assert f('COUNTROWS(TOPN(1, TS, TS[L], TRUE(), TS[G]))') is None
    assert f('"TOPN(1, TS, TS[L], 2)" & [TOPN(1, x, y, 2)]') is None
    assert f('MYTOPN(1, TS, TS[L], 2)') is None
    assert f('COUNTROWS(TOPNSKIP(1, 0, TS))') is None
