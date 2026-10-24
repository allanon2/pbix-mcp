"""Issue #218: the cross-filter memo keys the date table and its date column.

A filter on the date table reaches a table with no relationship to it through
the date table's date column (_get_date_cross_filter's fallback), so what it
selects depends on which table is the date table and which column its date
column. The model-wide memo of the cross filters, ``(id(table), 'xtf',
filters, ALL() snapshot, relationships)``, left both out, and contexts over
one tables mapping can name different ones: calc_tables builds its own with
an auto-detected date table. Whichever context ran first answered for all.

Expected values: the engine's own answers on a fresh model (a new tables
mapping has a filter cache of its own); in either order on one model the
answers must be the same."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

DAYS = [datetime(2023, 12, 25) + timedelta(days=i) for i in range(14)]


def _calendar_model():
    # G has no relationship: only the date-table fallback filters it
    return {"Cal": {"columns": ["Date", "Year", "Shifted"],
                    "rows": [[d, d.year, d + timedelta(days=3)] for d in DAYS]},
            "G": {"columns": ["Date", "N"], "rows": [[d, 1] for d in DAYS]},
            "Y": {"columns": ["Date", "Year"], "rows": [[d, d.year] for d in DAYS]}}


def _count(tables, date_table, date_column="Date"):
    return de.evaluate_measure("G", tables, {"G": "COUNTROWS(G)"}, {"Cal.Year": [2024]},
                               date_table=date_table, date_column=date_column, relationships=[])


def test_two_date_tables_in_either_order():
    # date table Cal: its 2024 dates reach G (7 rows); date table Y: the Cal
    # filter is not a date-table filter and reaches nothing (14)
    assert (_count(_calendar_model(), "Cal"), _count(_calendar_model(), "Y")) == (7, 14)
    tables = _calendar_model()
    assert (_count(tables, "Cal"), _count(tables, "Y")) == (7, 14)
    tables = _calendar_model()
    assert (_count(tables, "Y"), _count(tables, "Cal")) == (14, 7)


def test_two_date_columns_in_either_order():
    # Cal[Shifted] is three days later: the 2024 rows' shifted dates are
    # 4-10 January, of which G holds 4-7
    fresh = (_count(_calendar_model(), "Cal", "Date"), _count(_calendar_model(), "Cal", "Shifted"))
    assert fresh == (7, 4)
    tables = _calendar_model()
    assert (_count(tables, "Cal", "Date"), _count(tables, "Cal", "Shifted")) == fresh
    tables = _calendar_model()
    assert (_count(tables, "Cal", "Shifted"), _count(tables, "Cal", "Date")) == fresh[::-1]


def test_a_sum_across_the_fallback_in_either_order():
    def model():
        return {"Sales": {"columns": ["Date", "Amount"],
                          "rows": [[date(2019, 5, 1), 1.0], [date(2020, 5, 1), 10.0], [date(2020, 6, 1), 100.0]]},
                "Cal": {"columns": ["Date", "Year"],
                        "rows": [[date(2019, 5, 1), 2019], [date(2020, 5, 1), 2020], [date(2020, 6, 1), 2020]]},
                "Other": {"columns": ["Date", "Year"], "rows": [[date(2019, 5, 1), 2019]]}}

    def run(tables, dt):
        return de.evaluate_measure("S", tables, {"S": "SUM(Sales[Amount])"}, {"Cal.Year": [2020]},
                                   date_table=dt, relationships=[])

    assert (run(model(), "Cal"), run(model(), "Other")) == (110.0, 111.0)
    for order in (("Cal", "Other"), ("Other", "Cal")):
        tables = model()
        got = {dt: run(tables, dt) for dt in order}
        assert got == {"Cal": 110.0, "Other": 111.0}, order
