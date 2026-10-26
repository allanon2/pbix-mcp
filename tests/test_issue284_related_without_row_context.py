"""#284: RELATED with no row context -- in CALCULATE's expression, or with no iterator at all -- is an error.

What was wrong: _fn_related fell back to the first visible row of the related table when it found
no current row. CALCULATE consumes the row it transitions (it clears _current_row), so
SUMX(F, CALCULATE(RELATED(Cal[Bit]), USERELATIONSHIP(..))) read each row's order day through the
transition's filters (172) and CALCULATE(RELATED(Cal[Bit]), Cal[Date] = DATE(2024, 1, 2)) the
day's bit (8), where Desktop refuses both. With a row context but no active relationship between
the row's table and the column's -- CROSSFILTER(F[Order], Cal[Date], None) -- it was BLANK.

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, batteries B256
(build_b256.py: d_related_userel, d_related_plain) and B256b (build_b256b.py: rl_*), over the
model of tests/test_issue250_userelationship_date_part.py. The error is "The column 'Cal[Bit]'
either doesn't exist or doesn't have a relationship to any table available in the current
context." (translated from Desktop's Norwegian UI) for RELATED inside CALCULATE with any modifier
or none, and for RELATED under CROSSFILTER None of the only relationship; a row context taken
into a variable before the CALCULATE (rl_var 172), RELATED in a plain iterator (d_related_plain
172) and a measure holding the error in a branch never taken (rl_iffalse_meas 1) are no error.
Desktop reports the error before evaluating anything: IF(FALSE(), <it>, 1) fails too
(rl_iffalse), so a measure's expression is checked before it is evaluated
(DAXEngine._static_refusal) -- conservatively, only where every call around the RELATED is one whose
row context is known.
"""
import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

UR = "USERELATIONSHIP(F[Ship], Cal[Date])"
ERRORS = {
    "d_related_userel": (f"SUMX(F, CALCULATE(RELATED(Cal[Bit]), {UR}))", "Cal[Bit]"),
    "rl_plain": ("SUMX(F, CALCULATE(RELATED(Cal[Bit])))", "Cal[Bit]"),
    "rl_norow": ("CALCULATE(RELATED(Cal[Bit]), Cal[Date] = DATE(2024, 1, 2))", "Cal[Bit]"),
    "rl_p_ur": (f"SUMX(F, CALCULATE(RELATED(P[x]), {UR}))", "P[x]"),
    "rl_order_uo": ("SUMX(F, CALCULATE(RELATED(Cal[Bit]), USERELATIONSHIP(F[Order], Cal[Date])))", "Cal[Bit]"),
    "rl_other_pair": ("SUMX(F, CALCULATE(RELATED(Cal[Bit]), USERELATIONSHIP(G[Ship], Cal2[Date])))", "Cal[Bit]"),
    "rl_cfnone": ("SUMX(F, CALCULATE(RELATED(Cal[Bit]), CROSSFILTER(F[Order], Cal[Date], None)))", "Cal[Bit]"),
    "rl_cfboth": ("SUMX(F, CALCULATE(RELATED(Cal[Bit]), CROSSFILTER(F[Order], Cal[Date], Both)))", "Cal[Bit]"),
    "rl_inside_none": ("CALCULATE(SUMX(F, RELATED(Cal[Bit])), CROSSFILTER(F[Order], Cal[Date], None))", "Cal[Bit]"),
}
VALUES = {
    "rl_var": (f"SUMX(F, VAR b = RELATED(Cal[Bit]) RETURN CALCULATE(b, {UR}))", 172),
    "d_related_plain": ("SUMX(F, RELATED(Cal[Bit]))", 172),
    "rl_iffalse_meas": ("IF(FALSE(), [RelBitUR], 1)", 1),
}
ALL_MEASURES = {**MEASURES, "RelBitUR": f"SUMX(F, CALCULATE(RELATED(Cal[Bit]), {UR}))",
                **{k: e for k, (e, _w) in VALUES.items()}}


@pytest.mark.parametrize("name", list(ERRORS))
def test_no_row_context_is_desktops_error(name):
    expr, col = ERRORS[name]
    de._SHARED_FILTER_CACHES.clear()
    ctx = de.DAXContext(TABLES, dict(ALL_MEASURES), None, None, {}, RELS)
    with pytest.raises(DAXEvaluationError) as err:
        de.DAXEngine()._eval_expr(expr, ctx)
    assert err.value.message == (f"The column '{col}' either doesn't exist or doesn't have a relationship "
                                 "to any table available in the current context.")


@pytest.mark.parametrize("name", list(ERRORS))
def test_the_measure_reports_it(name):
    de._SHARED_FILTER_CACHES.clear()
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {**ALL_MEASURES, name: ERRORS[name][0]}, None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) is None
    assert "doesn't have a relationship to any table" in eng.eval_errors.get(name, "")


def test_a_branch_never_taken_fails_too():
    """rl_iffalse: the measure is refused before any of it runs, so the
    branch IF never takes still fails it."""
    expr = f"IF(FALSE(), SUMX(F, CALCULATE(RELATED(Cal[Bit]), {UR})), 1)"
    de._SHARED_FILTER_CACHES.clear()
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {**ALL_MEASURES, "rl_iffalse": expr}, None, None, {}, RELS)
    assert eng.evaluate_measure("rl_iffalse", ctx) is None
    assert eng.eval_errors.get("rl_iffalse", "").endswith(
        "The column 'Cal[Bit]' either doesn't exist or doesn't have a relationship to any table "
        "available in the current context.")


@pytest.mark.parametrize("name", list(VALUES))
def test_a_row_context_is_no_error(name):
    de._SHARED_FILTER_CACHES.clear()
    assert de.evaluate_measure(name, TABLES, ALL_MEASURES, {}, relationships=RELS) == VALUES[name][1]


# Battery B256c (build_b256c.py), Desktop 2.152: IFERROR / ISERROR in the measure's own
# expression do not catch the error, nor does a branch never taken avoid it -- also for RELATED
# with a row context and no active relationship (c_iffalse_noactive, which the first draft of
# this fix answered 1) -- but around a REFERENCE to a measure that fails so, IFERROR catches it:
# c_iferr_related_meas is -1 (the first draft let it escape; 0.9.147 answered 172).
CATCH_ERRORS = {
    "c_iferr_related": "IFERROR(SUMX(F, CALCULATE(RELATED(Cal[Bit]))), -1)",
    "c_iserr_related": "IF(ISERROR(SUMX(F, CALCULATE(RELATED(Cal[Bit])))), -1, 1)",
    "c_iferr_related_noactive": ("IFERROR(CALCULATE(SUMX(F, RELATED(Cal[Bit])), "
                                 "CROSSFILTER(F[Order], Cal[Date], None)), -1)"),
    "c_iffalse_noactive": ("IF(FALSE(), CALCULATE(SUMX(F, RELATED(Cal[Bit])), "
                           "CROSSFILTER(F[Order], Cal[Date], None)), 1)"),
}
CATCH_MEASURES = {**ALL_MEASURES, **CATCH_ERRORS, "c_iferr_related_meas": "IFERROR([RelBitUR], -1)",
                  "c_iserr_related_meas": "IF(ISERROR([RelBitUR]), -1, 1)"}


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


@pytest.mark.parametrize("name", list(CATCH_ERRORS))
def test_iferror_in_the_expression_does_not_catch_it(name, path):
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, dict(CATCH_MEASURES), None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) is None
    assert eng.eval_errors.get(name, "").endswith(
        "The column 'Cal[Bit]' either doesn't exist or doesn't have a relationship to any table "
        "available in the current context."), eng.eval_errors.get(name)


@pytest.mark.parametrize("name", ["c_iferr_related_meas", "c_iserr_related_meas"])
def test_iferror_around_a_reference_catches_it(name, path):
    # c_iserr_related_meas: exact consequence of c_iferr_related_meas (ISERROR sees what
    # IFERROR catches)
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, dict(CATCH_MEASURES), None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) == -1
    assert name not in eng.eval_errors


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_the_catch_probes_in_one_cache(order, path):
    names = list(CATCH_ERRORS) + ["c_iferr_related_meas", "rl_iffalse_meas", "d_related_plain"]
    if order == "reverse":
        names.reverse()
    got = de.evaluate_measures_batch(names, TABLES, dict(CATCH_MEASURES), {}, relationships=RELS)
    assert got == {**{n: None for n in CATCH_ERRORS}, "c_iferr_related_meas": -1,
                   "rl_iffalse_meas": 1, "d_related_plain": 172}


# Battery B256d (build_b256d.py), Desktop 2.152: IFERROR around a reference catches every error
# Desktop reports before evaluating a measure, not RELATED's alone -- a column with no row
# (#287, a_ref_col), USERELATIONSHIP with CROSSFILTER None (#283,
# a_ref_conflict), OneWay_LeftFiltersRight from the many side (#290, a_ref_lfr), two
# CROSSFILTERs of one relationship (#292, a_ref_two_cf) and RELATED over a table of
# columns (#298, a_ref_values_rel): each is -1. 0.9.147 answered what the
# referenced measures answered there: BLANK, 16383, 192, 16383, BLANK.
UR_S = "USERELATIONSHIP(F[Ship], Cal[Date])"
A_HELPERS = {
    "h_col": "SUMX(F, CALCULATE(F[v] + 0))",
    "h_conflict": f"CALCULATE(SUM(F[v]), {UR_S}, CROSSFILTER(F[Ship], Cal[Date], None), "
                  "Cal[Date] = DATE(2024, 1, 2))",
    "h_lfr": f"CALCULATE(SUM(F[v]), {UR_S}, CROSSFILTER(F[Ship], Cal[Date], OneWay_LeftFiltersRight), "
             "Cal[Date] = DATE(2024, 1, 4))",
    "h_two_cf": ("CALCULATE(SUM(F[v]), CROSSFILTER(F[Order], Cal[Date], None), "
                 "CROSSFILTER(F[Order], Cal[Date], Both), Cal[Date] = DATE(2024, 1, 2))"),
    "h_values_rel": "SUMX(VALUES(F[Order]), RELATED(Cal[Bit]))",
}
A_PROBES = {f"a_ref_{h[2:]}": f"IFERROR([{h}], -1)" for h in A_HELPERS}
A_MEASURES = {**CATCH_MEASURES, **A_HELPERS, **A_PROBES}


@pytest.mark.parametrize("name", list(A_PROBES))
def test_iferror_around_a_reference_catches_every_such_error(name, path):
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, dict(A_MEASURES), None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) == -1
    assert name not in eng.eval_errors


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_every_reference_in_one_cache(order, path):
    names = list(A_PROBES) + list(A_HELPERS)
    if order == "reverse":
        names.reverse()
    got = de.evaluate_measures_batch(names, TABLES, dict(A_MEASURES), {}, relationships=RELS)
    assert got == {**{n: -1 for n in A_PROBES}, **{n: None for n in A_HELPERS}}
