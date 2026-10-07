"""Issue #87: CALCULATE applied its modifiers in argument order.

DAX applies the modifiers -- ALL, ALLEXCEPT, ALLSELECTED, REMOVEFILTERS,
USERELATIONSHIP, CROSSFILTER -- before the filter arguments, wherever they are
written. Taken in order, a modifier written after a filter removed it:
CALCULATE([S], Cust[Region] = "West", ALL(Cust)) returned the all-regions
total. KEEPFILTERS also intersected with the context from BEFORE the
modifiers, and a time-intelligence argument rebuilt the context, dropping the
ALL(table) snapshot so the filter ALL had removed came back.

Every expected value is Power BI Desktop 2.152's (over ADOMD), evaluated under
an outer Cust[Region] = "East", and under East plus Cal[Date] = 2024-01-05.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

MEASURES = {'w_all': 'CALCULATE([S], Cust[Region] = "West", ALL(Cust))',
 'all_w': 'CALCULATE([S], ALL(Cust), Cust[Region] = "West")',
 'w_rf': 'CALCULATE([S], Cust[Region] = "West", REMOVEFILTERS(Cust))',
 'w_allcol': 'CALCULATE([S], Cust[Region] = "West", ALL(Cust[Region]))',
 'w_allF': 'CALCULATE([S], Cust[Region] = "West", ALL(F))',
 'allF_w': 'CALCULATE([S], ALL(F), Cust[Region] = "West")',
 'w_allexc': 'CALCULATE([S], Cust[Region] = "West", ALLEXCEPT(Cust, Cust[CustKey]))',
 'kf_all': 'CALCULATE([S], KEEPFILTERS(Cust[Region] = "West"), ALL(Cust))',
 'all_kf': 'CALCULATE([S], ALL(Cust), KEEPFILTERS(Cust[Region] = "West"))',
 'f_allF': 'CALCULATE([S], FILTER(ALL(Cal), Cal[Date] <= DATE(2024,1,5)), ALL(F))',
 'allF_f': 'CALCULATE([S], ALL(F), FILTER(ALL(Cal), Cal[Date] <= DATE(2024,1,5)))',
 'A_ytd': 'CALCULATE([S], ALL(F), DATESYTD(Cal[Date]))',
 'ytd_A': 'CALCULATE([S], DATESYTD(Cal[Date]), ALL(F))',
 'ytd_allCust': 'CALCULATE([S], DATESYTD(Cal[Date]), ALL(Cust))',
 'allCust_ytd': 'CALCULATE([S], ALL(Cust), DATESYTD(Cal[Date]))',
 'w_ytd_allCust': 'CALCULATE([S], Cust[Region] = "West", DATESYTD(Cal[Date]), ALL(Cust))',
 'ytd_rf_cal': 'CALCULATE([S], DATESYTD(Cal[Date]), REMOVEFILTERS(Cal))',
 'rf_cal_ytd': 'CALCULATE([S], REMOVEFILTERS(Cal), DATESYTD(Cal[Date]))',
 'plain': '[S]'}
# Desktop 2.152 over ADOMD: {measure: {outer context: value}}
DESKTOP = {'w_all': {'east': 5, 'east_d5': None},
 'all_w': {'east': 5, 'east_d5': None},
 'w_rf': {'east': 5, 'east_d5': None},
 'w_allcol': {'east': 5, 'east_d5': None},
 'w_allF': {'east': 5, 'east_d5': 5},
 'allF_w': {'east': 5, 'east_d5': 5},
 'w_allexc': {'east': 5, 'east_d5': None},
 'kf_all': {'east': 5, 'east_d5': None},
 'all_kf': {'east': 5, 'east_d5': None},
 'f_allF': {'east': 5, 'east_d5': 5},
 'allF_f': {'east': 5, 'east_d5': 5},
 'A_ytd': {'east': 10, 'east_d5': 5},
 'ytd_A': {'east': 10, 'east_d5': 5},
 'ytd_allCust': {'east': 10, 'east_d5': 5},
 'allCust_ytd': {'east': 10, 'east_d5': 5},
 'w_ytd_allCust': {'east': 5, 'east_d5': 2},
 'ytd_rf_cal': {'east': 5, 'east_d5': 3},
 'rf_cal_ytd': {'east': 5, 'east_d5': 3},
 'plain': {'east': 5, 'east_d5': 1}}


DAYS = [datetime(2024, 1, d) for d in range(1, 11)]
TABLES = {
    "Cal": {"columns": ["Date"], "rows": [[d] for d in DAYS]},
    "Cust": {"columns": ["CustKey", "Region"], "rows": [[1, "East"], [2, "West"]]},
    # one row a day, customers alternating 1, 2
    "F": {"columns": ["CustKey", "Date", "v"],
          "rows": [[1 + (i % 2), d, 1] for i, d in enumerate(DAYS)]},
}
RELS = [{"FromTable": "F", "FromColumn": "CustKey", "ToTable": "Cust", "ToColumn": "CustKey",
         "IsActive": 1},
        {"FromTable": "F", "FromColumn": "Date", "ToTable": "Cal", "ToColumn": "Date",
         "IsActive": 1}]
OUTER = {"east": {"Cust.Region": ["East"]},
         "east_d5": {"Cust.Region": ["East"], "Cal.Date": [DAYS[4]]}}


@pytest.mark.parametrize("outer", list(OUTER))
@pytest.mark.parametrize("name", list(MEASURES))
def test_matches_desktop(name, outer):
    measures = {"S": "SUM(F[v])", **MEASURES}
    got = de.evaluate_measures_smart([name], TABLES, measures, OUTER[outer], "Cal", "Date",
                                     RELS, simulate_row_context=False)[name]
    assert got == DESKTOP[name][outer], f"{MEASURES[name]} under {outer}"


def test_a_table_named_like_a_modifier_is_still_filtered():
    # `Allocation[...]` starts with "ALL": it was routed to the ALL branch and
    # its predicate dropped, leaving the outer Type = "B" (2) in force. A
    # filter argument replaces the outer filter on its column (as the measured
    # all_w / allF_w cells above): Type = "A" is 1 + 4.
    t = {"Allocation": {"columns": ["Type", "v"], "rows": [["A", 1], ["B", 2], ["A", 4]]}}
    m = {"S": "SUM(Allocation[v])", "SA": 'CALCULATE([S], Allocation[Type] = "A")'}
    got = de.evaluate_measures_smart(["SA"], t, m, {"Allocation.Type": ["B"]},
                                     simulate_row_context=False)["SA"]
    assert got == 5
