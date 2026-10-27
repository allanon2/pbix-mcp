"""CALENDAR and CALENDARAUTO return dates: arithmetic on their column keeps the date type, as on any date.

Over a CALENDAR row the date was carried as text ('2020-01-01'), so [Date] - 3 or
[Date] - 1 * WEEKDAY([Date]) came back as a number of days (43828.0), where
DATE(2020,1,1) - 3 is a date: a calculated date table's week-start column
(StartOfWeek = [Date] - WEEKDAY([Date])) then held numbers.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from pbix_mcp.dax.calc_tables import evaluate_calc_table_expression as calc

pytestmark = pytest.mark.unit

ONE_DAY = "CALENDAR(DATE(2020,1,1), DATE(2020,1,1))"


@pytest.mark.parametrize("expr, want", [
    ("[Date] - 3", datetime(2019, 12, 29)),
    ("[Date] - 1 * WEEKDAY([Date])", datetime(2019, 12, 28)),
    ("[Date] - ( 1 * WEEKDAY([Date]) )", datetime(2019, 12, 28)),
    ("[Date] + 7", datetime(2020, 1, 8)),
])
def test_date_arithmetic_over_a_calendar_row_is_a_date(expr, want):
    got, err = calc(f'SELECTCOLUMNS({ONE_DAY}, "c", {expr})', {})
    assert err is None and got["rows"] == [[want]]
    assert type(got["rows"][0][0]) is datetime


def test_the_calendar_column_is_a_date_and_reads_as_one():
    got, err = calc(f'SELECTCOLUMNS({ONE_DAY}, "D", [Date], "Y", YEAR([Date]), "M", FORMAT([Date], "MMM YYYY"))', {})
    assert err is None and got["rows"] == [[datetime(2020, 1, 1), 2020, "Jan 2020"]]


def test_calendarauto_rows_are_dates_too():
    tables = {"T": {"columns": ["D"], "rows": [[datetime(2021, 3, 5)]]}}
    got, err = calc("CALENDARAUTO()", tables)
    assert err is None and type(got["rows"][0][0]) is datetime
    assert got["rows"][0][0] == datetime(2021, 1, 1) and got["rows"][-1][0] == datetime(2021, 12, 31)
