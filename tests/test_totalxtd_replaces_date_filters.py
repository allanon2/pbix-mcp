"""TOTALYTD/QTD/MTD replace the date table's filters, as CALCULATE(e, DATESxTD(d)) does.

TOTALYTD(e, d) is CALCULATE(e, DATESYTD(d)) per Microsoft's DAX reference. On a
date table, the dates the function computes replace every other filter on that
table: grouped by Year x Period, the YTD for a period runs from January to that
period, not just over the period itself.

Measured against Power BI Desktop 2.157 (engine 17.0.83.18) on Microsoft's MIT
"Corporate Spend" sample, whose 'Date' table is marked as a date table:
``[Amount] := TOTALYTD(SUM([Value]), 'Date'[Date]) * .3`` at 2014 / Period 10 is
1,042,148,723.37 in Desktop (January to October) and 109,053,968.15 here before
this fix (October alone). TOTALYTD(SUM(..), 'Date'[Date]) differed in 11 of 48
year x period cells and TOTALQTD in 8; after the fix both equal Desktop in every
cell grouped by year, year x period and business area x year, as the
CALCULATE(..., DATESYTD/DATESQTD(...)) forms already did.

The miniature below is small enough to check by hand. Its dates are daily, so
it also shows TOTALMTD, which a month-grain date table cannot.
"""
from __future__ import annotations

from datetime import datetime

from pbix_mcp.dax import engine as de

TABLES = {
    "Date": {
        "columns": ["Date", "Year", "Month", "Day", "v"],
        "rows": [
            [datetime(2024, 1, 10), 2024, 1, 10, 1],
            [datetime(2024, 2, 10), 2024, 2, 10, 2],
            [datetime(2024, 3, 5), 2024, 3, 5, 4],
            [datetime(2024, 3, 15), 2024, 3, 15, 8],
            [datetime(2024, 4, 10), 2024, 4, 10, 16],
        ],
    }
}


def _ev(expr, filters):
    ctx = de.DAXContext(TABLES, {"M": expr}, None, None, filters, [])
    return de.DAXEngine().evaluate_measure("M", ctx)


MARCH = {"Date.Year": [2024], "Date.Month": [3]}
MARCH_15 = {"Date.Year": [2024], "Date.Month": [3], "Date.Day": [15]}


def test_totalytd_runs_from_january_under_a_month_filter():
    assert _ev("TOTALYTD(SUM('Date'[v]), 'Date'[Date])", MARCH) == 1 + 2 + 4 + 8


def test_totalqtd_runs_from_the_quarter_start_under_a_month_filter():
    assert _ev("TOTALQTD(SUM('Date'[v]), 'Date'[Date])", MARCH) == 1 + 2 + 4 + 8


def test_totalmtd_runs_from_the_month_start_under_a_day_filter():
    assert _ev("TOTALMTD(SUM('Date'[v]), 'Date'[Date])", MARCH_15) == 4 + 8


def test_totals_equal_their_calculate_forms():
    for total, dates, flt in (("TOTALYTD", "DATESYTD", MARCH), ("TOTALQTD", "DATESQTD", MARCH),
                              ("TOTALMTD", "DATESMTD", MARCH_15)):
        assert _ev(f"{total}(SUM('Date'[v]), 'Date'[Date])", flt) == \
            _ev(f"CALCULATE(SUM('Date'[v]), {dates}('Date'[Date]))", flt), total


def test_filters_on_other_tables_are_kept():
    tables = dict(TABLES, Other={"columns": ["k"], "rows": [[1]]})
    ctx = de.DAXContext(tables, {"M": "TOTALYTD(SUM('Date'[v]), 'Date'[Date])"}, None, None,
                        {**MARCH, "Other.k": [1]}, [])
    assert de.DAXEngine().evaluate_measure("M", ctx) == 15
