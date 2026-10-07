"""Start/end-of-period and balance functions use dates that EXIST in the column.

On a date table that is not daily -- here one row per month, dated the 1st --
ENDOFMONTH / ENDOFYEAR built the calendar end (the 31st, Dec 31), which matched
no row, so CALCULATE(e, ENDOFYEAR(d)) and CLOSINGBALANCEYEAR(e, d) were BLANK at
every level. Opening balances used the calendar day before the period, also
absent; and STARTOF*/ENDOF* inside CALCULATE kept the date table's other
filters, so CALCULATE(e, STARTOFYEAR(d)) under a Month filter was BLANK.

Measured against Power BI Desktop 2.157 on Microsoft's MIT Corporate Spend
sample ('Date': one row per month, marked as a date table), grouped by total,
year, year x period and business area x year: CALCULATE with START/ENDOF
MONTH/QUARTER/YEAR and OPENING/CLOSINGBALANCE MONTH/QUARTER/YEAR now equal
Desktop in every cell (before: up to 21 of 81 cells each differed).
"""
from __future__ import annotations

from datetime import datetime

from pbix_mcp.dax import engine as de

# One row per month for 2023 and 2024, dated the 1st; v = month number + 100 * (year - 2023).
ROWS = [[datetime(y, m, 1), y, m, m + 100 * (y - 2023)] for y in (2023, 2024) for m in range(1, 13)]
TABLES = {"Date": {"columns": ["Date", "Year", "Month", "v"], "rows": ROWS}}


def _ev(expr, filters):
    ctx = de.DAXContext(TABLES, {"M": expr}, None, None, filters, [])
    # Marked as a date table, like Corporate Spend's 'Date' (see #78: only a
    # marked table or a DateTime relationship column clears the other filters).
    ctx.date_tables = {"Date": "Date"}
    return de.DAXEngine().evaluate_measure("M", ctx)


Y24 = {"Date.Year": [2024]}
MAR24 = {"Date.Year": [2024], "Date.Month": [3]}
V = "SUM('Date'[v])"


def test_end_of_period_is_the_last_existing_date():
    assert _ev(f"CALCULATE({V}, ENDOFYEAR('Date'[Date]))", Y24) == 112      # 2024-12-01
    assert _ev(f"CALCULATE({V}, ENDOFQUARTER('Date'[Date]))", MAR24) == 103  # 2024-03-01
    assert _ev(f"CALCULATE({V}, ENDOFMONTH('Date'[Date]))", MAR24) == 103


def test_start_of_period_replaces_the_date_tables_filters():
    assert _ev(f"CALCULATE({V}, STARTOFYEAR('Date'[Date]))", MAR24) == 101   # January, not BLANK
    assert _ev(f"CALCULATE({V}, STARTOFQUARTER('Date'[Date]))", MAR24) == 101


def test_closing_balances():
    assert _ev(f"CLOSINGBALANCEYEAR({V}, 'Date'[Date])", Y24) == 112
    assert _ev(f"CLOSINGBALANCEQUARTER({V}, 'Date'[Date])", MAR24) == 103
    assert _ev(f"CLOSINGBALANCEMONTH({V}, 'Date'[Date])", MAR24) == 103


def test_opening_balances_use_the_last_date_before_the_period():
    assert _ev(f"OPENINGBALANCEMONTH({V}, 'Date'[Date])", MAR24) == 102      # 2024-02-01
    assert _ev(f"OPENINGBALANCEQUARTER({V}, 'Date'[Date])", {"Date.Year": [2024], "Date.Month": [5]}) == 103
    assert _ev(f"OPENINGBALANCEYEAR({V}, 'Date'[Date])", Y24) == 12          # 2023-12-01


def test_no_earlier_date_means_blank():
    assert _ev(f"OPENINGBALANCEYEAR({V}, 'Date'[Date])", {"Date.Year": [2023]}) is None
