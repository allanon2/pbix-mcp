"""#271: a CALCULATE boolean filter that reads a measure,
columns of two tables or no column -- Power BI Desktop refuses it.

0.9.147 evaluated TM[d] < [m_cnt] with the measure once in the outer context
(4 rows kept), and applied no filter at all for [m_zero] = 0 or TRUE() (the
unfiltered 4). Desktop refuses each (its Norwegian, in English):

* a measure: "A function 'PLACEHOLDER' has been used in a True/False
  expression that is used as a table filter expression. This is not
  allowed." (k7, k10, k22) -- an aggregate is fine (k8, 4);
* columns of two tables: "The expression contains columns from multiple
  tables, but only columns from a single table can be used in a True/False
  expression that is used as a table filter expression." (k9);
* no column: "The True/False expression does not specify a column. Each
  True/False expressions used as a table filter expression must refer to
  exactly one column." (k11).

Expected values: Power BI Desktop 2.152 over ADOMD,
F:/_desktop_oracle/build_b255b.py, over TM(n, d, z) = (7, 2, 1), (7, 0, 0),
(-7, 2, 1), (7, BLANK, 0) and TC(n, d) = (1, 2), (2, 3).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]},
    "TC": {"columns": ["n", "d"], "rows": [[1, 2], [2, 3]]},
}
HOME = {"m_zero": "COUNTROWS(ALL(TM)) - 4", "m_cnt": "COUNTROWS(TM)"}


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {**HOME, "p": dax}))
    return ("ERROR", eng.eval_errors["p"]) if "p" in eng.eval_errors else (got, None)


@pytest.mark.parametrize("expr,words", [
    ("CALCULATE(COUNTROWS(TM), TM[d] < [m_cnt])", "'PLACEHOLDER' has been used in a True/False"),   # k7
    ("CALCULATE(COUNTROWS(TM), TM[d] = 2 || TC[n] = 1)", "columns from multiple tables"),        # k9
    ("CALCULATE(COUNTROWS(TM), [m_zero] = 0)", "'PLACEHOLDER' has been used in a True/False"),   # k10
    ("CALCULATE(COUNTROWS(TM), TRUE())", "does not specify a column"),                          # k11
    ("CALCULATE(COUNTROWS(TM), TM[d] < [m_cnt] && TM[n] = 7)",
     "'PLACEHOLDER' has been used in a True/False"),                                           # k22
    ("CALCULATE(COUNTROWS(TM), TM[d] = CALCULATE(MAX(TM[d]), ALL(TM)))",
     "'CALCULATE' has been used in a True/False"),                                             # b255c k24
    ("IFERROR(CALCULATE(COUNTROWS(TM), TM[d] < [m_cnt]), -1)",
     "'PLACEHOLDER' has been used in a True/False"),                                           # b255c k25
], ids=["k7", "k9", "k10", "k11", "k22", "k24", "k25"])
def test_refused(expr, words):
    got, err = _engine(expr)
    assert got == "ERROR" and words in err


def test_an_aggregate_is_no_measure():
    # k8
    assert _engine("CALCULATE(COUNTROWS(TM), TM[d] < COUNTROWS(TM))") == (4, None)


def test_a_measure_inside_an_aggregate_is_fine():
    """build_b255c.py k23: TM[d] < MAXX(ALL(TM), [m_cnt]) keeps the rows
    whose d is below 1 (each row's own count): d = 0 and BLANK, 2."""
    assert _engine("CALCULATE(COUNTROWS(TM), TM[d] < MAXX(ALL(TM), [m_cnt]))") == (2, None)
