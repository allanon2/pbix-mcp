"""Issue #86: pbix_evaluate_dax_per_dimension ignored marked date tables -- the
gap #79 closed in pbix_evaluate_dax_grouped -- and, like it, left out the BLANK
group (#82).

Its per-value fallback called evaluate_measures_batch without date_tables, so
a filter on a marked date table's date column intersected the table's other
filters instead of clearing them. Expected values are Power BI Desktop 2.152's
(over ADOMD, #79 and #82), which the grouped tool already returns.
"""
from __future__ import annotations

import json
import re

import pytest

from pbix_mcp import server as S
from tests.test_issue78_date_table_rule import build_date_model
from tests.test_issue82_blank_member import build_blank_member_model

pytestmark = pytest.mark.unit


def _per_dimension(alias, measures, dimension):
    r = json.loads(S.pbix_evaluate_dax_per_dimension(alias, measures, dimension, "{}"))
    assert r["success"], r
    rows = {}
    for line in r["message"].splitlines()[3:]:
        cells = re.split(r"\s{2,}", line.strip())
        if len(cells) > 1:
            rows[cells[0]] = [None if c == "(null)" else int(c.replace(",", ""))
                              for c in cells[1:]]
    return rows


@pytest.fixture(scope="module")
def dates(tmp_path_factory):
    alias = build_date_model(str(tmp_path_factory.mktemp("i86") / "dates.pbix"))
    yield alias
    S._open_files.pop(alias, None)
    S._dax_cache.pop(alias, None)


@pytest.fixture(scope="module")
def blank(tmp_path_factory):
    alias = build_blank_member_model(str(tmp_path_factory.mktemp("i86b") / "blank.pbix"), {})
    yield alias
    S._open_files.pop(alias, None)
    S._dax_cache.pop(alias, None)


def test_marked_date_table_rule_reaches_the_per_dimension_tool(dates):
    rows = _per_dimension(dates, "DM_le,DM_ytd", "DM.Month")
    # Desktop: the date filter on the MARKED table clears Month (616 each);
    # 0.9.112 answered 496 / 120 / (null) and YTD 496 / 435 / 496
    assert [rows[m] for m in ("1", "2", "3")] == [[616, 496], [616, 931], [616, 1427]]


def test_unmarked_table_still_intersects(dates):
    rows = _per_dimension(dates, "DU_le", "DU.Month")
    assert [rows[m] for m in ("1", "2", "3")] == [[496], [120], [None]]


def test_the_blank_group_is_listed(blank):
    rows = _per_dimension(blank, "Total", "BD.Name")
    # Desktop: X 33, Y 2, Z 8 and (Blank) 20 -- the rows keyed 98 and 99
    assert rows == {"X": [33], "Y": [2], "Z": [8], "(Blank)": [20]}
