"""#275: a table of several values compared as a value is an
error, not FALSE.

Power BI Desktop 2.152 (F:/_desktop_oracle/build_b255c.py v15) answers
CALCULATE(IF(HASONEVALUE(TM[d]) && VALUES(TM[d]) = 7, 1), TM[z] = 0) with "A
table of multiple values was supplied where a single value was expected." --
under TM[z] = 0, TM[d] has the values 0 and BLANK, and the && evaluates both
operands under that filter (issue #249). The comparison compared the list of
rows with 7, FALSE, so the measure was BLANK. A one-row table is still its
value (issue #145), an empty one BLANK (#179).

TM(n, d, z) = (7, 2, 1), (7, 0, 0), (-7, 2, 1), (7, BLANK, 0).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]}}
MSG = "A table of multiple values was supplied where a single value was expected."


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    return ("ERROR", eng.eval_errors["p"]) if "p" in eng.eval_errors else (got, None)


def test_v15():
    got, err = _engine("CALCULATE(IF(HASONEVALUE(TM[d]) && VALUES(TM[d]) = 7, 1), TM[z] = 0)")
    assert got == "ERROR" and MSG in err


@pytest.mark.parametrize("expr", [
    "CALCULATE(IF(VALUES(TM[d]) = 7, 1, 0), TM[z] = 0)",      # v15's comparison alone
    "CALCULATE(IF(7 = VALUES(TM[d]), 1, 0), TM[z] = 0)",
    "CALCULATE(IF(VALUES(TM[d]) <> 7, 1, 0), TM[z] = 0)",
])
def test_the_comparison_itself_fails(expr):
    got, err = _engine(expr)
    assert got == "ERROR" and MSG in err


def test_one_value_is_a_value():
    """Under TM[d] = 2 VALUES(TM[d]) is the one value 2 (v13's shape, Desktop
    1 there); with no row it is BLANK, and BLANK = 0 holds."""
    assert _engine("CALCULATE(IF(VALUES(TM[d]) = 2, 1, 0), TM[d] = 2)") == (1, None)
    assert _engine("CALCULATE(IF(VALUES(TM[d]) = 0, 1, 0), TM[d] = 99)") == (1, None)
