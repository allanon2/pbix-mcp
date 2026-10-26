"""#299: STARTOFYEAR / ENDOFYEAR with a year end argument were empty.

What was wrong: _start_of / _end_of read their whole argument text as the dates. With a year end
after the dates -- ENDOFYEAR(Cal[Date], "12/30") -- that text names no column, the period was
empty, and CALCULATE(SUM(F[v]), ENDOFYEAR(Cal[Date], "12/30")) was BLANK whatever the dates.

Where the expected values come from: Microsoft's DAX reference gives STARTOFYEAR(<dates>
[, <year_end_date>]) and ENDOFYEAR(<dates> [, <year_end_date>]) -- the first / last date of the
year that ends on year_end_date (December 31 when omitted) and holds the dates in context -- and
the values follow by exact arithmetic over the model of
tests/test_issue250_userelationship_date_part.py (each F row's v a distinct power of two; by
order day: December 30 2023 is 3, December 31 4, January 1 2024 24, January 5 4096). The year end
is read as DATESYTD's (#289). Power BI Desktop 2.152 confirms the two it was
asked (battery B256d, build_b256d.py): y_eoy_ye_dec30 3 and y_soy_ye_j3 4.
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

J3 = "Cal[Date] = DATE(2024, 1, 3)"
DEC30 = "Cal[Date] = DATE(2023, 12, 30)"
PROBES = {
    # the year ending December 30 2023 closes on December 30
    "y_eoy_ye_dec30": (f'CALCULATE(CALCULATE(SUM(F[v]), ENDOFYEAR(Cal[Date], "12/30")), {DEC30})', 3),
    # the year holding January 3 2024 starts December 31 2023
    "y_soy_ye_j3": (f'CALCULATE(CALCULATE(SUM(F[v]), STARTOFYEAR(Cal[Date], "12/30")), {J3})', 4),
    "y_soy_iso_j3": (f'CALCULATE(CALCULATE(SUM(F[v]), STARTOFYEAR(Cal[Date], "2023-12-30")), {J3})', 4),
    # the year ending December 30 2024 runs past the calendar: its last day is January 5
    "y_eoy_ye_j3": (f'CALCULATE(CALCULATE(SUM(F[v]), ENDOFYEAR(Cal[Date], "12/30")), {J3})', 4096),
    # December 31 is the default (unchanged: 0.9.147 answered these)
    "y_eoy_default_dec30": (f"CALCULATE(CALCULATE(SUM(F[v]), ENDOFYEAR(Cal[Date])), {DEC30})", 4),
    "y_soy_default_j3": (f"CALCULATE(CALCULATE(SUM(F[v]), STARTOFYEAR(Cal[Date])), {J3})", 24),
    "y_eoy_dec31_dec30": (f'CALCULATE(CALCULATE(SUM(F[v]), ENDOFYEAR(Cal[Date], "12/31")), {DEC30})', 4),
}
ALL_MEASURES = {**MEASURES, **{k: e for k, (e, _w) in PROBES.items()}}


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


@pytest.mark.parametrize("name", list(PROBES))
def test_each_probe_on_a_fresh_cache(name, path):
    got = de.evaluate_measure(name, TABLES, ALL_MEASURES, {}, relationships=RELS)
    assert got == PROBES[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_all_probes_in_one_batch(order, path):
    names = list(PROBES) if order == "forward" else list(reversed(PROBES))
    got = de.evaluate_measures_batch(names, TABLES, dict(ALL_MEASURES), {}, relationships=RELS)
    assert got == {n: w for n, (_e, w) in PROBES.items()}
