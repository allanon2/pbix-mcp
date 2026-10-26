"""#297: OPENINGBALANCEYEAR / CLOSINGBALANCEYEAR apply their year end.

What was wrong: the YEAR balances took their year end (the last argument) apart from the filters
and never applied it: the year always ended on December 31. Under Cal[Date] = DATE(2024, 1, 3),
OPENINGBALANCEYEAR(SUM(F[v]), Cal[Date], "12/30") was December 31 2023's 4, and
CLOSINGBALANCEYEAR(.., "12/30") under December 30 2023 was December 31's 4 as well.

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256c
(build_b256c.py y_*), over the model of tests/test_issue250_userelationship_date_part.py: with
year end December 30 the year containing January 3 2024 starts December 31 2023, so the opening
balance is December 30's 3 (rows 0 and 1); the closing balance under December 30 2023 is that
day's 3; and with USERELATIONSHIP(F[Ship], Cal[Date]) beside it, the ship shipped on December 30
(row 11, 2048). The ISO spelling "2023-12-30" is the same year end (DATESYTD's,
#289), and a year end of December 31 is the default (exact arithmetic over the same
model).
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

UR = "USERELATIONSHIP(F[Ship], Cal[Date])"
J3 = "Cal[Date] = DATE(2024, 1, 3)"
DEC30 = "Cal[Date] = DATE(2023, 12, 30)"
PROBES = {
    "y_oby_ye_j3": (f'CALCULATE(OPENINGBALANCEYEAR(SUM(F[v]), Cal[Date], "12/30"), {J3})', 3),
    "y_cby_ye_dec30": (f'CALCULATE(CLOSINGBALANCEYEAR(SUM(F[v]), Cal[Date], "12/30"), {DEC30})', 3),
    "y_cby_ur_ye_dec30": (f'CALCULATE(CLOSINGBALANCEYEAR(SUM(F[v]), Cal[Date], {UR}, "12/30"), {DEC30})', 2048),
    # exact arithmetic over the same model
    "y_oby_iso_j3": (f'CALCULATE(OPENINGBALANCEYEAR(SUM(F[v]), Cal[Date], "2023-12-30"), {J3})', 3),
    "y_oby_default_j3": (f"CALCULATE(OPENINGBALANCEYEAR(SUM(F[v]), Cal[Date]), {J3})", 4),
    "y_oby_dec31_j3": (f'CALCULATE(OPENINGBALANCEYEAR(SUM(F[v]), Cal[Date], "12/31"), {J3})', 4),
    # the year ending December 30 2024 holds all of the calendar from December 31 2023: its last
    # day in the calendar is January 5 (row 12)
    "y_cby_ye_j3": (f'CALCULATE(CLOSINGBALANCEYEAR(SUM(F[v]), Cal[Date], "12/30"), {J3})', 4096),
    # no day before the year that starts December 31 2022: BLANK
    "y_oby_ye_dec30": (f'CALCULATE(OPENINGBALANCEYEAR(SUM(F[v]), Cal[Date], "12/30"), {DEC30})', None),
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


def test_the_fiscal_year_bounds():
    from datetime import datetime as D
    b = de.DAXEngine._fiscal_year_bounds
    assert b(D(2024, 1, 3), (12, 30)) == (D(2023, 12, 31), D(2024, 12, 30, 23, 59, 59))
    assert b(D(2023, 12, 30), (12, 30)) == (D(2022, 12, 31), D(2023, 12, 30, 23, 59, 59))
    assert b(D(2024, 7, 1), (6, 30)) == (D(2024, 7, 1), D(2025, 6, 30, 23, 59, 59))
    # a February 29 year end in a year without one ends on February 28
    assert b(D(2023, 3, 1), (2, 29)) == (D(2023, 3, 1), D(2024, 2, 29, 23, 59, 59))
    assert b(D(2023, 2, 1), (2, 29)) == (D(2022, 3, 1), D(2023, 2, 28, 23, 59, 59))
