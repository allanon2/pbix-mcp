"""#314: the ^ operator.

What was wrong (0.9.147): ^ was not an operator at all: 2 ^ 3 was BLANK and
f(x) ^ y answered f(x) (CURRENCY(1.1) ^ 2 was the Currency 1.1).

Desktop: always a Double, right-associative, binding tighter than the sign
and than * and /; BLANK() ^ 2 is BLANK and 0 ^ 0 an error. ^ is POWER: a
BLANK exponent is 0 (2 ^ BLANK() is 1, where the first implementation made
it BLANK), a negative base takes an odd root ((-8) ^ (1 / 3) is -2, where
it raised), and 0 ^ 0 fails with POWER's own message.

Expected values: Power BI Desktop 2.152's g() cells over ADOMD (type letter
+ FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim):
build_b254b.py h_pow, build_b254.py f_math cell 10; TR is the battery's
table of counts -7 .. 7 (SUMX(TR, TR[v] ^ 2) = 280 / 10^8);
build_b248c.py b_pow, b_pow2 and the raw a_err_pow00 (its Norwegian text
"Et argument i funksjonen POWER har feil datatype, eller resultatet er for
stort eller for lite.", in English).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def _ev(expr):
    tables = {"TR": {"columns": ["id", "v"], "rows": [[i, (i - 8) / 10000] for i in range(1, 16)]}}
    de.mark_currency_columns(tables, {"TR": ["v"]})
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(tables, {}, None, None, None, []))


CASES = [
    ("2 ^ 3", "D8.000000000"),
    ("2 ^ 0.5", "D1.414213562"),
    ("-2 ^ 2", "D-4.000000000"),
    ("2 ^ 3 ^ 2", "D512.000000000"),
    ("2 ^ -1", "D0.500000000"),
    ("BLANK() ^ 2", "B"),
    ("0 ^ 0", "E"),
    ("SUMX(TR, TR[v] ^ 2)", "D0.000002800"),
    ("2 * 3 ^ 2", "D18.000000000"),
    ("CURRENCY(1.1) ^ 2", "D1.210000000"),
    # build_b248c.py b_pow
    ("2 ^ BLANK()", "D1.000000000"),
    ("BLANK() ^ 0", "B"),
    ("(-8) ^ (1 / 3)", "D-2.000000000"),
    ("(-8) ^ 3", "D-512.000000000"),
    ("TRUE() ^ 2", "D1.000000000"),
    ('"2" ^ 2', "D4.000000000"),
    ("CURRENCY(2) ^ CURRENCY(2)", "D4.000000000"),
    # build_b248c.py b_pow2
    ("0 ^ -1", "E"),
    ("10 ^ 400", "E"),
    ("(-8) ^ 0.5", "E"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_cells(expr, desktop):
    assert _ev(g(expr)) == desktop


def test_zero_to_the_zero_fails_with_powers_message():
    # build_b248c.py a_err_pow00: "0 ^ 0" -- POWER's message, in English
    from pbix_mcp.errors import DAXEvaluationError
    with pytest.raises(DAXEvaluationError) as ei:
        _ev("0 ^ 0")
    assert ("An argument of function 'POWER' has the wrong data type or the result is too large "
            "or too small.") in str(ei.value)
