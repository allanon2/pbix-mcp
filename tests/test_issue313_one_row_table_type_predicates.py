"""#313: ISCURRENCY / ISDOUBLE / ISINT64 / ISSTRING / ISBOOLEAN
/ ISNUMERIC / ISDATETIME and CONVERT read a one-row, one-column table as its
value.

What was wrong (0.9.147): the type predicates and CONVERT took the table
itself, so every predicate was FALSE and CONVERT BLANK for
CALCULATE(VALUES(TR[v]), TR[id] = 10), whose cell read "?".

Expected values: Power BI Desktop 2.152 over ADOMD, build_b254.py l_scalars
cell 2: g(CALCULATE(VALUES(TR[v]), TR[id] = 10)) is "C0.000200000" -- the
type predicates say Currency and CONVERT(..., DOUBLE) is 0.0002.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def _tables():
    tables = {"TR": {"columns": ["id", "v", "d"],
                     "rows": [[i, (i - 8) / 10000, datetime(2024, 1, i)] for i in range(1, 16)]}}
    de.mark_currency_columns(tables, {"TR": ["v"]})
    return tables


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(_tables(), {}, None, None, None, []))


def test_l_scalars_cell_2():
    assert _ev(g("CALCULATE(VALUES(TR[v]), TR[id] = 10)")) == "C0.000200000"
