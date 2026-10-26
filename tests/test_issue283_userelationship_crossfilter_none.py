"""#283: USERELATIONSHIP and CROSSFILTER(.., None) on one relationship in one CALCULATE is an error.

What was wrong: _fn_calculate applied USERELATIONSHIP and CROSSFILTER one after the other in
written order and nothing checked them against each other, so the last one written won:
CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), CROSSFILTER(F[Ship], Cal[Date],
None), ..) answered 16383 and the same with CROSSFILTER written first 24.

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, batteries B256 and
B256b (build_b256.py / build_b256b.py), over the model of
tests/test_issue250_userelationship_date_part.py. Desktop answers "The USERELATIONSHIP and
CROSSFILTER functions are used in a single CALCULATE function to specify incompatible settings."
(translated from its Norwegian UI) whatever the argument order (d_userel_none_ship,
x_none_first), the column order inside CROSSFILTER (x_none_cols_rev) and also on the active
relationship (x_none_active). CROSSFILTER None on the OTHER relationship of the pair is no
conflict: d_none_order_userel 24; CROSSFILTER None alone on the inactive F[Ship] changes nothing:
x_none_alone 96 (F[Order]'s rows of the day).
"""
import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

UR = "USERELATIONSHIP(F[Ship], Cal[Date])"
J2 = "Cal[Date] = DATE(2024, 1, 2)"
ERRORS = {
    "d_userel_none_ship": f"CALCULATE(SUM(F[v]), {UR}, CROSSFILTER(F[Ship], Cal[Date], None), {J2})",
    "x_none_first": f"CALCULATE(SUM(F[v]), CROSSFILTER(F[Ship], Cal[Date], None), {UR}, {J2})",
    "x_none_cols_rev": f"CALCULATE(SUM(F[v]), {UR}, CROSSFILTER(Cal[Date], F[Ship], None), {J2})",
    "x_none_active": (f"CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Order], Cal[Date]), "
                      f"CROSSFILTER(F[Order], Cal[Date], None), {J2})"),
}
VALUES = {
    "d_none_order_userel": (f"CALCULATE(SUM(F[v]), CROSSFILTER(F[Order], Cal[Date], None), {UR}, {J2})", 24),
    "x_none_alone": (f"CALCULATE(SUM(F[v]), CROSSFILTER(F[Ship], Cal[Date], None), {J2})", 96),
}


@pytest.mark.parametrize("name", list(ERRORS))
def test_the_conflict_is_desktops_error(name):
    de._SHARED_FILTER_CACHES.clear()
    ctx = de.DAXContext(TABLES, dict(MEASURES), None, None, {}, RELS)
    with pytest.raises(DAXEvaluationError, match="USERELATIONSHIP and CROSSFILTER functions are used in a single "
                                                 "CALCULATE function to specify incompatible settings"):
        de.DAXEngine()._eval_expr(ERRORS[name], ctx)


@pytest.mark.parametrize("name", list(ERRORS))
def test_the_measure_reports_it(name):
    de._SHARED_FILTER_CACHES.clear()
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {**MEASURES, name: ERRORS[name]}, None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) is None
    assert "incompatible settings" in eng.eval_errors.get(name, "")


@pytest.mark.parametrize("name", list(VALUES))
def test_no_conflict(name):
    de._SHARED_FILTER_CACHES.clear()
    expr, want = VALUES[name]
    assert de.evaluate_measure(name, TABLES, {**MEASURES, name: expr}, {}, relationships=RELS) == want


# Battery B256c (build_b256c.py), Desktop 2.152: Desktop reports the conflict before it
# evaluates the measure -- IFERROR in the expression does not catch it (c_iferr_conflict) and a
# branch never taken fails too (c_iffalse_conflict). The first draft of this fix treated it as a
# calculation error: -1 and 1 (0.9.147: 16383 and 1).
STATIC = {
    "c_iferr_conflict": f"IFERROR(CALCULATE(SUM(F[v]), {UR}, CROSSFILTER(F[Ship], Cal[Date], None), {J2}), -1)",
    "c_iffalse_conflict": f"IF(FALSE(), CALCULATE(SUM(F[v]), {UR}, CROSSFILTER(F[Ship], Cal[Date], None)), 1)",
}


@pytest.mark.parametrize("name", list(STATIC))
def test_reported_before_the_measure_runs(name):
    de._SHARED_FILTER_CACHES.clear()
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {**MEASURES, **STATIC}, None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) is None
    assert eng.eval_errors.get(name, "").endswith(
        "The USERELATIONSHIP and CROSSFILTER functions are used in a single CALCULATE function to "
        "specify incompatible settings."), eng.eval_errors.get(name)
