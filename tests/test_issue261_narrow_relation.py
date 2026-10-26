"""#261: a window function in a row context whose relation
has fewer columns than the row.

Inside an iteration of T, a relation of some of T's columns -- ALL(T[c]),
ALL(T[a], T[b]) -- holds the iterated row by those columns. Power BI Desktop
2.152 numbers and offsets the rows so, with PARTITIONBY too. 0.9.147 compared
the row's every column with the relation's rows, found none, and then took
the relation's FIRST row for any row that has no single column: ROWNUMBER
',,' and '1,1,1,1', OFFSET ',,' and the first row's next row for every row.

Expected values are Desktop's answers by probe id: build_b257.py
(z_rownum_col_small, z_offset_col_small) and build_b257b.py (group G:
z_rownum_cols, z_offset_cols, z_rownum_part); the same tables.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"]]},
    "TS3": {"columns": ["G", "H", "L"],
            "rows": [[1, 1, "p"], [1, 2, "q"], [2, 1, "r"], [1, 1, "s"]]},
}


def _ev(expr):
    de._engine.eval_errors.clear()
    got = de.evaluate_measures_batch(["m"], TABLES, {"m": expr}, measure_tables={"m": "TS"})
    return got["m"], de._engine.eval_errors.get("m")


@pytest.mark.parametrize("expr,want", [
    ('CONCATENATEX(TS, ROWNUMBER(ALL(TS[G]), ORDERBY(TS[G])), ",", TS[L], ASC)',
     "1,1,2"),                                                          # z_rownum_col_small
    ('CONCATENATEX(TS, MAXX(OFFSET(1, ALL(TS[G]), ORDERBY(TS[G])), TS[G]), ",", TS[L], ASC)',
     "2,2,"),                                                           # z_offset_col_small
    ('CONCATENATEX(TS3, ROWNUMBER(ALL(TS3[G], TS3[H]), ORDERBY(TS3[G], ASC, TS3[H], ASC)), '
     '",", TS3[L], ASC)', "1,2,3,1"),                                   # z_rownum_cols
    ('CONCATENATEX(TS3, CONCATENATEX(OFFSET(1, ALL(TS3[G], TS3[H]), ORDERBY(TS3[G], ASC, '
     'TS3[H], ASC)), TS3[G] & "-" & TS3[H]), ",", TS3[L], ASC)', "1-2,2-1,,1-2"),  # z_offset_cols
    ('CONCATENATEX(TS, ROWNUMBER(ALL(TS[G]), ORDERBY(TS[G]), PARTITIONBY(TS[G])), ",", '
     'TS[L], ASC)', "1,1,1"),                                           # z_rownum_part
])
def test_the_row_is_found_by_the_relations_columns(expr, want):
    assert _ev(expr) == (want, None)
