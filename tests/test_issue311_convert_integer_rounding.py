"""#311: CONVERT(x, INTEGER) rounds half away from zero -- a
Double in Double arithmetic, a Currency on its count, a date as its serial;
text is read as an exact decimal and rounded half to even; past the Int64
range it is Desktop's overflow error.

What was wrong (0.9.147): CONVERT(.., INTEGER) was int(round(float(x))),
half to even (CONVERT(2.5, INTEGER) 2, CURRENCY(2.5) 2), BLANK for a date,
and a Python int past Int64 for CONVERT(1E19, INTEGER).

Expected values: Power BI Desktop 2.152's g() cells over ADOMD (type letter +
FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim): build_b254.py
r_to_int, build_b254b.py a_int; the raw probe g_ovf_int (CONVERT(1E19,
INTEGER)) fails with "... er enten for stort eller for lite." -- "The result
of a conversion or arithmetic operation is either too large or too small.";
build_b248c.py's raw a_err_int_text gives the text of CONVERT("abc", INTEGER).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext({}, {}, None, None, None, []))


CASES = [
    # build_b254.py r_to_int
    ("CONVERT(CURRENCY(2.5), INTEGER)", "I3.000000000"),
    ("CONVERT(CURRENCY(-2.5), INTEGER)", "I-3.000000000"),
    ("CONVERT(CURRENCY(3.5), INTEGER)", "I4.000000000"),
    ("CONVERT(2.5, INTEGER)", "I3.000000000"),
    ("CONVERT(-2.5, INTEGER)", "I-3.000000000"),
    ("CONVERT(3.5, INTEGER)", "I4.000000000"),
    # build_b254b.py a_int
    ("CONVERT(0.49999999999999994, INTEGER)", "I1.000000000"),
    ('CONVERT("2.5", INTEGER)', "I2.000000000"),
    ('CONVERT("3.5", INTEGER)', "I4.000000000"),
    ("CONVERT(DATE(2024, 1, 1) + 0.5, INTEGER)", "I45293.000000000"),
    ("CONVERT(1E19, INTEGER)", "E"),
    ("CONVERT(-0.5, INTEGER)", "I-1.000000000"),
    ("CONVERT(CURRENCY(0.5), INTEGER)", "I1.000000000"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_cells(expr, desktop):
    assert _ev(g(expr)) == desktop


def test_past_int64_the_overflow_message():
    with pytest.raises(DAXEvaluationError, match="either too large or too small"):
        _ev("CONVERT(1E19, INTEGER)")


def test_text_that_is_no_number_has_desktops_message():
    # build_b248c.py a_err_int_text: CONVERT("abc", INTEGER) fails with "Kan
    # ikke konvertere verdien abc for typen Text til typen Integer." -- in English
    with pytest.raises(DAXEvaluationError, match=r"Cannot convert value 'abc' of type Text to type Integer\."):
        _ev('CONVERT("abc", INTEGER)')
