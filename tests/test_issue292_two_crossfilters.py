"""#292: two CROSSFILTERs of one relationship in one CALCULATE are an error.

What was wrong: _fn_calculate applied CROSSFILTER(F[Order], Cal[Date], None) and then
CROSSFILTER(F[Order], Cal[Date], Both) one after the other: the None switched the relationship
off, the Both did not switch it back on, and the sum took every row (16383).

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256b
(build_b256b.py), over the model of tests/test_issue250_userelationship_date_part.py: x_two_cf is
"The cross-filtering direction of a relationship cannot be set (using the CROSSFILTER function)
more than once in a single CALCULATE function." (translated from Desktop's Norwegian UI). Two
USERELATIONSHIPs of the same table pair are accepted: x_two_ur 96, F[Order]'s rows of January 2.
"""
import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

J2 = "Cal[Date] = DATE(2024, 1, 2)"


def test_two_crossfilters_of_one_relationship():
    expr = (f"CALCULATE(SUM(F[v]), CROSSFILTER(F[Order], Cal[Date], None), "
            f"CROSSFILTER(F[Order], Cal[Date], Both), {J2})")
    de._SHARED_FILTER_CACHES.clear()
    ctx = de.DAXContext(TABLES, dict(MEASURES), None, None, {}, RELS)
    with pytest.raises(DAXEvaluationError, match="more than once in a single CALCULATE function"):
        de.DAXEngine()._eval_expr(expr, ctx)


def test_two_userelationships_of_one_pair():
    expr = f"CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), USERELATIONSHIP(F[Order], Cal[Date]), {J2})"
    de._SHARED_FILTER_CACHES.clear()
    assert de.evaluate_measure("x_two_ur", TABLES, {**MEASURES, "x_two_ur": expr}, {}, relationships=RELS) == 96


# Battery B256c (build_b256c.py), Desktop 2.152: reported before the measure runs -- IFERROR in
# the expression does not catch it, a branch never taken fails too. The first draft of this fix:
# -1 and 1 (0.9.147: 16383 and 1).
ONONE = "CROSSFILTER(F[Order], Cal[Date], None)"
OBOTH = "CROSSFILTER(F[Order], Cal[Date], Both)"
STATIC = {
    "c_iferr_two_cf": f"IFERROR(CALCULATE(SUM(F[v]), {ONONE}, {OBOTH}, {J2}), -1)",
    "c_iffalse_two_cf": f"IF(FALSE(), CALCULATE(SUM(F[v]), {ONONE}, {OBOTH}), 1)",
}


@pytest.mark.parametrize("name", list(STATIC))
def test_reported_before_the_measure_runs(name):
    de._SHARED_FILTER_CACHES.clear()
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {**MEASURES, **STATIC}, None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) is None
    assert eng.eval_errors.get(name, "").endswith(
        "The cross-filtering direction of a relationship cannot be set (using the CROSSFILTER "
        "function) more than once in a single CALCULATE function."), eng.eval_errors.get(name)
