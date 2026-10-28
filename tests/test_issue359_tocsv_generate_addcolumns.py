"""#359: TOCSV heads and fills GENERATE, GENERATEALL and ADDCOLUMNS over a
one-column table as Desktop does.

What was wrong (0.9.149): TOCSV took the first row's keys as written and
headed every one with the row's table. A GENERATE row keeps its inner row
under keys prefixed ``_inner_``, so TOCSV(GENERATE(CALENDAR(..), ROW("Y",
..))) printed '__calendar__'[_inner___table__], '__calendar__'[_inner_Y],
.. and GENERATE(T, ROW(..)) / GENERATE(T, U) / GENERATEALL dropped the inner
columns; ADDCOLUMNS(VALUES(T[Region]), "N", 1) holds Region as
``__value__`` and printed three empty lines.

Expected values: Power BI Desktop 2.152, build_b358.py (desk.json under
F:\\_tmp_pbix\\pr352\\b358\\, the strings as Desktop printed them): a model
table's column is headed 'T'[Column], any column a table function made --
ROW, ADDCOLUMNS, SELECTCOLUMNS, CALENDAR's Date -- [Name]; GENERATE is the
outer row's columns then the inner row's, both Region columns kept in a
measure (GENERATE(T, U); a calculated table refuses the repeated name, #356);
a date reads as its text, a BLANK as nothing. TOCSV(K) of a model table is
unchanged (tests/conformance golden).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

# build_b358.py's T and U
MODEL = {
    "T": {"columns": ["Region", "Amount"], "rows": [["E", 1], ["W", 2], ["N", 3]]},
    "U": {"rows": [["E", 10]], "columns": ["Region", "X"]},
}


def _ev(dax):
    return de.DAXEngine()._eval_expr(dax, de.DAXContext(MODEL, {}, None, None, None, []))


@pytest.mark.parametrize("probe, dax, desktop", [
    ("g_cal_csv", 'TOCSV(GENERATE(CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 2)), ROW("Y", YEAR([Date]))))',
     "[Date],[Y]\n1/1/2024,2024\n1/2/2024,2024"),
    ("g_table_csv", 'TOCSV(GENERATE(T, ROW("Twice", [Amount] * 2)))',
     "'T'[Region],'T'[Amount],[Twice]\nE,1,2\nW,2,4\nN,3,6"),
    ("g_shared_csv", "TOCSV(GENERATE(T, U))",
     "'T'[Region],'T'[Amount],'U'[Region],'U'[X]\nE,1,E,10\nW,2,E,10\nN,3,E,10"),
    ("g_nested_csv", 'TOCSV(GENERATE(GENERATE(CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 1)), ROW("A", 1)), ROW("B", 2)))',
     "[Date],[A],[B]\n1/1/2024,1,2"),
    ("g_all_csv", 'TOCSV(GENERATEALL(T, FILTER(SELECTCOLUMNS(U, "X", U[X]), [X] > T[Amount] * 9)))',
     "'T'[Region],'T'[Amount],[X]\nE,1,10\nW,2,\nN,3,"),
    ("a_values_csv", 'TOCSV(ADDCOLUMNS(VALUES(T[Region]), "N", 1))', "'T'[Region],[N]\nE,1\nW,1\nN,1"),
    ("a_cal_csv", 'TOCSV(ADDCOLUMNS(CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 1)), "M", MONTH([Date])))',
     "[Date],[M]\n1/1/2024,1"),
])
def test_desktop_b358_tocsv(probe, dax, desktop):
    assert _ev(dax) == desktop, probe


def test_a_model_table_and_max_rows_read_as_before():
    assert _ev("TOCSV(T)") == "'T'[Region],'T'[Amount]\nE,1\nW,2\nN,3"
    assert _ev("TOCSV(T, 1)") == "'T'[Region],'T'[Amount]\nE,1"
    assert _ev('TOCSV(ADDCOLUMNS(T, "Half", [Amount] / 2), 2)') == "'T'[Region],'T'[Amount],[Half]\nE,1,0.5\nW,2,1"


def test_an_added_column_named_like_a_model_column_is_its_own():
    # derived from a_values_csv (not measured): ADDCOLUMNS' column is [Name]
    # even when the row's table has a column of that name
    assert _ev('TOCSV(ADDCOLUMNS(VALUES(T[Region]), "Amount", 7))') == "'T'[Region],[Amount]\nE,7\nW,7\nN,7"


def test_tojson_takes_the_same_headings():
    got = _ev('TOJSON(ADDCOLUMNS(VALUES(T[Region]), "N", 1), 1)')
    assert got.startswith("{\n\t\"header\": [\"'T'[Region]\", \"[N]\"],\n\t\"rowCount\": 3,")
    assert "[\"E\", 1]" in got
