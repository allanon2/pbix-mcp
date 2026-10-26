"""#290: CROSSFILTER's OneWay_LeftFiltersRight / OneWay_RightFiltersLeft name the side that filters.

What was wrong: _apply_crossfilter knew None, OneWay and Both only and read every other word as
Both, so OneWay_RightFiltersLeft on F[Ship] -> Cal[Date] let F filter Cal (8 where Desktop says
127) and OneWay_LeftFiltersRight, which Desktop refuses on a many-to-one relationship, answered.

The rule, from Desktop: left is the first column's side, right the second's. On a many-to-one
relationship the one side filtering the many side is OneWay; the many side alone filtering the
one side is the error "The CROSSFILTER function cannot be used to enable only the many side of
the many-to-one relationship to filter the one side." (translated from Desktop's Norwegian UI),
reported before evaluation (no "Calculation error in measure" prefix).

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256b
(build_b256b.py), over the model of tests/test_issue250_userelationship_date_part.py: under
USERELATIONSHIP(F[Ship], Cal[Date]), OneWay_RightFiltersLeft keeps every Cal day under F[v] = 8
(x_rfl_bits 127, the sum of the seven day bits) and still filters F by Cal (x_rfl_sum 192);
OneWay_LeftFiltersRight is the error (x_lfr_bits, x_lfr_sum; x_lfr_order_sum on the active
F[Order]). Controls: Both and OneWay written before the USERELATIONSHIP (x_both_first 8,
x_oneway_first 192).
"""
import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

UR = "USERELATIONSHIP(F[Ship], Cal[Date])"
LFR = "CROSSFILTER(F[Ship], Cal[Date], OneWay_LeftFiltersRight)"
RFL = "CROSSFILTER(F[Ship], Cal[Date], OneWay_RightFiltersLeft)"
J4 = "Cal[Date] = DATE(2024, 1, 4)"
VALUES = {
    "x_rfl_bits": (f"CALCULATE(SUM(Cal[Bit]), {UR}, {RFL}, F[v] = 8)", 127),
    "x_rfl_sum": (f"CALCULATE(SUM(F[v]), {UR}, {RFL}, {J4})", 192),
    "x_both_first": (f"CALCULATE(SUM(Cal[Bit]), CROSSFILTER(F[Ship], Cal[Date], Both), {UR}, F[v] = 8)", 8),
    "x_oneway_first": (f"CALCULATE(SUM(F[v]), CROSSFILTER(F[Ship], Cal[Date], OneWay), {UR}, {J4})", 192),
}
ERRORS = {
    "x_lfr_bits": f"CALCULATE(SUM(Cal[Bit]), {UR}, {LFR}, F[v] = 8)",
    "x_lfr_sum": f"CALCULATE(SUM(F[v]), {UR}, {LFR}, {J4})",
    "x_lfr_order_sum": ("CALCULATE(SUM(F[v]), CROSSFILTER(F[Order], Cal[Date], OneWay_LeftFiltersRight), "
                        "Cal[Date] = DATE(2024, 1, 2))"),
}


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


@pytest.mark.parametrize("name", list(VALUES))
def test_the_side_that_filters(name, path):
    expr, want = VALUES[name]
    assert de.evaluate_measure(name, TABLES, {**MEASURES, name: expr}, {}, relationships=RELS) == want


@pytest.mark.parametrize("name", list(ERRORS))
def test_the_many_side_alone_is_desktops_error(name):
    de._SHARED_FILTER_CACHES.clear()
    ctx = de.DAXContext(TABLES, dict(MEASURES), None, None, {}, RELS)
    with pytest.raises(DAXEvaluationError, match="cannot be used to enable only the many side"):
        de.DAXEngine()._eval_expr(ERRORS[name], ctx)


# Battery B256c (build_b256c.py), Desktop 2.152: reported before the measure runs. IFERROR does
# not catch it (c_iferr_lfr) and a branch never taken fails too (c_iffalse_lfr; the first draft
# of this fix answered 1, 0.9.147 1; c_iferr_lfr 0.9.147 192).
STATIC = {
    "c_iferr_lfr": f"IFERROR(CALCULATE(SUM(F[v]), {UR}, {LFR}, {J4}), -1)",
    "c_iffalse_lfr": f"IF(FALSE(), CALCULATE(SUM(F[v]), {UR}, {LFR}), 1)",
}


@pytest.mark.parametrize("name", list(STATIC))
def test_reported_before_the_measure_runs(name, path):
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {**MEASURES, **STATIC}, None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) is None
    assert eng.eval_errors.get(name, "").endswith(
        "The CROSSFILTER function cannot be used to enable only the many side of the many-to-one "
        "relationship to filter the one side."), eng.eval_errors.get(name)
