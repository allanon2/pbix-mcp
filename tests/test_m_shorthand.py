"""Power Query's shorthand for constant steps and parameter values (pbix_mcp.m.shorthand; pivot-studio #352).

The expected texts are .NET's own formats (double "R" as .NET Framework writes it: 15 significant digits, else 17;
a short date and long time per culture; TimeSpan's "c"), not this module's output."""
from __future__ import annotations

import datetime as dt

import pytest

from pbix_mcp.m.check import query_steps
from pbix_mcp.m.shorthand import duration_text, number_text, value_text


def bar(expr: str, culture: str = "en-US"):
    step = query_steps(f"let\n    x = {expr}\nin\n    x", culture).steps[0]
    return step.formula if step.formula is not None else ("unsupported", step.formula_unsupported)


@pytest.mark.parametrize("expr, text", [
    ("5", "5"), ("-5", "-5"), ("+5", "5"), ("0.1", "0.1"), ("123456789012345", "123456789012345"),
    ("0.0001", "0.0001"), ("1e-5", "1E-05"), ("2.5e-7", "2.5E-07"), ("#nan", "NaN"),
    ("true", "TRUE"), ("false", "FALSE"), ("null", "null"),
    ('"abc"', "abc"), ('""', ""), ('"localhost"', "localhost"), ('"="', "="),
    ("#date(2020, 1, 1)", "1/1/2020"), ("#date(2020, 12, 31)", "12/31/2020"),
    ("#datetime(2020, 1, 1, 13, 5, 9)", "1/1/2020 1:05:09 PM"), ("#datetime(2020, 1, 1, 0, 0, 0)", "1/1/2020 12:00:00 AM"),
    ("#datetime(2020, 1, 1, 10, 0, 0.5)", "2020-01-01T10:00:00.5000000"),
    ("#time(13, 5, 9)", "1:05:09 PM"), ("#time(10, 0, 0.25)", "10:00:00.2500000"),
    ("#datetimezone(2020, 1, 1, 10, 0, 0, 10, 0)", "1/1/2020 10:00:00 AM +10:00"),
    ("#datetimezone(2020, 1, 1, 10, 0, 0, -5, -30)", "1/1/2020 10:00:00 AM -05:30"),
    ("#duration(0, 0, 5, 0)", "0.00:05:00"), ("#duration(1, 2, 0, 0)", "1.02:00:00"),
    ("#duration(0, 0, 0, 1.5)", "0.00:00:01.5000000"),
])
def test_a_constant_step_shows_its_shorthand(expr, text):
    assert bar(expr) == text


@pytest.mark.parametrize("expr", [
    "(5)", "-(5)", "- -5", "1 + 1", "{1}", "[a = 1]", "type number", "#date", "#date((2020), 1, 1)",
    '#"#date"(2020, 1, 1)', "-#duration(0, 0, 5, 0)", "not true", '-"a"', "#date(2020, 13, 1)",
    "Number.From(5)", "x", '"a" & "b"', "5 meta [a = 1]",
])
def test_anything_else_shows_as_written(expr):
    assert bar(expr) == "= " + expr


@pytest.mark.parametrize("expr, text", [
    ('"5"', "'5"), ('" 5"', "' 5"), ('"1,000"', "'1,000"), ('"5%"', "'5%"), ('"TRUE"', "'TRUE"),
    ('"false"', "'false"), ('"null"', "'null"), ('"=x"', "'=x"), ("\"'q\"", "''q"), ('"1/2/2020"', "'1/2/2020"),
    ('"2020-01-31"', "'2020-01-31"), ('"10:30"', "'10:30"), ('"P1D"', "'P1D"), ('"1.02:00:00"', "'1.02:00:00"),
    ('"NULL"', "NULL"), ('"Paris"', "Paris"), ('"t rue"', "'t rue"),
    # NumberValue.TryParse's NumberStyles.Any: trailing signs, a currency symbol after, the percent search,
    # a trailing group separator, NaN and the infinity symbols
    ('"5-"', "'5-"), ('"5$"', "'5$"), ('"$5"', "'$5"), ('"(5)"', "'(5)"), ('"%5"', "'%5"), ('"5 %"', "'5 %"),
    ('"-%5"', "'-%5"), ('"1,"', "'1,"), ('"NaN"', "'NaN"), ('"\u221e"', "'\u221e"), ('"-\u221e"', "'-\u221e"),
    ('"1e999"', "'1e999"), ('",1"', ",1"), ('"5%%"', "5%%"), ('"(5"', "(5"), ('"5e"', "5e"),
    # M's ISO forms with a zone
    ('"2020-01-01T10:00:00+10:00"', "'2020-01-01T10:00:00+10:00"), ('"2020-01-01T10:00:00Z"', "'2020-01-01T10:00:00Z"),
])
def test_text_that_reads_as_something_else_gets_a_tick(expr, text):
    assert bar(expr) == text


def test_text_with_a_control_character_is_a_formula():
    assert bar('"a#(lf)b"') == '= "a#(lf)b"'
    assert bar('"a#(tab)""b"""') == '= "a#(tab)""b"""'


def test_the_culture_writes_dates_and_times():
    assert bar("#date(2020, 1, 5)", "en-AU") == "5/01/2020"
    assert bar("#datetime(2020, 1, 5, 13, 5, 9)", "en-AU") == "5/01/2020 1:05:09 PM"
    assert bar("#datetime(2020, 1, 5, 13, 5, 9)", "") == "01/05/2020 13:05:09"
    assert bar('"5/01/2020"', "en-AU") == "'5/01/2020"
    assert bar('"Infinity"', "") == "'Infinity"
    assert bar('"\u00a45"', "") == "'\u00a45"


@pytest.mark.parametrize("expr, text", [
    # over 15 significant digits a literal keeps its decimal: .NET's decimal "G", digits and scale as written
    ("0.30000000000000004", "0.30000000000000004"), ("1234567890123456789", "1234567890123456789"),
    ("10000000000000000", "10000000000000000"), ("1.0000000000000001", "1.0000000000000001"),
    ("1234567890.12345670", "1234567890.12345670"), ("-1234567890.12345670", "-1234567890.12345670"),
    ("1e20", "100000000000000000000"),
    # under a decimal's 28 places the decimal is 0: a double
    ("1e-30", "1E-30"), ("0.000000000000000000000000000001", "1E-30"),
    # past a decimal's range: a double
    ("1e30", "1E+30"),
])
def test_a_long_number_literal_writes_its_decimal(expr, text):
    assert bar(expr) == text


def test_a_query_without_steps_shows_its_own_tokens():
    assert query_steps("// note\n1 + 1").formula == "= 1 + 1"
    assert query_steps("  1 + 1  ").formula == "= 1 + 1"
    assert query_steps("1 + 1 // t").formula == "= 1 + 1"
    assert query_steps("/* a */ 5 // b").formula == "5"


@pytest.mark.parametrize("v, text", [
    (0.1 + 0.2, "0.30000000000000004"), (1 / 3, "0.33333333333333331"), (-2.5, "-2.5"), (123456.789, "123456.789"),
    (1e15, "1000000000000000"), (2.0 ** 62, "4611686018427387904"), (1.5e20, "150000000000000000000"),
    (1e300, "1E+300"), (1.7976931348623157e308, "1.7976931348623157E+308"), (5e-324, "4.94065645841247E-324"),
    (1e-5, "1E-05"), (0.0, "0"), (-0.0, "0"), (float("inf"), "∞"), (float("-inf"), "-∞"),
])
def test_number_text_is_dotnets_round_trip(v, text):
    assert number_text(v, "en-US") == text


@pytest.mark.parametrize("td, text", [
    (dt.timedelta(0), "0.00:00:00"), (dt.timedelta(minutes=-5), "-00:05:00"), (dt.timedelta(days=-1, hours=-2), "-1.02:00:00"),
    (dt.timedelta(days=3, microseconds=1), "3.00:00:00.0000010"),
])
def test_a_duration_is_timespans_constant_format(td, text):
    assert duration_text(td) == text


def test_a_datetime_under_a_millisecond_drops_the_fraction():
    assert value_text(dt.datetime(2020, 1, 1, 10, 0, 0, 999), "en-US") == "1/1/2020 10:00:00 AM"


def params(text: str):
    q = query_steps(text)
    if q.parameter_unsupported is not None:
        return ("unsupported", q.parameter_unsupported)
    return q.parameter_value if q.is_parameter else "not a parameter"


@pytest.mark.parametrize("text, value", [
    ('"abc" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]', "abc"),
    ('"5" meta [IsParameterQuery=true, Type="Text"]', "5"),               # no tick for a parameter
    ('5 meta [IsParameterQuery=true, Type="Number"]', "5"),
    ('#date(2020, 1, 1) meta [IsParameterQuery=true, Type="Date"]', "1/1/2020"),
    ('"a#(lf)b" meta [IsParameterQuery=true]', "a\nb"),                  # no formula either
    ('null meta [IsParameterQuery=true]', None),                         # "..."
    ('x meta [IsParameterQuery=true]', None),
    ('(5) meta [IsParameterQuery=true]', None),
    ('Foo meta [IsParameterQuery=true, BinaryIdentifier=Foo, Type="Binary"]', "Foo"),
    ('@Foo meta [IsParameterQuery=true, BinaryIdentifier=Foo, Type="Binary"]', "Foo"),
    ('5 meta [IsParameterQuery=false]', "5"),                            # a file loads it as one: the field is there
    ('let X = 5 meta [IsParameterQuery=true, Type="Number"] in X', "5"),  # single-step, as Power BI allows
    ('let X = 5 meta [IsParameterQuery=true] in Y', "not a parameter"),
    ('let X = 5 meta [IsParameterQuery=true], Y = X in Y', "not a parameter"),
    ('5 meta [a=1]', "not a parameter"), ('5', "not a parameter"),
])
def test_a_parameters_value_as_the_queries_pane_shows_it(text, value):
    assert params(text) == value



def test_a_binary_parameter_value_is_refused_not_dropped():
    assert params('#binary("AQID") meta [IsParameterQuery=true]')[0] == "unsupported"
