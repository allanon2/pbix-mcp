"""A CALCULATE filter on a MARKED date table's date column removes the table's other filters.

When a table is marked as a date table, Desktop adds ALL(table) to a CALCULATE
that filters its date column. Without it the classic running total

    CALCULATE(SUM(..), FILTER(ALL('Date'[Date]), 'Date'[Date] <= MAX('Date'[Date])))

kept the Period filter and returned the period's own value under a Year x
Period axis, and CALCULATE(SUM(..), 'Date'[Date] <= DATE(...)) changed with
the Year on the axis.

Measured against Power BI Desktop 2.157 on Microsoft's MIT Corporate Spend
sample ('Date' marked; date column 'Date'), grouped by total, year,
year x period and business area x year: the running total (11 cells), a
`<=` filter (72) and an `=` filter (71) differed; all match now.
ModelReader.date_tables reads the marking (Table.DataCategory = 'Time').
"""
from __future__ import annotations

from datetime import datetime

from pbix_mcp.dax import engine as de

ROWS = [[datetime(2024, m, 1), 2024, m, m] for m in range(1, 7)]
TABLES = {"Date": {"columns": ["Date", "Year", "Month", "v"], "rows": ROWS}}
MARKED = {"Date": "Date"}
MAR = {"Date.Year": [2024], "Date.Month": [3]}


def _ev(expr, filters, date_tables=MARKED):
    return de.evaluate_measures_smart(["M"], TABLES, {"M": expr}, filters, simulate_row_context=False,
                                      date_tables=date_tables)["M"]


RUNNING = "CALCULATE(SUM('Date'[v]), FILTER(ALL('Date'[Date]), 'Date'[Date] <= MAX('Date'[Date])))"


def test_running_total_on_a_marked_date_table():
    assert _ev(RUNNING, MAR) == 1 + 2 + 3


def test_direct_filter_on_the_date_column_ignores_the_month_on_the_axis():
    expr = "CALCULATE(SUM('Date'[v]), 'Date'[Date] <= DATE(2024, 2, 1))"
    assert _ev(expr, MAR) == 1 + 2
    assert _ev(expr, {"Date.Year": [2024], "Date.Month": [5]}) == 1 + 2


def test_unmarked_tables_keep_their_other_filters():
    """Not marked (or the caller did not say): the previous behaviour."""
    assert _ev(RUNNING, MAR, date_tables={}) == 3


def test_filters_written_by_the_same_calculate_are_kept():
    expr = "CALCULATE(SUM('Date'[v]), 'Date'[Date] <= DATE(2024, 6, 1), 'Date'[Month] >= 4)"
    assert _ev(expr, MAR) == 4 + 5 + 6


def test_filters_on_other_columns_do_not_trigger_it():
    assert _ev("CALCULATE(SUM('Date'[v]), 'Date'[Month] = 2)", {"Date.Year": [2024]}) == 2
