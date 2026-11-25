"""The date-table functions (pbix_mcp.m.dates; Table.FromList and Splitter.SplitByNothing in tables): Power Query's
rules as Microsoft's function reference and .NET's documented date formatting and calendar give them. Each
expected value is written out by hand from those rules (the week numbers from CalendarWeekRule.FirstDay: week 1
holds 1 January), never computed by the code under test."""
import datetime as dt

import pytest

from pbix_mcp.m import CellError, MError, evaluate
from pbix_mcp.m.load import conforms
from pbix_mcp.m.values import MType

D = dt.date


def err(expr: str, culture: str = "en-US") -> MError:
    with pytest.raises(MError) as e:
        evaluate(expr, culture=culture)
    return e.value


class TestDateParts:
    def test_parts_of_a_date_a_datetime_and_a_datetimezone(self):
        assert evaluate("Date.Year(#date(2024, 2, 29))") == 2024
        assert evaluate("Date.Month(#datetime(2024, 2, 29, 23, 0, 0))") == 2
        # a datetimezone's own clock date, not UTC's (2024-03-01 01:00 +10:00 is 2024-02-29 in UTC)
        assert evaluate("Date.Day(#datetimezone(2024, 3, 1, 1, 0, 0, 10, 0))") == 1

    @pytest.mark.parametrize("month, quarter", [(1, 1), (3, 1), (4, 2), (6, 2), (7, 3), (9, 3), (10, 4), (12, 4)])
    def test_quarter_of_year(self, month, quarter):
        assert evaluate(f"Date.QuarterOfYear(#date(2024, {month}, 15))") == quarter

    def test_null_gives_null(self):
        for f in ("Year", "Month", "Day", "QuarterOfYear", "DayOfWeek", "WeekOfYear", "StartOfWeek", "EndOfWeek"):
            assert evaluate(f"Date.{f}(null)") is None

    def test_a_value_without_a_date(self):
        assert err('Date.Year("2024")').message == "The Date value must contain the Date component."
        assert err("Date.QuarterOfYear(#time(1, 0, 0))").message == "The Date value must contain the Date component."
        assert err("Date.DayOfWeek(5)").message == "We expected a Date value with a Days part."


class TestWeeks:
    def test_day_values(self):
        assert evaluate("{Day.Sunday, Day.Monday, Day.Tuesday, Day.Wednesday, Day.Thursday, Day.Friday, "
                        "Day.Saturday}") == [0, 1, 2, 3, 4, 5, 6]

    def test_day_of_week_counts_from_the_first_day(self):
        # 2024-01-07 is a Sunday
        assert evaluate("Date.DayOfWeek(#date(2024, 1, 7), Day.Monday)") == 6
        assert evaluate("Date.DayOfWeek(#date(2024, 1, 8), Day.Monday)") == 0
        assert evaluate("Date.DayOfWeek(#date(2024, 1, 7), Day.Sunday)") == 0
        assert evaluate("Date.DayOfWeek(#date(2024, 1, 10), Day.Saturday)") == 4

    def test_the_default_first_day_is_the_cultures(self):
        # en-US weeks start on Sunday, en-AU's on Monday
        assert evaluate("Date.DayOfWeek(#date(2024, 1, 7))", culture="en-US") == 0
        assert evaluate("Date.DayOfWeek(#date(2024, 1, 7))", culture="en-AU") == 6
        assert evaluate("Date.WeekOfYear(#date(2024, 1, 7))", culture="en-US") == 2
        assert evaluate("Date.WeekOfYear(#date(2024, 1, 7))", culture="en-AU") == 1
        assert evaluate("Date.StartOfWeek(#date(2024, 1, 10))", culture="en-US") == D(2024, 1, 7)
        assert evaluate("Date.StartOfWeek(#date(2024, 1, 10))", culture="en-AU") == D(2024, 1, 8)

    @pytest.mark.parametrize("date, first, week", [
        ("2024, 1, 1", "Day.Sunday", 1),       # a Monday
        ("2024, 1, 6", "Day.Sunday", 1),
        ("2024, 1, 7", "Day.Sunday", 2),
        ("2024, 12, 31", "Day.Monday", 53),
        ("2023, 1, 1", "Day.Monday", 1),       # a Sunday: week 1 is that one day
        ("2023, 1, 2", "Day.Monday", 2),
        ("2023, 12, 31", "Day.Sunday", 53),
        ("2000, 12, 31", "Day.Sunday", 54),    # a leap year starting on a Saturday
        ("2021, 1, 3", "Day.Monday", 1),       # ISO 8601 would say 2020's week 53
    ])
    def test_week_of_year_is_week_one_holding_january_first(self, date, first, week):
        assert evaluate(f"Date.WeekOfYear(#date({date}), {first})") == week

    def test_start_and_end_of_week(self):
        assert evaluate("Date.StartOfWeek(#date(2024, 1, 3), Day.Monday)") == D(2024, 1, 1)
        assert evaluate("Date.EndOfWeek(#date(2024, 1, 3), Day.Monday)") == D(2024, 1, 7)
        assert evaluate("Date.EndOfWeek(#date(2024, 1, 3), Day.Sunday)") == D(2024, 1, 6)
        assert evaluate("Date.StartOfWeek(#date(2024, 1, 1), Day.Monday)") == D(2024, 1, 1)
        assert evaluate("Date.EndOfWeek(#date(2024, 1, 7), Day.Monday)") == D(2024, 1, 7)

    def test_a_datetime_keeps_its_kind(self):
        assert evaluate("Date.StartOfWeek(#datetime(2024, 1, 3, 13, 5, 0), Day.Monday)") == dt.datetime(2024, 1, 1)
        assert evaluate("Date.EndOfWeek(#datetime(2024, 1, 3, 13, 5, 0), Day.Monday)") == \
            dt.datetime(2024, 1, 7, 23, 59, 59, 999999)
        tz = dt.timezone(dt.timedelta(hours=10))
        assert evaluate("Date.StartOfWeek(#datetimezone(2024, 1, 3, 13, 5, 0, 10, 0), Day.Monday)") == \
            dt.datetime(2024, 1, 1, tzinfo=tz)

    def test_out_of_range(self):
        assert err("Date.StartOfWeek(#date(1, 1, 1), Day.Tuesday)").message == (
            "The Date operation failed because the resulting value falls outside the range of allowed values.")
        assert err("Date.EndOfWeek(#datetime(9999, 12, 31, 0, 0, 0), Day.Thursday)").message == (
            "The DateTime operation failed because the resulting value falls outside the range of allowed values.")

    def test_a_bad_first_day(self):
        assert err("Date.WeekOfYear(#date(2024, 1, 1), 7)").message == "The Day value provided is invalid."
        assert err("Date.WeekOfYear(#date(2024, 1, 1), 1.5)").message == (
            "The number is out of range of a 32 bit integer value.")


class TestDateToText:
    @pytest.mark.parametrize("fmt, text", [
        ("yyyyMM", "202403"), ("MMMM", "March"), ("MMM", "Mar"), ("dddd", "Tuesday"), ("ddd", "Tue"),
        ("d", "3/5/2024"), ("dd/MM/yy", "05/03/24"), ("D", "Tuesday, March 5, 2024"), ("M", "March 5"),
        ("Y", "March 2024"), ("s", "2024-03-05T00:00:00"), ("yyyy-MM-dd HH:mm tt", "2024-03-05 00:00 AM"),
        ("h:mm:ss.fff", "12:00:00.000"), ("ss.FFF", "00"), ("'Week' d", "Week 5"), ('"MMMM"', "MMMM"),
        ("\\d d", "d 5"), ("%d", "5"), ("y", "March 2024"), ("yyyyy", "02024"),
        ("YYYY-MM-DD", "2024-03-05"), ("YYYYMMDD", "20240305"), ("ddddd", "Tuesday"), ("gg", "A.D."),
    ])
    def test_formats_in_en_us(self, fmt, text):
        m = fmt.replace('"', '""')            # M text: a quote doubled, a backslash literal
        assert evaluate(f'Date.ToText(#date(2024, 3, 5), "{m}")', culture="en-US") == text

    def test_long_time_and_the_general_pattern(self):       # Desktop's answers, #105's NUC probe 2026-11-26
        assert evaluate('Date.ToText(#date(2024, 9, 5), "T", "en-US")') == "12:00:00 AM"
        assert evaluate('Date.ToText(#date(2024, 9, 5), "", "en-US")') == "9/5/2024 12:00:00 AM"
        assert evaluate('Date.ToText(#date(2024, 9, 5), "", "en-AU")') == "5/09/2024 12:00:00 AM"
        assert evaluate('Date.ToText(#date(2024, 9, 5), "T", "")') == "00:00:00"
        assert evaluate('Date.ToText(#date(2024, 9, 5), "", "")') == "09/05/2024 00:00:00"

    def test_the_culture_argument_and_the_default(self):
        assert evaluate('Date.ToText(#date(2024, 3, 5))', culture="en-AU") == "5/03/2024"
        assert evaluate('Date.ToText(#date(2024, 3, 5), null, "en-AU")', culture="en-US") == "5/03/2024"
        assert evaluate('Date.ToText(#date(2024, 3, 5), "D", "en-AU")') == "Tuesday, 5 March 2024"
        assert evaluate('Date.ToText(#date(2024, 3, 5), "MMMM", null)', culture="en-AU") == "March"

    def test_null_date_and_errors(self):
        assert evaluate('Date.ToText(null, "x")') is None
        e = err('Date.ToText(#date(2024, 3, 5), "\'abc")')
        assert (e.reason, e.message) == ("Expression.Error", "The output DateTime format specified isn't supported.")
        assert err('Date.ToText(#date(2024, 3, 5), "Q")').message == \
            "The output DateTime format specified isn't supported."
        assert err("Date.ToText(#datetime(2024, 3, 5, 0, 0, 0))").message == \
            "We cannot convert the value #datetime(2024, 3, 5, 0, 0, 0) to type Date."
        assert err('Date.ToText("2024-03-05")').message == 'We cannot convert the value "2024-03-05" to type Date.'

    def test_refusals_name_what_is_missing(self):
        assert "isn't supported yet" in err('Date.ToText(#date(2024, 3, 5), [Format = "yyyy"])').message
        assert "isn't supported yet" in err('Date.ToText(#date(2024, 3, 5), "t")').message
        assert "isn't supported yet" in err('Date.ToText(#date(2024, 3, 5), "zzz")').message
        assert "isn't supported yet" in err('Date.ToText(#date(2024, 3, 5), "MMMM", "fr-FR")').message


class TestDurationsAndLists:
    def test_total_days(self):
        assert evaluate("Duration.TotalDays(#duration(1, 12, 0, 0))") == 1.5
        assert evaluate("Duration.TotalDays(#date(2024, 3, 1) - #date(2024, 2, 1))") == 29
        assert evaluate("Duration.TotalDays(null)") is None
        assert err("Duration.TotalDays(1)").message == "We cannot convert the value 1 to type Duration."

    def test_list_dates(self):
        assert evaluate("List.Dates(#date(2024, 2, 28), 3, #duration(1, 0, 0, 0))") == [
            D(2024, 2, 28), D(2024, 2, 29), D(2024, 3, 1)]
        assert evaluate("List.Dates(#date(2024, 2, 28), 2.5, #duration(1, 0, 0, 0))") == [
            D(2024, 2, 28), D(2024, 2, 29), D(2024, 3, 1)]          # while i < count
        assert evaluate("List.Dates(#date(2024, 1, 1), 0, #duration(1, 0, 0, 0))") == []
        assert evaluate("List.Dates(#date(2024, 1, 1), 3, #duration(0, 12, 0, 0))") == [
            D(2024, 1, 1), D(2024, 1, 1), D(2024, 1, 2)]             # a time of day is dropped from each date
        assert evaluate("List.Dates(#date(2024, 1, 3), 2, #duration(-1, 0, 0, 0))") == [D(2024, 1, 3), D(2024, 1, 2)]

    def test_list_dates_errors(self):
        assert err("List.Dates(#date(2024, 1, 1), -1, #duration(1, 0, 0, 0))").message == \
            "The 'count' argument is out of range."
        assert err("List.Dates(#date(2024, 1, 1), 2, 1)").message == "We cannot convert the value 1 to type Duration."
        assert err("List.Dates(null, 2, #duration(1, 0, 0, 0))").message == \
            "We cannot convert the value null to type Date."
        assert err("List.Dates(#date(9999, 12, 30), 3, #duration(1, 0, 0, 0))").message == (
            "The Date operation failed because the resulting value falls outside the range of allowed values.")


class TestText:
    def test_text_from(self):
        assert evaluate("Text.From(2024)") == "2024"
        assert evaluate("Text.From(true)") == "true"
        assert evaluate("Text.From(null)") is None
        assert evaluate('Text.From("x")') == "x"
        assert evaluate("Text.From(#date(2024, 3, 5))", culture="en-AU") == "5/03/2024"
        assert evaluate('Text.From(#date(2024, 3, 5), "en-US")', culture="en-AU") == "3/5/2024"
        assert err("Text.From({1})").message == "We cannot convert a value of type List to type Text."

    def test_pad_start(self):
        assert evaluate('Text.PadStart("7", 2, "0")') == "07"
        assert evaluate('Text.PadStart("53", 2, "0")') == "53"
        assert evaluate('Text.PadStart("123", 2, "0")') == "123"
        assert evaluate('Text.PadStart("a", 3)') == "  a"
        assert evaluate('Text.PadStart(null, 3, "0")') is None

    def test_pad_start_errors(self):
        assert err('Text.PadStart("a", 3, "xy")').message == "The value isn't a single-character string."
        assert err('Text.PadStart("abc", 2, "xy")').message == "The value isn't a single-character string."
        assert err('Text.PadStart("a", -1)').message == "The 'count' argument is out of range."
        assert err('Text.PadStart("a", 2.5)').message == "The number is out of range of a 32 bit integer value."
        assert err('Text.PadStart("a", null)').message == "We cannot convert the value null to type Number."
        long = "abcdefghijklmnopqrstuvwxyz"
        assert err(f'Text.PadStart("a", "{long}")').message == \
            'We cannot convert the value "abcdefghijklmnopqrst..." to type Number.'


class TestFromList:
    def test_split_by_nothing_with_a_table_type(self):
        t = evaluate("Table.FromList({#date(2024, 1, 1), #date(2024, 1, 2)}, Splitter.SplitByNothing(), "
                     "type table [Date = date])")
        assert t.columns == ["Date"] and [x.kind for x in t.types] == ["date"]
        assert t.rows == [[D(2024, 1, 1)], [D(2024, 1, 2)]]

    def test_the_type_is_ascribed_not_checked(self):
        t = evaluate('Table.FromList({1, "a", null}, Splitter.SplitByNothing(), type table [Date = date])')
        assert t.rows == [[1], ["a"], [None]]

    def test_columns_null_count_and_names(self):
        t = evaluate("Table.FromList({[a = 1], [a = 2]}, Splitter.SplitByNothing(), null, null, ExtraValues.Error)")
        assert t.columns == ["Column1"] and [x.kind for x in t.types] == ["any"]
        t = evaluate("Table.FromList({1, 2}, each {_, _ * 10}, 3, \"-\")")
        assert t.columns == ["Column1", "Column2", "Column3"] and t.rows == [[1, 10, "-"], [2, 20, "-"]]
        t = evaluate('Table.FromList({1}, each {_, _}, {"a", "b"})')
        assert t.columns == ["a", "b"] and t.rows == [[1, 1]]
        assert evaluate("Table.FromList({}, Splitter.SplitByNothing())").columns == []

    def test_extra_values(self):
        t = evaluate("Table.FromList({1}, each {_, _, _}, 2)")
        cell = t.rows[0][0]
        assert isinstance(cell, CellError)
        assert (cell.error.reason, cell.error.message) == (
            "DataFormat.Error", "There were more columns in the result than expected.")
        t = evaluate("Table.FromList({1}, each {_, _, _}, 2, null, ExtraValues.Ignore)")
        assert t.rows == [[1, 1]]

    def test_bad_columns(self):
        msg = ("The columns parameter must be null, specify the number of columns, specify a list of column names, "
               "or specify a table type.")
        assert err('Table.FromList({1}, Splitter.SplitByNothing(), "a")').message == msg
        assert err("Table.FromList({1}, Splitter.SplitByNothing(), -1)").message == msg
        assert err("Table.FromList({1}, Splitter.SplitByNothing(), {1})").message == msg


class TestInt8:
    def test_int8_is_a_number_type_ascribed_by_add_column(self):
        t = evaluate('Table.AddColumn(#table({"a"}, {{1}}), "b", each 2.5, Int8.Type)')
        assert t.types[1].facet == "Int8.Type" and t.rows == [[1, 2.5]]

    def test_load_conforms_whole_numbers_in_range(self):   # hypothesis (D29), checked on Desktop by the NUC probe
        int8 = MType("number", facet="Int8.Type")
        assert conforms(127, int8) and conforms(-128, int8)
        assert not conforms(2.5, int8) and not conforms(128, int8)


def test_the_date_table_query():
    """ms-artificial-intelligence-sample's Query1 (a public Microsoft sample's date-table function), shortened to
    the steps whose rules differ, over a year's end in en-US (weeks from Sunday) and en-AU (from Monday)."""
    q = """(StartDate as date, EndDate as date, optional Culture as nullable text) as table =>
    let DayCount = Duration.TotalDays(EndDate - StartDate) + 1,
        Source = List.Dates(StartDate, DayCount, #duration(1, 0, 0, 0)),
        T = Table.FromList(Source, Splitter.SplitByNothing(), type table [Date = date]),
        Y = Table.AddColumn(T, "Year", each Date.Year([Date]), Int64.Type),
        W = Table.AddColumn(Y, "YearWeek", each Text.From([Year]) & Text.PadStart(Text.From(Date.WeekOfYear([Date])),
            2, "0"), type text),
        S = Table.AddColumn(W, "WeekStart", each Date.StartOfWeek([Date]), type date),
        N = Table.AddColumn(S, "DayInWeek", each Date.DayOfWeek([Date], Day.Monday) + 1, Int8.Type),
        M = Table.AddColumn(N, "Month", each Date.ToText([Date], "MMM", Culture), type text)
    in M"""
    us = evaluate(f"({q})(#date(2023, 12, 30), #date(2024, 1, 1), null)", culture="en-US")
    assert us.rows == [
        [D(2023, 12, 30), 2023, "202352", D(2023, 12, 24), 6, "Dec"],
        [D(2023, 12, 31), 2023, "202353", D(2023, 12, 31), 7, "Dec"],
        [D(2024, 1, 1), 2024, "202401", D(2023, 12, 31), 1, "Jan"],
    ]
    au = evaluate(f'({q})(#date(2023, 12, 30), #date(2024, 1, 1), "en-AU")', culture="en-AU")
    assert [r[2:4] for r in au.rows] == [["202353", D(2023, 12, 25)], ["202353", D(2023, 12, 25)],
                                         ["202401", D(2024, 1, 1)]]
    assert evaluate(f"({q})(#date(2024, 1, 2), #date(2024, 1, 1))").rows == []
