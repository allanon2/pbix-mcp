"""#278: an apostrophe in a bracketed column name opened a
quoted name in the operator scanners.

DAX quotes a table name with apostrophes ('My Table'); inside [...] an
apostrophe is a character of the column's name. The comparison scanner and
the arithmetic / logical scanner treated it as the start of a quoted name, so
everything after TQ[it's] was "inside quotes" and no operator was found:
CALCULATE(COUNTROWS(TQ), TQ[it's] = 2) applied no filter (3; 0.9.147 answered
1, its regex path read the column), FILTER(TQ, TQ[it's] = 2) was BLANK,
SUMX(TQ, TQ[it's] * 2) BLANK.

Expected values: exact arithmetic over TQ(it's, v) = (1, 1), (2, 1), (3, 1).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"TQ": {"columns": ["it's", "v"], "rows": [[1, 1], [2, 1], [3, 1]]}}


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    return ("ERROR", eng.eval_errors["p"]) if "p" in eng.eval_errors else (got, None)


@pytest.mark.parametrize("expr,want", [
    ("CALCULATE(COUNTROWS(TQ), TQ[it's] = 2)", 1),
    ("CALCULATE(COUNTROWS(TQ), 'TQ'[it's] >= 2)", 2),
    ("CALCULATE(COUNTROWS(TQ), TQ[it's] IN {2, 3})", 2),
    ("CALCULATE(COUNTROWS(TQ), TQ[it's] = 2 && TQ[v] = 1)", 1),
    ("COUNTROWS(FILTER(TQ, TQ[it's] = 2))", 1),
    ("COUNTROWS(FILTER(TQ, TQ[it's] >= 2 || TQ[v] = 0))", 2),
    ("SUMX(TQ, IF(TQ[it's] = 2, 1, 0))", 1),
    ("SUMX(TQ, TQ[it's] * 2)", 12),
    ("SUMX(TQ, TQ[it's] + TQ[v])", 9),
])
def test_an_apostrophe_in_a_column_name(expr, want):
    assert _engine(expr) == (want, None)
