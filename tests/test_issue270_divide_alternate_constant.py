"""#270: DIVIDE's alternate result must be a constant --
Power BI Desktop refuses any other, whether the division needs it or not.

_fn_divide evaluated the alternate result before dividing, so DIVIDE(1, 1,
MOD(1, [m_zero])) failed on MOD's division by zero; Desktop fails it too, but
because the alternate is no constant: "The alternate result to return on
division by zero must be a constant numeric value" (its Norwegian "Det
alternative resultatet om å returnere delinger med null må være en konstant
numerisk verdi.", in English), in a scalar and in a row. A constant is a
literal, TRUE() / FALSE() / BLANK(), DATE / CURRENCY of literals, or a VAR:
DIVIDE(1, 0, CURRENCY(2.5)) is 2.5, DIVIDE(4, 2, CURRENCY(1)) 2, DIVIDE(1, 0,
TRUE()) TRUE and VAR k = -1 RETURN DIVIDE(1, 0, k) -1, while INT(2.5),
COUNTROWS(TM) and a measure are refused (build_b255c.py y10-y17; the second
version of the fix took INT(2.5) and refused a measure only as part of an
expression). IFERROR does not catch the refusal (y12).

Expected values: Power BI Desktop 2.152 over ADOMD -- F:/_desktop_oracle/
build_b255b.py y3 and y4 (the refusal), build_b139.py div_alt_text
(DIVIDE(3, 0, "7") is "7"), build_b254b.py c_divide (cells 2 and 3: D2.5 and
D2, the CURRENCY alternates); DIVIDE(10, 0, -1) is -1 and DIVIDE(10, 4, -1)
2.5 by the function's definition (Microsoft Learn: the alternate result is
returned on division by zero).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]}}
HOME = {"m_zero": "COUNTROWS(ALL(TM)) - 4"}


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {**HOME, "p": dax}))
    return ("ERROR", eng.eval_errors["p"]) if "p" in eng.eval_errors else (got, None)


@pytest.mark.parametrize("expr", [
    "SUMX(TM, DIVIDE(TM[z], 1, MOD(TM[n], TM[d])))",   # y3
    "DIVIDE(1, 1, MOD(1, [m_zero]))",                   # y4
    "DIVIDE(1, 0, [m_zero])",                           # b255c y10: a measure
    "IFERROR(DIVIDE(1, 1, MOD(1, [m_zero])), -1)",      # b255c y12: IFERROR does not catch it
    "DIVIDE(1, 0, INT(2.5))",                           # b255c y15: a function of a literal
    "DIVIDE(1, 0, COUNTROWS(TM))",                      # b255c y16: a table read
], ids=["y3", "y4", "y10", "y12", "y15", "y16"])
def test_an_alternate_that_is_no_constant_is_refused(expr):
    got, err = _engine(expr)
    assert got == "ERROR"
    assert "alternate result" in err and "constant" in err


@pytest.mark.parametrize("expr,want", [
    ('DIVIDE(3, 0, "7")', "7"),
    ("DIVIDE(10, 0, -1)", -1),
    ("DIVIDE(10, 4, -1)", 2.5),
    ("DIVIDE(10, 0, BLANK())", None),
    ("DIVIDE(1, 0, CURRENCY(2.5))", 2.5),      # build_b254b.py c_divide, cell 2
    ("DIVIDE(4, 2, CURRENCY(1))", 2),          # cell 3
    ("VAR k = -1 RETURN DIVIDE(1, 0, k)", -1),  # build_b255c.py y11: a VAR
    ("DIVIDE(1, 0, TRUE())", True),             # y17
    # a number literal written with a leading dot, as Microsoft Learn writes
    # [Price] * .20 (DAX operators): a literal is a constant (b139, y11)
    ("DIVIDE(1, 0, .5)", 0.5),
    ("DIVIDE(1, 0, -.5)", -0.5),
    ("DIVIDE(1, 0, CURRENCY(.5))", 0.5),
])
def test_a_constant_alternate_is_taken(expr, want):
    assert _engine(expr) == (want, None)
