"""#321 (#248): POWER(base, exponent) is the ^ operator's
arithmetic, errors included.

What was wrong (0.9.147): POWER called math.pow, so POWER(0, -1),
POWER(10, 400) and POWER(-8, 0.5) raised Python's ValueError /
OverflowError ("math domain error", "math range error") instead of a DAX
error, and POWER(-8, 1 / 3) raised where the odd root is -2.

Expected values: Power BI Desktop 2.152 over ADOMD, builder verbatim:
build_b248c.py b_pow2 g() cells (type letter + FORMAT(CONVERT(x, DOUBLE),
"0.000000000"), "E" an error) for POWER(0, -1), POWER(10, 400) and
POWER(-8, 0.5). Desktop's error for 0 ^ 0 (raw a_err_pow00, "Et argument i
funksjonen POWER har feil datatype, eller resultatet er for stort eller for
lite.") names POWER: the operator is the function, so its message and its
odd root ((-8) ^ (1 / 3) is D-2, b_pow) are taken as POWER's. Those two
are inferences, not POWER cells Desktop answered; the tests that pin them
say so, and build_b248g.py p_pow / p_pow_msg ask.

Desktop's b248g answers (test_desktop_b248g, builder verbatim) confirm
them: p_pow is D1 B E E E D-2 D-0.5 D-2 -- POWER(2, BLANK()) 1, POWER(BLANK(),
0) BLANK, 0 ^ BLANK() an error, (-8) ^ (2 / 3) an error, the odd roots of
(-32) ^ 0.2, (-8) ^ (-1 / 3) and POWER(-8, 1 / 3) -- and p_pow_msg's
POWER(-8, 0.5) fails with POWER's message. One cell was new: BLANK() ^
BLANK() is an error (0 ^ 0), where the engine of round 3 answered BLANK.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit

POWER_MSG = ("An argument of function 'POWER' has the wrong data type or the result is too large "
             "or too small.")


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext({}, {}, None, None, None, []))


CASES = [
    ("POWER(0, -1)", "E"),
    ("POWER(10, 400)", "E"),
    ("POWER(-8, 0.5)", "E"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_cells(expr, desktop):
    # b248c b_pow2, measured for POWER itself
    assert _ev(g(expr)) == desktop


def test_odd_root_inferred_from_the_operator():
    # NOT a Desktop cell for POWER: Desktop measured (-8) ^ (1 / 3) = D-2
    # (b_pow); POWER takes the operator's arithmetic because 0 ^ 0's message
    # names POWER (a_err_pow00). build_b248g.py p_pow asks POWER(-8, 1 / 3)
    assert _ev(g("POWER(-8, 1 / 3)")) == "D-2.000000000"


def test_powers_message_measured_for_the_operator():
    # a_err_pow00: Desktop's message text for 0 ^ 0
    with pytest.raises(DAXEvaluationError) as ei:
        _ev("0 ^ 0")
    assert POWER_MSG in str(ei.value)


@pytest.mark.parametrize("expr", ["POWER(0, 0)", "POWER(0, -1)", "POWER(10, 400)", "POWER(-8, 0.5)"])
def test_errors_take_powers_message_inferred(expr):
    # NOT measured text: Desktop gave only E for these (b_pow2) and the text
    # for 0 ^ 0 alone; the shared message is inferred from that, and
    # build_b248g.py p_pow_msg asks POWER(-8, 0.5)'s text
    with pytest.raises(DAXEvaluationError) as ei:
        _ev(expr)
    assert POWER_MSG in str(ei.value)


def test_operator_and_function_agree():
    for base, exp in (("2", "10"), ("-8", "3"), ("CURRENCY(2)", "CURRENCY(2)"), ("TRUE()", "2"), ("2.5", "-2")):
        assert _ev(f"POWER({base}, {exp})") == _ev(f"({base}) ^ ({exp})")


def cells(*xs):
    return ' & " " & '.join(xs)


def test_desktop_b248g_p_pow():
    # build_b248g.py p_pow, Desktop 2.152 (desk_b248g.json)
    expr = cells(g("POWER(2, BLANK())"), g("POWER(BLANK(), 0)"), g("0 ^ BLANK()"), g("BLANK() ^ BLANK()"),
                 g("(-8) ^ (2 / 3)"), g("(-32) ^ 0.2"), g("(-8) ^ (-1 / 3)"), g("POWER(-8, 1 / 3)"))
    assert _ev(expr) == "D1.000000000 B E E E D-2.000000000 D-0.500000000 D-2.000000000"


def test_desktop_b248g_p_pow_msg():
    # build_b248g.py p_pow_msg: "Et argument i funksjonen POWER har feil
    # datatype, eller resultatet er for stort eller for lite."
    with pytest.raises(DAXEvaluationError) as e:
        _ev("POWER(-8, 0.5)")
    assert POWER_MSG in str(e.value)


@pytest.mark.parametrize("expr", ["BLANK() ^ BLANK()", "POWER(BLANK(), BLANK())"])
def test_blank_to_a_blank_power_is_zero_to_zero(expr):
    # p_pow cell 3 for the operator; POWER, its arithmetic, the same (POWER's
    # form not measured)
    with pytest.raises(DAXEvaluationError) as e:
        _ev(expr)
    assert POWER_MSG in str(e.value)
