"""#291: a date filter clears a row transition's filters on the rest of the date table (#78).

What was wrong: a filter on a date column that Power BI clears its table for (a marked date
table's, or a DateTime relationship column, issue #78) removes the table's other filters, but
only those the CALCULATE did not write itself -- and the row transition's filters on the row's
expanded table (issue #115) counted as written by the CALCULATE. So in
SUMX(F, CALCULATE(SUM(Cal[Bit]), Cal[Date] = DATE(2024, 1, 2))) each row's Cal[Year] and Cal[Bit]
stayed beside the new date and only the two rows ordered on January 2 kept it: 16.

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256b
(build_b256b.py), over the model of tests/test_issue250_userelationship_date_part.py (Cal[Date] is
a DateTime relationship column): r_day_bits 112, January 2's bit (8) for each of the 14 rows;
r_meas_day 16, the two rows SHIPPED that day through [XShipBits] (0.9.147 BLANK); over
VALUES(F[Id]), which filters no expanded table, r_ids_meas_day 16 held already.
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

J2 = "Cal[Date] = DATE(2024, 1, 2)"
PROBES = {
    "r_day_bits": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {J2}))", 112),
    "r_meas_day": (f"SUMX(F, CALCULATE([XShipBits], {J2}))", 16),
    "r_ids_meas_day": (f"SUMX(VALUES(F[Id]), CALCULATE([XShipBits], {J2}))", 16),
}


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


@pytest.mark.parametrize("name", list(PROBES))
def test_the_date_filter_clears_the_transition(name, path):
    expr, want = PROBES[name]
    assert de.evaluate_measure(name, TABLES, {**MEASURES, name: expr}, {}, relationships=RELS) == want
