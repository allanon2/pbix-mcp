"""pbix_mcp.m.shorthand.display_text: a value's text in Power Query's data preview (pivot-studio #361).

MEASURED rows are Power BI Desktop 2.157's preview cells in en-AU, read from its live DOM (pivot-studio #350): hard
values. RULE rows are the display rules the module states (a number's 10 digits, scientific outside 0.0001 to
99,999,999,999, the second's rounding, the column facets), not yet measured on Desktop.
"""
import datetime as dt
from decimal import Decimal

import pytest

from pbix_mcp.m.shorthand import ShorthandUnsupported, display_text, value_text

AEST = dt.timezone(dt.timedelta(hours=10))
IST_ISH = dt.timezone(-dt.timedelta(hours=5, minutes=30))

MEASURED_EN_AU = [
    (1.5, "1.5"), (7, "7"), (-42, "-42"), (7.0, "7"), (-42.0, "-42"),
    (dt.date(2026, 1, 5), "5/01/2026"), (dt.date(2026, 12, 31), "31/12/2026"),
    (dt.datetime(2026, 1, 5, 13, 5, 9), "5/01/2026 1:05:09 PM"), (dt.datetime(2026, 12, 31), "31/12/2026 12:00:00 AM"),
    (dt.time(13, 5, 9), "1:05:09 PM"), (dt.time(0, 0, 0), "12:00:00 AM"),
    (dt.timedelta(days=1, hours=2, minutes=3, seconds=4), "1.02:03:04"), (dt.timedelta(seconds=5), "0.00:00:05"),
    (True, "TRUE"), (False, "FALSE"), (None, "null"),
    (dt.datetime(2026, 1, 5, 13, 5, 9, tzinfo=AEST), "5/01/2026 1:05:09 PM +10:00"),
    (dt.datetime(2026, 12, 31, 23, 59, 59, tzinfo=IST_ISH), "31/12/2026 11:59:59 PM -05:30"),
]


@pytest.mark.parametrize("value,text", MEASURED_EN_AU)
def test_measured_en_au(value, text):
    assert display_text(value, "en-AU") == text


def test_measured_count():
    assert len(MEASURED_EN_AU) == 18


@pytest.mark.parametrize("value,text", [
    (1 / 3, "0.333333333"), (0.1 + 0.2, "0.3"), (2 / 3, "0.666666667"), (123.456789123, "123.4567891"),
    (1234567890.5, "1234567891"), (99999999999.0, "99999999999"), (0.0001, "0.0001"), (-0.25, "-0.25"),
    (0.0, "0"), (-0.0, "0"), (1e-7, "1.0E-07"), (-1e-7, "-1.0E-07"), (123456789012.0, "1.23457E+11"), (1e20, "1.0E+20"),
    (10 ** 20, "1.0E+20"), (0.00009999994, "9.99999E-05"), (0.000099999995, "1.0E-04"), (Decimal("0.1"), "0.1"),
    (float("nan"), "NaN"), (float("inf"), "Infinity"), (float("-inf"), "-Infinity"),
])
def test_number_rule(value, text):
    assert display_text(value, "en-US") == text


def test_differs_from_the_round_trip():
    # the formula bar's shorthand keeps 17 digits; the cell shows 10 (the reason #361's first port was wrong)
    assert value_text(0.1 + 0.2, "en-US") == "0.30000000000000004"
    assert display_text(0.1 + 0.2, "en-US") == "0.3"


@pytest.mark.parametrize("value,facet,culture,text", [
    (0.5, "Percentage.Type", "en-US", "50.00%"), (0.12345, "Percentage.Type", "en-AU", "12.35%"),
    (-0.5, "Percentage.Type", "en-US", "-50.00%"), (12.5, "Percentage.Type", "en-US", "1250.00%"),
    (0.5, "Percentage.Type", "", "50.00 %"),
    (1234.5, "Currency.Type", "en-US", "1,234.50"), (-1234567.125, "Currency.Type", "en-AU", "-1,234,567.13"),
    (1.5, "Int64.Type", "en-US", "1.5"),
])
def test_column_facets(value, facet, culture, text):
    assert display_text(value, culture, facet) == text


@pytest.mark.parametrize("value,text", [
    (dt.time(13, 5, 9, 499_999), "1:05:09 PM"), (dt.time(13, 5, 9, 500_000), "1:05:10 PM"),
    (dt.datetime(2026, 12, 31, 23, 59, 59, 600_000), "1/1/2027 12:00:00 AM"),
    (dt.datetime(2026, 1, 5, 13, 5, 9, 500_000, tzinfo=AEST), "1/5/2026 1:05:10 PM +10:00"),
    (dt.time(23, 59, 59, 900_000), "12:00:00 AM"),
    (dt.timedelta(seconds=-5), "-00:00:05"), (dt.timedelta(seconds=1.5), "0.00:00:01.5000000"),
    ("abc", "abc"), ("5", "5"),
])
def test_time_rule_and_round_trip_kinds(value, text):
    assert display_text(value, "en-US") == text


def test_an_unknown_culture_is_refused():
    with pytest.raises(ShorthandUnsupported):
        display_text(1.5, "xx-ZZ")
