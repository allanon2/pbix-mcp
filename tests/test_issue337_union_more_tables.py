"""#337: UNION reads every table, not the first two.

UNION(<table_expression1>, <table_expression2> [, <table_expression>]...)
takes two or more tables (Microsoft's DAX reference for UNION). The engine
read args[0] and args[1] and dropped the rest without a word:
COUNTROWS(UNION({1}, {2}, {3})) was 2, and UNION(A, A, A) over a 2-row A
had 4 rows. The column typing of #326 (Int64 values that
meet a Double are Doubles) now sees every table too.

Expected values: exact arithmetic (row counts and sums of the tables'
rows), and for the typing Power BI Desktop 2.152's rule from
build_b257.py / build_b257b.py (k_max_id, ub_empty_*; see
test_issue326_mixed_number_columns.py). A UNION of three or more
tables is not itself measured on Desktop.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

P = 2 ** 53
TABLES = {
    "A": {"columns": ["v"], "rows": [[1], [2]]},
    "TC": {"columns": ["K"], "rows": [[P], [P + 1]]},
    "TD": {"columns": ["D"], "rows": [[float(P)]]},
}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


@pytest.mark.parametrize("expr,want", [
    ("COUNTROWS(UNION({1}, {2}, {3}))", 3),
    ("COUNTROWS(UNION(A, A, A))", 6),
    ("COUNTROWS(UNION(A, A, A, A, A))", 10),
    ("SUMX(UNION({1}, {2}, {3}, {4}), [Value])", 10),
    ("COUNTROWS(DISTINCT(UNION(A, {3}, A)))", 3),
])
def test_every_table_counts(expr, want):
    assert _ev(expr) == want


def test_a_third_table_types_the_column():
    """{2**53 + 1} meets the Double 2.5 in the fourth table: the column is
    Double, and 2**53 + 1 is the Double 2**53 (k_max_id's rule)."""
    assert _ev("MAXX(UNION({1}, {2}, {9007199254740993}, {2.5}), [Value]) - 9007199254740992") == 0


def test_an_empty_double_third_table_types_the_column():
    """An empty Double side in third place still makes the column Double
    (ub_empty_distinct's rule): 2**53 and 2**53 + 1 are one Double."""
    assert _ev('COUNTROWS(DISTINCT(UNION(SELECTCOLUMNS(TC, "v", TC[K]), '
               'SELECTCOLUMNS(FILTER(TC, FALSE()), "v", TC[K]), '
               'SELECTCOLUMNS(FILTER(TD, FALSE()), "v", TD[D]))))') == 1
