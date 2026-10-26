"""Issue #251: calc_tables' date reading and the encoder's DateTime parse
still depended on the Python version.

0.9.147 (#242) made the engine read ISO date-time text the same way on every
Python (engine._iso_moment). Two readers of user text kept their own
fromisoformat call, whose grammar widened in Python 3.11 to the basic format
('20240110'), week dates ('2024W021'), 'Z', ',' and fractions of any length:

- calc_tables._to_date (the generated calendar's range over a fact table's
  date columns) read date.fromisoformat on the text's first ten characters
  before any 'T': a text date column holding '20230105' and '2024-06-01'
  gave a calendar from 2023 on 3.11+ and from 2024 on 3.10;
- the encoder's DateTime parse (a text value in a DateTime column of a built
  model) read datetime.fromisoformat on the whole text: '2024-01-10T12:30:15.5'
  and '...T00:00:00.0000000' (.NET's round trip) built on 3.11+ and failed on
  3.10, '20240110' and '2024W021' built on 3.11+ only, and
  '...T00:00:00.000Z' (JavaScript's toISOString) failed on 3.10 with
  ValueError and on 3.11+ with TypeError (an aware datetime minus the naive
  epoch), as every text with an offset did everywhere.

Both now read the text as the engine does (#242's rule): the extended format,
as Python 3.11+ reads it, on every version, offset dropped and wall-clock time
kept; the basic format, week and ordinal dates and the parsers' accidents
refused. _to_date keeps its prefix reading, so each spelling Python 3.10 read
reads as before. The encoder now also refuses the accidents 3.10 and 3.11+
both read as another moment than written ('T12.500' as 12:00:00.5, a fraction
after ':', what follows a NUL dropped), as the engine refuses them.

Expected values: #242's (Python 3.11.9 / 3.13.1 datetime.fromisoformat on the
extended format, offset dropped). A differential over 937,019 generated
strings (dates, separators, times, fractions, offsets and stray characters
combined, random strings): one digest of both readers' answers on 3.10.4,
3.11.9 and 3.13.1 (0.9.147: one per version); _to_date answers as 0.9.147 on
3.10 for every string; the encoder stores 0.9.147-on-3.10's value wherever
that read one, except 226 strings with a refused feature (a NUL 105, a
fraction of the hour or minute 96, a fraction after ':' 25). The rest of the
engine's text-to-date readers are strptime with fixed formats: over 351,387
generated strings one digest on 3.10.4 and 3.11.9; 3.13.1 differed only
where %I meets a leading space (its %I reads ' 9'), which TIMEVALUE strips
before it parses.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from pbix_mcp.dax import calc_tables as ct
from pbix_mcp.dax import engine as de
from pbix_mcp.formats import vertipaq_encoder as ve

pytestmark = pytest.mark.unit

D = datetime
EPOCH = D(1899, 12, 30)


def serial(d: datetime) -> float:
    return (d - EPOCH).total_seconds() / 86400.0


# The extended format as Python 3.11+ reads it (offset dropped). "3.10" marks
# what 3.10.4's fromisoformat refused; "aware" what both read with an offset,
# which the encoder could not subtract from its naive epoch (TypeError).
EXTENDED = {
    "2024-01-10": D(2024, 1, 10),
    "2024-01-10T12": D(2024, 1, 10, 12),
    "2024-01-10T12:30": D(2024, 1, 10, 12, 30),
    "2024-01-10 12:30:15": D(2024, 1, 10, 12, 30, 15),
    "2024-01-10x12:30": D(2024, 1, 10, 12, 30),
    "2024-01-10T12:30:15.123": D(2024, 1, 10, 12, 30, 15, 123000),
    "2024-01-10T12:30:15.123456": D(2024, 1, 10, 12, 30, 15, 123456),
    "2024-01-10T12:30:15.5": D(2024, 1, 10, 12, 30, 15, 500000),                 # 3.10
    "2024-01-10T12:30:15.1234": D(2024, 1, 10, 12, 30, 15, 123400),              # 3.10
    "2024-01-10T12:30:15.1234567": D(2024, 1, 10, 12, 30, 15, 123456),           # 3.10
    "2024-01-10T12:30:15.123456789": D(2024, 1, 10, 12, 30, 15, 123456),         # 3.10 (numpy's str)
    "2024-01-10T12:30:15,5": D(2024, 1, 10, 12, 30, 15, 500000),                 # 3.10
    "2024-01-10T00:00:00.0000000": D(2024, 1, 10),                               # 3.10 (.NET)
    "2024-01-10T12:30:15Z": D(2024, 1, 10, 12, 30, 15),                          # 3.10; aware on 3.11+
    "2024-01-10T00:00:00.000Z": D(2024, 1, 10),                                  # 3.10; aware on 3.11+
    "2024-01-10T12:30:15+05:00": D(2024, 1, 10, 12, 30, 15),                     # aware
    "2024-01-10T12:30:15.123-05:30": D(2024, 1, 10, 12, 30, 15, 123000),         # aware
    "2024-01-10T12:30:15+05": D(2024, 1, 10, 12, 30, 15),                        # 3.10; aware on 3.11+
    "2024-01-10T12:30:15.5+05:00": D(2024, 1, 10, 12, 30, 15, 500000),           # 3.10; aware on 3.11+
    "0001-01-01": D(1, 1, 1),
    "9999-12-31T23:59:59.999999": D(9999, 12, 31, 23, 59, 59, 999999),
    "2024-02-29": D(2024, 2, 29),
}

# No moment on any version; in the comments what 3.11+ read, and 3.10 where
# it read the text too.
REFUSED = [
    "20240110",                     # 2024-01-10
    "2024W021",                     # 2024-01-08
    "2024-W02-1",                   # 2024-01-08
    "2024-W02",                     # 2024-01-08
    "2024W02",                      # 2024-01-08
    "20240110T1230",                # 2024-01-10 12:30
    "2024-01-10T1230",              # 12:30
    "2024-01-10T12:30:15+0500",     # 12:30:15
    "2024-01-10T12.500",            # 12:00:00.5, 3.10 too
    "2024-01-10T12:30:15:123",      # 12:30:15.123, 3.10 too
    "2024-01-10T12:30:15x+05:00",   # 12:30:15, 3.10 too
    "2024-01-10T12:30\x00",         # 12:30, 3.10 too
    # refused by every version anyway
    "2024-1-10", " 2024-01-10", "2024-01-10 ", "２０２４-01-10", "2024-02-30", "0000-01-01", "2024-13-01",
    "2024-01-10T24:00", "2024-01-10T12:30:15+24:00", "2024-01-10Z", "12:30:00", "", "x",
]


# ---------------------------------------------------------------- _to_date

@pytest.mark.parametrize("s", list(EXTENDED))
def test_to_date_reads_the_extended_format(s):
    assert ct._to_date(s) == EXTENDED[s].date()


@pytest.mark.parametrize("s", [
    "20240110", "2024W021", "2024-W02-1", "2024-W02", "2024W02", "20240110T1230", "2024W021T12:00",
    "2024-1-10", " 2024-01-10", "２０２４-01-10", "2024-02-30", "0000-01-01", "2024-13-01", "2024-01-1",
    "", "x", "T2024-01-10",
])
def test_to_date_refuses_the_basic_format_and_week_dates(s):
    # 0.9.147 on 3.11+: '20240110' and '20240110T1230' 2024-01-10, the week
    # dates 2024-01-08
    assert ct._to_date(s) is None


@pytest.mark.parametrize("s", [
    "2024-01-10T25:99", "2024-01-10 junk", "2024-01-10x", "2024-01-10T1230", "2024-01-10T12.500",
    "2024-01-10T12:30:15+0500", "2024-01-10Z", "2024-01-10 ", "2024-01-10T",
])
def test_to_date_keeps_reading_a_date_prefix(s):
    # what 3.10 read: the extended date in the first ten characters before a 'T'
    assert ct._to_date(s) == date(2024, 1, 10)


def test_to_date_of_other_values_is_unchanged():
    assert ct._to_date(None) is None
    assert ct._to_date(20240110) == date(2024, 1, 10)         # a number YYYYMMDD
    assert ct._to_date(20240110.0) == date(2024, 1, 10)
    assert ct._to_date(2024) is None
    assert ct._to_date(D(2024, 1, 10, 12, 30)) == date(2024, 1, 10)
    assert ct._to_date(date(2024, 1, 10)) == date(2024, 1, 10)


def test_the_generated_calendar_spans_the_readable_dates():
    # 0.9.147 on 3.11+: 2023-01-01 to 2025-12-31 ('20230105' and the week
    # date '2025W011' read); on 3.10: 2024
    tables = {"FactSales": {"columns": ["OrderDate", "Amt"],
                            "rows": [["20230105", 1], ["2024-06-01", 2], ["2025W011", 3],
                                     ["2024-02-29T23:59:59.5Z", 4]]}}
    cal = ct._generate_calendar(tables, {})
    dates = [r[0] for r in cal["rows"]]
    assert (dates[0], dates[-1], len(dates)) == ("2024-01-01T00:00:00", "2024-12-31T00:00:00", 366)


# ------------------------------------------------- the encoder's DateTime

@pytest.mark.parametrize("s", list(EXTENDED))
def test_encoder_reads_the_extended_format(s):
    assert ve._convert_value_for_dict(s, "DateTime") == serial(EXTENDED[s])


@pytest.mark.parametrize("s", REFUSED)
def test_encoder_refuses_the_basic_format_and_parser_accidents(s):
    with pytest.raises(ValueError):
        ve._convert_value_for_dict(s, "DateTime")


def test_encoder_reads_text_as_the_engine_does():
    for s in list(EXTENDED) + REFUSED:
        m = de._iso_moment(s)
        if m is None:
            with pytest.raises(ValueError):
                ve._convert_value_for_dict(s, "DateTime")
        else:
            assert ve._convert_value_for_dict(s, "DateTime") == serial(m), ascii(s)


def test_encoder_of_other_values_is_unchanged():
    assert ve._convert_value_for_dict(None, "DateTime") is None
    assert ve._convert_value_for_dict(D(2024, 1, 10, 12), "DateTime") == serial(D(2024, 1, 10, 12))
    assert ve._convert_value_for_dict(date(2024, 1, 10), "DateTime") == serial(D(2024, 1, 10))
    assert ve._convert_value_for_dict(45301, "DateTime") == 45301.0
    assert ve._convert_value_for_dict(45301.25, "DateTime") == 45301.25


def test_a_built_model_stores_text_date_times_on_every_version(tmp_path):
    # 0.9.147: ValueError on 3.10 ('.5', '.0000000', 'Z'), TypeError on 3.11+
    # ('Z', '+05:00')
    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    texts = ["2024-01-10T12:30:15.5", "2024-01-10T00:00:00.0000000", "2024-01-11T00:00:00.000Z",
             "2024-01-12T08:00:00+05:00", "2024-01-13"]
    b = PBIXBuilder("b251")
    b.add_table("T", [{"name": "N", "data_type": "Int64"}, {"name": "When", "data_type": "DateTime"}],
                rows=[{"N": i, "When": s} for i, s in enumerate(texts)])
    b.add_measure("T", "Rows", "COUNTROWS(T)")
    p = str(tmp_path / "b251.pbix")
    b.save(p)
    alias = "b251_" + tmp_path.name[-6:]
    assert json.loads(S.pbix_open(p, alias))["success"]
    try:
        t = S._get_dax_context(alias)["tables"]["T"]
        ni, wi = t["columns"].index("N"), t["columns"].index("When")
        got = {r[ni]: r[wi] for r in t["rows"]}
    finally:
        S.pbix_close(alias)
    assert got == {0: D(2024, 1, 10, 12, 30, 15, 500000), 1: D(2024, 1, 10), 2: D(2024, 1, 11),
                   3: D(2024, 1, 12, 8), 4: D(2024, 1, 13)}


@pytest.mark.parametrize("s", ["20240110", "2024W021"])
def test_a_built_model_refuses_the_basic_format_on_every_version(tmp_path, s):
    # 0.9.147 built these on 3.11+ (2024-01-10, 2024-01-08) and failed on 3.10
    from pbix_mcp.builder import PBIXBuilder

    b = PBIXBuilder("b251r")
    b.add_table("T", [{"name": "When", "data_type": "DateTime"}], rows=[{"When": s}])
    with pytest.raises(ValueError, match="isoformat"):
        b.save(str(tmp_path / "b251r.pbix"))


# ------------------------------------------- the engine's strptime readers

def test_timevalue_reads_a_space_padded_hour_on_every_version():
    # Python 3.13's strptime reads %I as ' 9', 3.10 and 3.11 do not; TIMEVALUE
    # strips the text first, so the answer is the same on each
    tables = {"T": {"columns": ["N"], "rows": [[1]]}}
    got = de.evaluate_measures_batch(["a", "b", "c"], tables,
                                     {"a": 'TIMEVALUE(" 9:00 AM")', "b": 'TIMEVALUE("9:00 AM")',
                                      "c": 'TIMEVALUE(" 9:00:05 PM")'}, {})
    assert got["a"] == got["b"] == D(1899, 12, 30, 9)
    assert got["c"] == D(1899, 12, 30, 21, 0, 5)
