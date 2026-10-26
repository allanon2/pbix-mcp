"""#280: a bare text column as a CALCULATE filter was applied
as its visible values; Power BI Desktop converts each value to TRUE / FALSE
and fails on one that is no TRUE / FALSE.

CALCULATE(COUNTROWS(TS), TS[s]) over TS[s] = "1", "x", "3" answered 3 (the
column's values as a filter), and under an outer TS[s] = "x" 1. Desktop fails
both with "Cannot convert value 'x' of type Text to type True/False." (its
Norwegian "Kan ikke konvertere verdien x for typen Text til typen
True/False.", in English): a bare column is a boolean filter on ALL(TS[s])
(#264), which replaces the outer filter on the column (k45).

Only the bare-column CALCULATE filter converts this way. The engine's general
text-to-TRUE/FALSE conversion is unchanged and Desktop has not been asked
about it: COUNTROWS(FILTER(ALL(TS[s]), TS[s])) still counts 3,
CALCULATE(COUNTROWS(TS), NOT TS[s]) is BLANK and IF("x", 1, 0) is 1.

Expected values: Power BI Desktop 2.152 over ADOMD, F:/_desktop_oracle/
build_b255d.py k41 and k45 (errors), b255c k37 (a number column: 2).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]},
    "TS": {"columns": ["s", "k"], "rows": [["1", 1], ["x", 0], ["3", 1]]},
}


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    return ("ERROR", str(eng.eval_errors["p"])) if "p" in eng.eval_errors else (got, None)


@pytest.mark.parametrize("expr", [
    "CALCULATE(COUNTROWS(TS), TS[s])",                              # k41
    'CALCULATE(CALCULATE(COUNTROWS(TS), TS[s]), TS[s] = "x")',      # k45
    "CALCULATE(COUNTROWS(TS), KEEPFILTERS(TS[s]))",                 # the same filter, kept
])
def test_a_text_value_that_is_no_boolean_fails(expr):
    got, err = _engine(expr)
    assert got == "ERROR"
    assert "Cannot convert value 'x' of type Text to type True/False" in err


def test_a_number_column_still_filters_its_true_values():
    """build_b255c.py k37: CALCULATE(COUNTROWS(TM), TM[d]) is 2."""
    assert _engine("CALCULATE(COUNTROWS(TM), TM[d])") == (2, None)


def test_a_text_comparison_is_unchanged():
    """A comparison on the text column is no bare column: 2 rows are not "x"."""
    assert _engine('CALCULATE(COUNTROWS(TS), TS[s] <> "x")') == (2, None)
