"""#355 (reported with PR #352 by @allanon2): a bare [Column] over a row of
one column, and a row's column written in another case, is found.

What was wrong (0.9.149): a row of one column (CALENDAR, VALUES, DISTINCT)
carries its cell under ``__column__`` / ``__value__``, and that column was
matched in its exact spelling only, so ADDCOLUMNS(CALENDAR(..), "M",
MONTH([DATE])) failed with "Measure or column '[DATE]' cannot be found" (a
measure over it was BLANK). VALUES(T[Region]) happened to work through its
model table's columns, CALENDAR has none. A column a virtual table made
(ADDCOLUMNS / SELECTCOLUMNS / ROW, GENERATE's inner row) was found in its
exact spelling only as well.

Expected values:
- Microsoft's DAX syntax: names of tables, columns and measures are not
  case-sensitive, so [DATE], [date] and [Date] are one column.
- The Artificial Intelligence sample's Case Calendar (Desktop-saved) reads
  CALENDAR's Date as [DATE] and [Date] in one expression;
  test_issue356_generate_calc_table_columns.py recomputes it.
- Fixed Decimal columns (#248) and a natural join's right-hand columns keep
  their types; a SELECTCOLUMNS row carries its source's single column as
  lineage only (#225), so an enclosing row's column of that name is the one
  read. A row's column against a measure of its name: #358.
- Power BI Desktop 2.152, build_b358.py (desk.json under
  F:\\_tmp_pbix\\pr352\\b358\\): m_month_day_case "1-30,1-31,2-1",
  m_month_sumx 4, v_region_lower "E!,N!,W!", v_region_upper_filter 2,
  v_added_other_case "Ex,Nx,Wx" (0.9.149: BLANK, BLANK, -, -, BLANK).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.dax.calc_tables import evaluate_calc_table_expression as build_table

pytestmark = pytest.mark.unit

TABLES = {
    "Store": {"columns": ["Region", "Units"], "rows": [["North", 4], ["South", 5], ["East", 6]]},
    "Pair": {"columns": ["z"], "rows": [[1], [2]]},
}


def _scalar(dax, measures=None, tables=None):
    ctx = de.DAXContext(tables if tables is not None else TABLES, measures or {}, None, None, None, [])
    return de.DAXEngine()._eval_expr(dax, ctx)


def test_calendar_date_in_another_case():
    got, err = build_table('ADDCOLUMNS(CALENDAR(DATE(2023, 2, 27), DATE(2023, 3, 1)), '
                          '"Mon", MONTH([DATE]), "Dy", DAY([date]))', {})
    assert err is None, err
    assert got["rows"] == [[datetime(2023, 2, 27), 2, 27], [datetime(2023, 2, 28), 2, 28],
                           [datetime(2023, 3, 1), 3, 1]]


FEB = "CALENDAR(DATE(2023, 2, 27), DATE(2023, 3, 1))"


@pytest.mark.parametrize("dax, expected", [
    (f"SUMX({FEB}, MONTH([DATE]))", 7),
    (f'CONCATENATEX(ADDCOLUMNS({FEB}, "Mon", MONTH([DATE]), "Dy", DAY([date])), [Mon] & "/" & [Dy], " ", [Date], DESC)',
     "3/1 2/28 2/27"),
    (f"SUMX({FEB}, DATEDIFF(DATE(2023, 2, 1), [DaTe], DAY))", 26 + 27 + 28),
    ('CONCATENATEX(VALUES(Store[Region]), [region] & "?", "+", Store[Region], DESC)', "South?+North?+East?"),
    ('COUNTROWS(FILTER(VALUES(Store[Region]), [REGION] <> "South"))', 2),
    ('COUNTROWS(FILTER(DISTINCT(Store[Region]), [rEgIoN] = "East"))', 1),
    # a column a virtual table made, in another case
    ('CONCATENATEX(ADDCOLUMNS(VALUES(Store[Region]), "Initial", LEFT(Store[Region], 1)), [INITIAL], "", [initial], ASC)',
     "ENS"),
    ('SUMX(SELECTCOLUMNS(Store, "Qty", Store[Units]), [QTY])', 15),
    ('SUMX(GENERATE(Store, ROW("Triple", Store[Units] * 3)), [triple])', 45),
    ('SUMX(GENERATE(CALENDAR(DATE(2023, 3, 5), DATE(2023, 3, 6)), ROW("DayNo", WEEKDAY([date]))), [DAYNO])', 3),
    # an enclosing row's column in another case
    ('SUMX(SELECTCOLUMNS(Store, "q", Store[Units]), SUMX(Pair, [Q]))', 30),
])
def test_bare_column_in_any_case(dax, expected):
    assert _scalar(dax) == expected


def test_a_name_nothing_has_still_raises():
    got, err = build_table('ADDCOLUMNS(CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 1)), "M", MONTH([Datum]))', {})
    assert got is None and "[Datum]" in err


def test_selectcolumns_keeps_its_sources_column_as_lineage_only():
    # SELECTCOLUMNS(VALUES(Store[Region]), "x", ..) has the column x only:
    # [region] is the enclosing row's (#225), in any case
    dax = ('CONCATENATEX(ADDCOLUMNS(ROW("Region", "-out"), "r", '
           'CONCATENATEX(SELECTCOLUMNS(VALUES(Store[Region]), "x", Store[Region] & ""), [x] & [region], ",", [x], ASC)), [r])')
    assert _scalar(dax) == "East-out,North-out,South-out"


def test_fixed_decimal_and_joined_columns_keep_their_types():
    tables = {
        "C": {"columns": ["k", "price"], "rows": [[1, 0.1], [2, 0.2]]},
        "D": {"columns": ["k", "rate"], "rows": [[1, 0.5], [2, 0.25]]},
    }
    de.mark_currency_columns(tables, {"C": ["price"], "D": ["rate"]})
    iscur = 'IF(ISCURRENCY({}), "C", "N")'
    assert _scalar(f'CONCATENATEX(C, {iscur.format("[PRICE]")}, ",", C[k], ASC)', tables=tables) == "C,C"
    assert _scalar(f'CONCATENATEX(NATURALINNERJOIN(C, D), {iscur.format("[RATE]")}, ",", [k], ASC)',
                   tables=tables) == "C,C"
    assert _scalar(f'CONCATENATEX(NATURALINNERJOIN(C, D), {iscur.format("[rate]")}, ",", [k], ASC)',
                   tables=tables) == "C,C"
