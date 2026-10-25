"""Issue #242: text read as a date or date-time answered differently by
Python version.

The engine reads a text that is wholly an ISO date or date-time as its
moment where text meets dates: a text join key against a date key
(_index_spellings, _midnight_date), a filter value on a date column, the
In-set readings the #203 pushdown checks (_inset_aliasing). That reading was
datetime.fromisoformat, whose grammar widened in Python 3.11 to the ISO
basic format ('20240110'), week dates ('2024W021'), 'Z', ',' and fractions
of any length. CI runs 3.10 to 3.13, and so:
- a text key '20240110' related to a date column joined
  datetime(2024, 1, 10) on 3.11 / 3.13 (SUM 1010) and nothing on 3.10
  (SUM 1000);
- a filter value '20240110' selected 2024-01-10 on 3.11+ only, '2024W022'
  selected 2024-01-09;
- '2024-01-10T00:00:00.000Z' (JavaScript's toISOString) and
  '...T00:00:00.0000000' (.NET's round trip) selected their date on 3.11+
  and nothing on 3.10;
- '2024-01-10T12:30:15.5' was its exact time on 3.11+ and MIDNIGHT on 3.10:
  refused by fromisoformat there, it fell to the strict strptime loop, which
  reads 1-6 fraction digits but answers the date alone. A first version of
  this fix took 3.10's rule and made that midnight the answer everywhere;
- test_issue210's "ISO basic and extended" refusal failed on 3.10.

The engine now reads the extended format itself (_iso_moment) as 3.11+
reads it, on every version, and refuses the basic format, week and ordinal
dates and the parsers' accidents (each read by some Python as another moment
than the text names). A strict spelling with a time keeps its time.

Expected values: Python 3.13.1's and 3.11.9's datetime.fromisoformat,
measured on the strings below (offset dropped); the strings 3.10.4 refuses
or reads are marked. A differential of _iso_moment against fromisoformat
over about 10.3 million generated strings (each date, separator, time,
fraction, stray character and offset piece above combined, random strings,
and the fast shapes with characters replaced) gave one digest of answers on
3.10, 3.11 and 3.13; on 3.11 and 3.13 the same value wherever both read
one, nothing read that fromisoformat refuses, and every string it reads and
_iso_moment refuses has one of the refused features.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

D = datetime


# Python 3.11.9 / 3.13.1: datetime.fromisoformat(s).replace(tzinfo=None).
# "3.10 refuses" marks what 3.10.4's fromisoformat raised ValueError for.
EXTENDED = {
    "2024-01-10": D(2024, 1, 10),
    "2024-01-10T12": D(2024, 1, 10, 12),
    "2024-01-10T12:30": D(2024, 1, 10, 12, 30),
    "2024-01-10T12:30:15": D(2024, 1, 10, 12, 30, 15),
    "2024-01-10 12:30": D(2024, 1, 10, 12, 30),
    "2024-01-10x12:30": D(2024, 1, 10, 12, 30),
    "2024-01-10€12:30": D(2024, 1, 10, 12, 30),
    "2024-01-10t12:30": D(2024, 1, 10, 12, 30),
    "2024-01-10T12:30:15.5": D(2024, 1, 10, 12, 30, 15, 500000),            # 3.10 refuses
    "2024-01-10T12:30:15.12": D(2024, 1, 10, 12, 30, 15, 120000),           # 3.10 refuses
    "2024-01-10T12:30:15.123": D(2024, 1, 10, 12, 30, 15, 123000),
    "2024-01-10T12:30:15.1234": D(2024, 1, 10, 12, 30, 15, 123400),         # 3.10 refuses
    "2024-01-10T12:30:15.12345": D(2024, 1, 10, 12, 30, 15, 123450),        # 3.10 refuses
    "2024-01-10T12:30:15.123456": D(2024, 1, 10, 12, 30, 15, 123456),
    "2024-01-10T12:30:15.1234567": D(2024, 1, 10, 12, 30, 15, 123456),      # 3.10 refuses
    "2024-01-10T12:30:15.123456789": D(2024, 1, 10, 12, 30, 15, 123456),    # 3.10 refuses
    "2024-01-10T12:30:15,5": D(2024, 1, 10, 12, 30, 15, 500000),            # 3.10 refuses
    "2024-01-10T12:30:15,123": D(2024, 1, 10, 12, 30, 15, 123000),          # 3.10 refuses
    "2024-01-10 12:30:15.123456": D(2024, 1, 10, 12, 30, 15, 123456),
    "2024-01-10T12:30:15Z": D(2024, 1, 10, 12, 30, 15),                     # 3.10 refuses
    "2024-01-10T12Z": D(2024, 1, 10, 12),                                   # 3.10 refuses
    "2024-01-10T12:30Z": D(2024, 1, 10, 12, 30),                            # 3.10 refuses
    "2024-01-10T00:00:00.000Z": D(2024, 1, 10),                             # 3.10 refuses
    "2024-01-10T00:00:00.0000000Z": D(2024, 1, 10),                         # 3.10 refuses
    "2024-01-10T00:00:00.0000000": D(2024, 1, 10),                          # 3.10 refuses
    "2024-01-10T12:30:15+05:00": D(2024, 1, 10, 12, 30, 15),
    "2024-01-10T12:30:15-05:00": D(2024, 1, 10, 12, 30, 15),
    "2024-01-10T12:30:15+05": D(2024, 1, 10, 12, 30, 15),                   # 3.10 refuses
    "2024-01-10T12:30:15+05:00:30": D(2024, 1, 10, 12, 30, 15),
    "2024-01-10T12:30:15+05:00:30.123456": D(2024, 1, 10, 12, 30, 15),
    "2024-01-10T12:30:15+05:00:30.123": D(2024, 1, 10, 12, 30, 15),         # 3.10 refuses
    "2024-01-10T12:30:15+05:00:30,5": D(2024, 1, 10, 12, 30, 15),           # 3.10 refuses
    "2024-01-10T12:30:15-00:99": D(2024, 1, 10, 12, 30, 15),
    "2024-01-10T12:30:15+23:59:59.999999": D(2024, 1, 10, 12, 30, 15),
    "2024-01-10T12:30:15-23:59:59.9999999": D(2024, 1, 10, 12, 30, 15),     # 3.10 refuses
    "2024-01-10T12+05:00": D(2024, 1, 10, 12),
    "2024-01-10T12:30:15.123+05:00": D(2024, 1, 10, 12, 30, 15, 123000),
    "2024-01-10T12:30:15.5+05:00": D(2024, 1, 10, 12, 30, 15, 500000),      # 3.10 refuses
    "0001-01-01": D(1, 1, 1),
    "9999-12-31": D(9999, 12, 31),
    "2024-02-29": D(2024, 2, 29),
}

# No moment on any version. In the comments, what 3.11.9 / 3.13.1 read
# (offset dropped), and 3.10.4 where it read the string too.
REFUSED = [
    # the basic format, week and ordinal dates (#242)
    "20240110",                            # 2024-01-10
    "2024W021",                            # 2024-01-08
    "2024-W02-1",                          # 2024-01-08
    "2024-W02",                            # 2024-01-08
    "2024-010",
    "2024-01-10T1230",                     # 12:30
    "2024-01-10T123015",                   # 12:30:15
    "2024-01-10T12:30:15+0500",            # 12:30:15
    "2024-01-10T12:30:15+053000",          # 12:30:15
    "2024-01-10T12:30:15.123+0530",        # 12:30:15.123
    # a fraction of the hour or minute, which 3.11+'s documentation says is
    # not supported, read as a fraction of a second ('T12.5' is 12:30)
    "2024-01-10T12.5",                     # 12:00:00.5
    "2024-01-10T12.500",                   # 12:00:00.5, 3.10 too
    "2024-01-10T12:30.5",                  # 12:30:00.5
    "2024-01-10T12:30.500",                # 12:30:00.5, 3.10 too
    "2024-01-10T12,5",                     # 12:00:00.5
    "2024-01-10T12:30:15+05:30.5",         # 12:30:15
    # a fraction after ':'
    "2024-01-10T12:30:15:123",             # 12:30:15.123, 3.10 too
    "2024-01-10T12:30:15:5",               # 12:30:15.5
    "2024-01-10T12:30:15+05:00:00:000000",  # 12:30:15, 3.10 too
    "2024-01-10T12:30:15:1234567",         # 12:30:15.123456
    # a stray character before the offset
    "2024-01-10T12:30:15x+05:00",          # 12:30:15, 3.10 too
    "2024-01-10T12:30:15 Z",               # 12:30:15
    "2024-01-10T00:123Z",                  # 00:12
    "2024-01-10T12:+05:00",                # 12:00, 3.10 too
    "2024-01-10T123+05:00",                # 12:00, 3.10 too
    "2024-01-10T12:30:15Z+05:00",          # 3.10: 12:30:15
    # a NUL, after which the C parser reads nothing
    "2024-01-10T12:30\x00",                # 12:30, 3.10 too
    "2024-01-10T12\x00",                   # 12:00, 3.10 too
    "2024-01-10T12:30:15Z\x00",            # 12:30:15
    "2024-01-10T12:30:15+05:00\x00",       # 12:30:15
    "2024-01-10T00.123456\x00z",           # 00:00:00.123456
    # refused by every version anyway
    "2024-01-10T", "2024-01-10 ", "2024-01-10T12:", "2024-1-10", "0000-01-01", "2024-02-30", "2024-13-01",
    "2024-01-10T24:00", "2024-01-10T12:60", "2024-01-10T12:30:60", "2024-01-10T12:30:15+24:00",
    "2024-01-10T12:30:15-23:99", "2024-01-10T12:30:15+23:59:60", "+2024-01-10", "２024-01-10",
    "2024-01-10T２12:00", "2024-01-10Z", "2024-01-10T12:30:15z", "2024-01-10T12:30:15.",
    "2024-01-10T12:30:15ZZ", "2024-01-10T12:30:15+05:00Z", "2024-01-10T12:30:15+5:00", "2024-01-10T12:30:15+05:",
]


def _strict_moment(s):
    """The strict spellings (_as_date_str_strict's strptime formats), with
    the time a date-time spelling has."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@pytest.mark.parametrize("s", list(EXTENDED))
def test_the_extended_format_reads_as_on_python_3_11(s):
    assert de._as_moment_str(s) == EXTENDED[s]
    assert de._iso_moment(s) == de._iso_moment_parse(s) == EXTENDED[s]


@pytest.mark.parametrize("s", REFUSED)
def test_basic_format_and_parser_accidents_are_no_iso_moment(s):
    # what is left is the strict spellings' ('2024-1-10' is one)
    assert de._as_moment_str(s) == (_strict_moment(s) if s[:1].isdigit() else None)
    assert de._iso_moment(s) is None


def test_the_shortcut_answers_as_the_grammar():
    # the shapes _iso_moment hands to fromisoformat, and their near misses
    shapes = ["2024-01-10", "2024-01-10T12:30:15", "2024-01-10 12:30:15.123", "2024-01-10x12:30:15.123456",
              "2024-01-10T12:30:15.1234567", "2024-01-10T12:30:15Z", "2024-01-10T12:30:15.123Z",
              "2024-01-10T12:30:15.1234567Z", "2024-01-10Z", "2023-02-29", "2023-02-29T12:30:15",
              "2024-01-10T24:00:00", "2024-01-10T23:60:00", "2024-01-10T23:59:60", "2024-01-10\ud80012:30:15",
              "2024-01-10T12:30:15.12٣", "2024-01-10T12:30:15.123456²", "2024-01-10T12:30:15.1234٣Z",
              "2024-01-10T1x:30:15", "2024-01-10T12:30:15.12x", "0000-01-01T00:00:00", "9999-12-31T23:59:59.999999",
              "2024-01-10T12:30:15+01:00", "2024-01-10T12:30:15.123-05:30", "2024-01-10T12:30:15.123456+23:59",
              "2024-01-10T12:30:15+24:00", "2024-01-10T12:30:15+00:99", "2024-01-10T12:30:15.1234567+01:00",
              "2024-01-10T12:30:15+0x:00", "2024-01-10+01:00", "2024-01-10T12:30:15.12Z", "2024-01-10T12:30:15.1234Z"]
    for s in shapes:
        assert de._iso_moment(s) == de._iso_moment_parse(s), ascii(s)
    assert de._iso_moment("2024-01-10\ud80012:30:15") == D(2024, 1, 10, 12, 30, 15)   # 3.10's fromisoformat refuses


def test_a_fraction_of_any_length_is_the_exact_time():
    # T[When] holds midnight (N=1) and 12:30:15.5 (N=2). 0.9.146 on 3.10 and
    # the first version of this fix on every Python selected the midnight row.
    tables = {"T": {"columns": ["When", "N"],
                    "rows": [[D(2024, 1, 10), 1], [D(2024, 1, 10, 12, 30, 15, 500000), 2]]}}
    for v in ("2024-01-10T12:30:15.5", "2024-01-10T12:30:15.50", "2024-01-10T12:30:15.500",
              "2024-01-10T12:30:15.5000", "2024-01-10T12:30:15.500Z", "2024-01-10T12:30:15,5"):
        assert de.evaluate_measures_batch(["S"], tables, {"S": "SUM(T[N])"}, {"T.When": [v]})["S"] == 2, v
    # a text key with that time joins no midnight row (it joined 10)
    d = {"columns": ["K", "Cat"], "rows": [["2024-01-10T12:30:15.5", "a"]]}
    f = {"columns": ["K", "Amt"], "rows": [[D(2024, 1, 10), 10], [D(2024, 1, 10, 12, 30, 15, 500000), 100]]}
    rels = [{"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "K", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    got = de.evaluate_measures_batch(["T"], {"D": d, "F": f}, {"T": "SUM(F[Amt])"}, {"D.Cat": ["a"]},
                                     None, None, rels)["T"]
    assert got is None


def test_a_strict_spelling_with_a_time_keeps_its_time():
    assert de._as_moment_str("2024-01-10T1:02:03") == D(2024, 1, 10, 1, 2, 3)
    assert de._as_moment_str("2024-1-10 12:30:00.5") == D(2024, 1, 10, 12, 30, 0, 500000)
    assert de._as_moment_str("2024-01-10T12:30:15.5") == D(2024, 1, 10, 12, 30, 15, 500000)
    assert de._as_moment_str("2024-1-10") == D(2024, 1, 10)
    assert de._as_moment_str("10/01/2024") == D(2024, 1, 10)
    # a join alias to the bare date only at midnight (#126)
    assert de._midnight_date("2024-01-10T1:02:03") is None
    assert de._midnight_date("2024-01-10 0:00:00") == date(2024, 1, 10)
    assert "2024-01-10" not in de._index_spellings("2024-01-10T1:02:03")


def test_the_basic_format_is_no_moment_and_no_join_spelling():
    assert de._as_moment_str("20240110") is None
    assert de._as_moment_str("2024W021") is None
    assert "2024-01-10" not in de._index_spellings("20240110")
    assert "2024-01-10" in de._index_spellings("2024-01-10T00:00:00")
    assert "2024-01-10" in de._index_spellings("2024-01-10T00:00:00.000Z")


def test_a_text_key_in_the_basic_format_joins_no_date():
    # D[Key] is text, F[K] dates; only F's own text key '20240110' is 'a'
    d = {"columns": ["Key", "Cat"], "rows": [["20240110", "a"], ["20240111", "b"]]}
    f = {"columns": ["K", "Amt"], "rows": [[D(2024, 1, 10), 10], [D(2024, 1, 11), 100], ["20240110", 1000]]}
    rels = [{"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    got = de.evaluate_measures_batch(["T"], {"D": d, "F": f}, {"T": "SUM(F[Amt])"}, {"D.Cat": ["a"]},
                                     None, None, rels)["T"]
    assert got == 1000


@pytest.mark.parametrize("value,want", [
    ("2024-01-10", 2), ("2024-01-10T00:00:00", 2), ("2024-01-10T00:00:00.000", 2),
    ("2024-01-10 00:00:00+01:00", 2), ("2024-01-10T00:00:00Z", 2), ("2024-01-10T00:00:00.000Z", 2),
    ("2024-01-10T00:00:00.0000000", 2), ("2024-01-10T00:00:00.0000000Z", 2), ("2024-01-10T00:00:00.5", None),
    ("20240110", None), ("2024W022", None), ("2024-01-10T00.5", None),
])
def test_a_filter_value_on_a_date_column(value, want):
    tables = {"D": {"columns": ["Date", "N"],
                    "rows": [[D(2024, 1, 9), 1], [D(2024, 1, 10), 2], [D(2024, 1, 11), 3]]}}
    assert de.evaluate_measures_batch(["S"], tables, {"S": "SUM(D[N])"}, {"D.Date": [value]})["S"] == want


def test_the_in_set_reads_the_basic_format_as_text():
    # "20200101" and "2020-01-01" are two texts and two In-set values
    assert de._inset_aliasing([["20200101"], ["2020-01-01"], ["x"]], 0) == (False, False)
    assert de._inset_aliasing([["2020-01-01T00:00:00"], ["2020-01-01"]], 0)[0] is True
    assert de._inset_aliasing([["2020-01-01T00:00:00Z"], ["2020-01-01"]], 0)[0] is True
