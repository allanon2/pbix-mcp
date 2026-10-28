"""#354 (reported with PR #353 by @allanon2): CALENDAR's and CALENDARAUTO's
rows hold their days as dates.

What was wrong (0.9.149): each row carried its day as ISO text
('2020-01-01'). Arithmetic converts text to a number, and keeps a date's type
only for a date operand, so over a CALENDAR row [Date] - 3 was 43828.0 and
[Date] - 1 * WEEKDAY([Date]) 43827.0, where DATE(2020, 1, 1) - 3 is a date. A
calculated date table's week-start column then held numbers, and the text of
[Date] itself read "2020-01-01" where a date reads "1/1/2020".

Expected values:
- Microsoft's CALENDAR: "Returns a table with a single column named "Date"
  that contains a contiguous set of dates", from start to end inclusive;
  CALENDARAUTO's range is whole years of the model's dates.
- The Artificial Intelligence sample (Desktop-saved) stores Case Calendar[WEEK]
  = [Date] - (1 * WEEKDAY([Date])) as dates: 2020-01-01 -> 2019-12-28,
  2021-12-31 -> 2021-12-25 (test_issue356_generate_calc_table_columns.py
  recomputes its whole table).
- Otherwise exact date arithmetic (2020-01-01 is a Wednesday, WEEKDAY 4).
- Power BI Desktop 2.152, build_b358.py (desk.json under
  F:\\_tmp_pbix\\pr352\\b358\\): d_minus3_text "12/29/2019,12/30/2019",
  d_plus7_text "1/8/2020,1/9/2020", d_weekday_text "12/28/2019,12/28/2019",
  d_cal_text "1/1/2020,1/2/2020", d_minus3_isdatetime "T,T", c_treatas 11,
  c_compare 5, c_serial_sum 90585 (0.9.149: "43828,43829", ..., "N,N").
"""
from __future__ import annotations

from datetime import datetime

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.dax.calc_tables import evaluate_calc_table_expression as build_table

pytestmark = pytest.mark.unit

NEW_YEAR = "CALENDAR(DATE(2020, 1, 1), DATE(2020, 1, 2))"


def _scalar(expr, tables=None):
    ctx = de.DAXContext(tables or {}, {}, None, None, None, [])
    return de.DAXEngine()._eval_expr(expr, ctx)


def _one_column(expr, tables=None):
    got, err = build_table(expr, tables or {})
    assert err is None, err
    return [r[0] for r in got["rows"]]


@pytest.mark.parametrize("shift, expected", [
    ("[Date] - 3", [datetime(2019, 12, 29), datetime(2019, 12, 30)]),
    ("[Date] + 7", [datetime(2020, 1, 8), datetime(2020, 1, 9)]),
    ("[Date] - 1 * WEEKDAY([Date])", [datetime(2019, 12, 28), datetime(2019, 12, 28)]),
    ("[Date] - ( 1 * WEEKDAY([Date]) )", [datetime(2019, 12, 28), datetime(2019, 12, 28)]),
    ("[Date] - WEEKDAY([Date]) + 1", [datetime(2019, 12, 29), datetime(2019, 12, 29)]),
])
def test_days_moved_off_a_calendar_row_keep_their_type(shift, expected):
    got = _one_column(f'SELECTCOLUMNS({NEW_YEAR}, "c", {shift})')
    assert got == expected
    assert all(type(v) is datetime for v in got)


def test_calendar_rows_hold_datetimes_at_midnight():
    got = _one_column("CALENDAR(DATE(2024, 2, 28), DATE(2024, 3, 1))")
    assert got == [datetime(2024, 2, 28), datetime(2024, 2, 29), datetime(2024, 3, 1)]
    assert all(type(v) is datetime for v in got)


def test_a_time_of_day_in_the_bounds_is_dropped():
    # CALENDAR's rows are whole days from the start's day to the end's day
    got = _one_column("CALENDAR(DATE(2024, 1, 1) + TIME(18, 0, 0), DATE(2024, 1, 2) + TIME(6, 0, 0))")
    assert got == [datetime(2024, 1, 1), datetime(2024, 1, 2)]


def test_calendarauto_rows_are_dates_of_whole_years():
    facts = {"F": {"columns": ["When"], "rows": [[datetime(2021, 3, 5)], [datetime(2021, 8, 1)]]}}
    got = _one_column("CALENDARAUTO()", facts)
    assert got[0] == datetime(2021, 1, 1) and got[-1] == datetime(2021, 12, 31)
    assert len(got) == 365 and all(type(v) is datetime for v in got)


def test_the_text_of_a_calendar_date_is_a_dates_text():
    # 0.9.149: "43828,43829" and "2020-01-01,2020-01-02"
    assert _scalar(f'CONCATENATEX(SELECTCOLUMNS({NEW_YEAR}, "c", [Date] - 3), [c] & "", ",", [c], ASC)') \
        == _scalar('CONCATENATEX({DATE(2019, 12, 29), DATE(2019, 12, 30)}, [Value] & "", ",", [Value], ASC)')
    assert _scalar(f'CONCATENATEX({NEW_YEAR}, [Date] & "", ",", [Date], ASC)') \
        == _scalar('DATE(2020, 1, 1) & "," & DATE(2020, 1, 2)')


def test_isdatetime_of_the_arithmetic():
    assert _scalar(f'CONCATENATEX(SELECTCOLUMNS({NEW_YEAR}, "c", [Date] - 3), '
                   f'IF(ISDATETIME([c]), "T", "N"), ",")') == "T,T"


def test_the_week_start_the_sample_stores():
    # Case Calendar[WEEK] of the Artificial Intelligence sample, Desktop's values
    got = _one_column('SELECTCOLUMNS(CALENDAR(DATE(2021, 12, 30), DATE(2021, 12, 31)), '
                      '"w", VAR d = WEEKDAY([Date]) RETURN [Date] - ( 1 * d ))')
    assert got == [datetime(2021, 12, 25), datetime(2021, 12, 25)]


# the consumers of CALENDAR's rows read the dates as they read the text


DAYS = {"DD": {"columns": ["Date", "V"],
               "rows": [[datetime(2024, 1, 1), 1], [datetime(2024, 1, 2), 10], [datetime(2024, 1, 3), 100]]}}
TEN = "CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 10))"


@pytest.mark.parametrize("dax, expected", [
    (f"COUNTROWS(FILTER({TEN}, [Date] > DATE(2024, 1, 5)))", 5),
    ('CONCATENATEX(CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 2)), FORMAT([Date], "yyyy-mm-dd"), ",", [Date], ASC)',
     "2024-01-01,2024-01-02"),
    (f'FORMAT(MAXX({TEN}, [Date]), "MMM YYYY")', "Jan 2024"),
    (f"MAXX({TEN}, YEAR([Date]) * 100 + MONTH([Date]))", 202401),
    (f"SUMX({TEN}, DATEDIFF(DATE(2024, 1, 1), [Date], DAY))", 45),
    ("SUMX(CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 2)), [Date] * 1)", 45292 + 45293),
    ("CALCULATE(SUM(DD[V]), TREATAS(CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 2)), DD[Date]))", 11),
    (f"COUNTROWS(INTERSECT({TEN}, VALUES(DD[Date])))", 3),
    (f"COUNTROWS(EXCEPT({TEN}, VALUES(DD[Date])))", 7),
    ("COUNTROWS(INTERSECT(CALENDAR(DATE(2024, 1, 2), DATE(2024, 1, 5)), "
     "GENERATESERIES(DATE(2024, 1, 1), DATE(2024, 1, 3), 1)))", 2),
    (f'COUNTROWS(FILTER({TEN}, CONTAINS(DD, DD[Date], [Date])))', 3),
    (f"MAXX({TEN}, [Date]) - MINX({TEN}, [Date])", datetime(1900, 1, 8)),
])
def test_consumers_of_calendar_rows(dax, expected):
    assert _scalar(dax, DAYS) == expected


def test_a_calculated_table_of_calendar_is_typed_datetime():
    # the stored column was the text "2024-01-01 00:00:00" (or ISO text)
    from pbix_mcp.server import _infer_calc_type_name
    got = _one_column("CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 3))")
    assert _infer_calc_type_name(got) == "DateTime"
    assert _infer_calc_type_name(["2024-01-01"]) == "String"
    assert _infer_calc_type_name([1, 2.5]) == "Double"
