"""List filters in filter_context match as DAX compares values.

* Text is case-insensitive: TREATAS({"north"}, T[Region]) selects "North".
* Text is text: '01' and '1' are different values (the old numeric fallback
  merged them into one key). A number still selects the text spelled the same
  way ('1' by 1), as callers pass codes either way, but never '01'.
* Datetimes are equal only as the same moment: a filter on 09:00 does not
  select 17:00 on the same day (the old date-only match merged them). A
  date-only entry is midnight, so '2026-10-01' still selects a Date table's
  2026-10-01 00:00 (the DATESMTD representation case).
* Numbers still match across int/float storage (issue #39).
"""
from __future__ import annotations

from datetime import datetime

from pbix_mcp.dax import engine as de

T = {"Region": "Region", "Code": "Code", "At": "At", "Year": "Year"}
TABLES = {"F": {"columns": ["Region", "Code", "At", "Year", "v"], "rows": [
    ["North", "01", datetime(2026, 10, 1, 9, 0), 2024.0, 1],
    ["South", "1", datetime(2026, 10, 1, 17, 0), 2025.0, 2],
    ["North", "2", datetime(2026, 10, 2, 0, 0), 2024.0, 4],
]}}


def _sum(filters):
    return de.evaluate_measures_smart(["M"], TABLES, {"M": "SUM('F'[v])"}, filters,
                                      simulate_row_context=False)["M"]


def test_text_is_case_insensitive():
    assert _sum({"F.Region": ["north"]}) == 5


def test_a_text_cell_is_not_a_number():
    assert _sum({"F.Code": [1]}) == 2
    assert _sum({"F.Code": ["1"]}) == 2
    assert _sum({"F.Code": ["01"]}) == 1


def test_datetimes_match_as_moments():
    assert _sum({"F.At": ["2026-10-01 09:00:00"]}) == 1
    assert _sum({"F.At": [datetime(2026, 10, 1, 17, 0)]}) == 2
    assert _sum({"F.At": ["2026-10-02"]}) == 4            # a date is midnight
    assert _sum({"F.At": ["2026-10-01"]}) in (None, 0)    # no row at midnight on the 1st


def test_numbers_still_match_across_storage():
    assert _sum({"F.Year": [2024]}) == 5
