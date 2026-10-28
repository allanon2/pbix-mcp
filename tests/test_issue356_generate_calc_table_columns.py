"""#356 (reported with PR #352 by @allanon2): a calculated table written
GENERATE(...) has the outer table's columns, then the inner table's, under
their own names.

What was wrong (0.9.149): a GENERATE row is the outer row with the inner
row's keys prefixed ``_inner_`` (one level deeper per nested GENERATE), and
the calculated table took those keys as its columns: GENERATE(CALENDAR(..),
ROW("Y", ..)) came out as _inner___table__, _inner_Y, _inner___column__,
_inner___value__ and lost Date; GENERATE(T, U) with a column both tables
have was accepted; GENERATEALL's unmatched rows made the table refuse with
"result rows have inconsistent columns".

Expected values:
- Microsoft's GENERATE: "A table with the Cartesian product between each row
  in table1 and the table that results from evaluating table2 in the context
  of the current row from table1"; "All column names from table1 and table2
  must be different or an error is returned." GENERATEALL keeps a table1 row
  whose table2 is empty, table2's columns BLANK (#300).
- The Artificial Intelligence sample (Desktop-saved): Case Calendar and
  Opportunity Calendar are GENERATE(CALENDAR(..), VAR .. RETURN ROW(..)).
  Desktop stores Date first, then the ROW's columns, and the values below
  (Case Calendar, 731 rows from 2020-01-01: DaySeq 1, YEAR MONTH NUMBER
  24240, YEAR WEEK "2020-W01", WEEK 2019-12-28, Wednesday / 4; 2021-12-31:
  DaySeq 731, "2021-W53", WEEK 2021-12-25, Friday / 6). With the sample
  present, both tables are recomputed and compared with every stored column
  that does not read TODAY() (1,827 rows, 19,732 cells).
- Power BI Desktop 2.152 refuses a calculated table with a repeated column
  (#151, CROSSJOIN). In a measure it keeps both (build_b358.py g_shared_rows
  3, g_shared_csv heads 'T'[Region] and 'U'[Region]; TOCSV is #359), and
  g_cal_csv / g_nested_csv / g_all_csv head [Date],[Y] / [Date],[A],[B] /
  'T'[Region],'T'[Amount],[X]: the column order kept here.
- GENERATE's rows inside the engine (#300, #302, #303) are unchanged.
"""
from __future__ import annotations

import os
from datetime import date, datetime

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.dax.calc_tables import evaluate_calc_table_expression as build_table

pytestmark = pytest.mark.unit

# shops and the goal set for some of their cities
SHOPS = {"Shop": {"columns": ["City", "Units"], "rows": [["Oslo", 4], ["Bergen", 7], ["Molde", 9]]},
         "Goal": {"columns": ["City", "Target"], "rows": [["Bergen", 50], ["Oslo", 30]]}}


def test_calendar_then_the_rows_columns():
    table, why = build_table('GENERATE(CALENDAR(DATE(2023, 12, 31), DATE(2024, 1, 1)), ROW("Yr", YEAR([Date])))', {})
    assert why is None, why
    assert table == {"columns": ["Date", "Yr"],
                   "rows": [[datetime(2023, 12, 31), 2023], [datetime(2024, 1, 1), 2024]]}


def test_a_model_table_then_the_rows_columns():
    got, err = build_table('GENERATE(Shop, ROW("Dozen", [Units] * 12, "Label", "@" & [City]))', SHOPS)
    assert err is None, err
    assert got == {"columns": ["City", "Units", "Dozen", "Label"],
                   "rows": [["Oslo", 4, 48, "@Oslo"], ["Bergen", 7, 84, "@Bergen"], ["Molde", 9, 108, "@Molde"]]}


def test_an_inner_table_of_several_rows():
    tables = {"A": {"columns": ["a"], "rows": [[1], [2]]}, "B": {"columns": ["b"], "rows": [[10], [20], [30]]}}
    got, err = build_table("GENERATE(A, FILTER(B, B[b] > A[a] * 10))", tables)
    assert err is None, err
    assert got == {"columns": ["a", "b"], "rows": [[1, 20], [1, 30], [2, 30]]}


def test_nested_generate():
    one_day = "CALENDAR(DATE(2024, 6, 1), DATE(2024, 6, 1))"
    got, err = build_table(f'GENERATE(GENERATE({one_day}, ROW("P", 3)), ROW("Q", [P] * 5))', {})
    assert err is None, err
    assert got == {"columns": ["Date", "P", "Q"], "rows": [[datetime(2024, 6, 1), 3, 15]]}
    got, err = build_table(f'GENERATE({one_day}, GENERATE(ROW("P", 3), ROW("Q", 4)))', {})
    assert err is None, err
    assert got == {"columns": ["Date", "P", "Q"], "rows": [[datetime(2024, 6, 1), 3, 4]]}


def test_generate_refuses_a_repeated_name():
    got, err = build_table("GENERATE(Shop, Goal)", SHOPS)
    assert got is None and err == "a column named 'City' comes from two tables"
    # names ignore case
    got, err = build_table('GENERATE(Shop, SELECTCOLUMNS(Goal, "UNITS", Goal[Target]))', SHOPS)
    assert got is None and "'UNITS'" in err


def test_generateall_keeps_unmatched_rows_with_blank_inner_columns():
    got, err = build_table('GENERATEALL(Shop, SELECTCOLUMNS(FILTER(Goal, Goal[City] = Shop[City]), '
                          '"Target", Goal[Target]))', SHOPS)
    assert err is None, err
    assert got == {"columns": ["City", "Units", "Target"],
                   "rows": [["Oslo", 4, 30], ["Bergen", 7, 50], ["Molde", 9, None]]}


def test_generate_over_values_and_a_selectcolumns_inner_table():
    got, err = build_table('GENERATE(VALUES(Shop[City]), SELECTCOLUMNS(FILTER(Goal, Goal[City] = Shop[City]), '
                          '"t", Goal[Target] / 10))', SHOPS)
    assert err is None, err
    assert got == {"columns": ["City", "t"], "rows": [["Oslo", 3], ["Bergen", 5]]}


# GENERATE inside the engine reads as before


def _scalar(expr, tables=SHOPS):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(tables, {}, None, None, None, []))


@pytest.mark.parametrize("dax, expected", [
    ('SUMX(GENERATE(Shop, ROW("k", 1)), Shop[Units])', 20),
    ('CONCATENATEX(GENERATE(Shop, ROW("d", [Units] + 1)), Shop[City] & [d], ";", Shop[Units], ASC)',
     "Oslo5;Bergen8;Molde10"),
    ('COUNTROWS(FILTER(GENERATE(Shop, ROW("d", [Units] + 1)), Shop[Units] > 5))', 2),
    ("COUNTROWS(GENERATE(Shop, Goal))", 6),
    ('COUNTROWS(GENERATE(CALENDAR(DATE(2024, 3, 1), DATE(2024, 3, 4)), ROW("m", MONTH([Date]))))', 4),
    ('COUNTROWS(FILTER(GENERATEALL(Shop, SELECTCOLUMNS(FILTER(Goal, Goal[City] = Shop[City]), "Target", '
     'Goal[Target])), ISBLANK([Target])))', 1),
])
def test_generate_rows_in_the_engine(dax, expected):
    assert _scalar(dax) == expected


# the Artificial Intelligence sample's calendar shape


SHAPE = '''
VAR First = DATE ( YEAR ( MIN ( Cases[Opened] ) ), 1, 1 )
VAR Last = DATE ( YEAR ( MAX ( Cases[Opened] ) ), 12, 31 )
RETURN
    GENERATE (
        CALENDAR ( First, Last ),
        VAR Seq = DATEDIFF ( First, [DATE], DAY ) + 1
        VAR Here = [DATE]
        VAR Yr = YEAR ( Here )
        VAR Mo = MONTH ( Here )
        VAR WeekStart = [Date] - ( 1 * WEEKDAY ( [Date] ) )
        RETURN ROW (
            "DAY", Here, "DaySeq", Seq, "YEAR", Yr, "MONTH NUMBER", Mo,
            "MONTH", FORMAT ( Here, "MMMM" ), "YEAR MONTH NUMBER", Yr * 12 + ( Mo - 1 ),
            "YEAR MONTH", FORMAT ( Here, "MMM YYYY" ),
            "YEAR WEEK", Yr & "-W" & RIGHT ( "0" & WEEKNUM ( Here ), 2 ),
            "WEEK", WeekStart, "Weekday", FORMAT ( Here, "DDDD" ), "WeekdaySeq", WEEKDAY ( Here, 1 )
        )
    )
'''


def test_the_samples_case_calendar_shape():
    cases = {"Cases": {"columns": ["Opened"], "rows": [[datetime(2020, 11, 30)], [datetime(2021, 5, 25)]]}}
    got, err = build_table(SHAPE, cases)
    assert err is None, err
    assert got["columns"] == ["Date", "DAY", "DaySeq", "YEAR", "MONTH NUMBER", "MONTH", "YEAR MONTH NUMBER",
                              "YEAR MONTH", "YEAR WEEK", "WEEK", "Weekday", "WeekdaySeq"]
    assert len(got["rows"]) == 731
    d1, d2 = datetime(2020, 1, 1), datetime(2021, 12, 31)
    assert got["rows"][0] == [d1, d1, 1, 2020, 1, "January", 24240, "Jan 2020", "2020-W01",
                              datetime(2019, 12, 28), "Wednesday", 4]
    assert got["rows"][-1] == [d2, d2, 731, 2021, 12, "December", 24263, "Dec 2021", "2021-W53",
                               datetime(2021, 12, 25), "Friday", 6]


_SAMPLE = os.path.join(os.path.dirname(__file__), "..", "test_samples", "templates",
                       "Artificial Intelligence Sample.pbix")
# the columns that read TODAY(), so differ from Desktop's on any other day
_TODAY_COLUMNS = {"RELATIVE MONTH", "RELATIVE WEEK", "RELATIVE DAY",
                  "RELATIVE 07 DAY PERIOD", "RELATIVE 30 DAY PERIOD"}


def _cell(v):
    if isinstance(v, datetime):
        return (v.year, v.month, v.day, v.hour, v.minute, v.second)
    if isinstance(v, date):
        return (v.year, v.month, v.day, 0, 0, 0)
    return v


@pytest.mark.skipif(not os.path.exists(_SAMPLE), reason="Artificial Intelligence Sample.pbix not present")
def test_the_samples_calendars_equal_what_desktop_stored():
    from pbix_mcp.formats.model_reader import ModelReader
    reader = ModelReader(_SAMPLE)
    exprs = {r["Name"]: r["QueryDefinition"] for r in reader._query_metadata(
        "SELECT t.Name, p.QueryDefinition FROM [Table] t JOIN [Partition] p ON p.TableID = t.ID "
        "WHERE p.Type = 2 AND t.Name IN ('Case Calendar', 'Opportunity Calendar')")}
    facts = {t: reader.get_table(t) for t in ("Cases", "Opportunities")}
    rows = cells = 0
    for name in ("Case Calendar", "Opportunity Calendar"):
        stored = reader.get_table(name)
        got, err = build_table(exprs[name], facts)
        assert err is None, (name, err)
        assert got["columns"][0] == "Date" and set(got["columns"]) == set(stored["columns"])
        at = stored["columns"].index("Date")
        want = sorted(stored["rows"], key=lambda r: r[at])
        assert len(got["rows"]) == len(want)
        for col in stored["columns"]:
            if col in _TODAY_COLUMNS:
                continue
            si, gi = stored["columns"].index(col), got["columns"].index(col)
            assert [_cell(r[gi]) for r in got["rows"]] == [_cell(r[si]) for r in want], (name, col)
            cells += len(want)
        rows += len(want)
    assert (rows, cells) == (1827, 19732)
