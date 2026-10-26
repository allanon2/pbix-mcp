"""#309 (#248): CURRENCY(<text>) and CONVERT(<text>, CURRENCY)
read the text as an exact decimal and round it half to even; text that is no
number is an error.

What was wrong (0.9.147): CURRENCY(<text>) was the Int64 0 for any text, and
CONVERT(<text>, CURRENCY) read the text as a Double and did not round.

Expected values: Power BI Desktop 2.152's cells over ADOMD, builders
verbatim: build_b254.py r_cur_text and build_b254b.py a_text -- r() = type
letter + the value in counts of 1/10,000 ("0.00"), g() = type letter +
FORMAT(CONVERT(x, DOUBLE), "0.000000000"), "E" an error (ISERROR); the
error's text from build_b248c.py's raw a_err_cur_text.
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


def r(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE) * 10000, "0.00"))'


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext({}, {}, None, None, None, []))


CASES = [
    # build_b254.py r_cur_text
    (r('CURRENCY("0.00005")'), "C0.00"),
    (r('CURRENCY("0.00015")'), "C2.00"),
    (r('CURRENCY("0.00025")'), "C2.00"),
    (r('CURRENCY("-0.00005")'), "C0.00"),
    (r('CURRENCY("-0.00015")'), "C-2.00"),
    (r('CURRENCY("1.00005")'), "C10000.00"),
    (r('CURRENCY("0.03125")'), "C312.00"),
    # build_b254b.py a_text
    (r('CONVERT("0.00015", CURRENCY)'), "C2.00"),
    (r('CONVERT("0.00025", CURRENCY)'), "C2.00"),
    (r('CURRENCY("1.5E-4")'), "C2.00"),
    (r('CURRENCY(" 0.00025 ")'), "C2.00"),
    (g('CURRENCY("abc")'), "E"),
    (g('CURRENCY("")'), "E"),
    (g("CURRENCY(TRUE())"), "C1.000000000"),
    (g("CURRENCY(DATE(2024, 1, 1))"), "C45292.000000000"),
    (g("CURRENCY(BLANK())"), "B"),
    (g('CURRENCY(1) + "0.00015"'), "D1.000150000"),
    (g('VALUE("0.00015")'), "D0.000150000"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[str(i) for i in range(len(CASES))])
def test_desktop_cells(expr, desktop):
    assert _ev(expr) == desktop


def test_text_that_is_no_number_has_desktops_message():
    # build_b248c.py a_err_cur_text: CURRENCY("abc") fails with "Kan ikke
    # konvertere verdien abc for typen Text til typen Currency." -- in English
    from pbix_mcp.errors import DAXEvaluationError
    with pytest.raises(DAXEvaluationError, match=r"Cannot convert value 'abc' of type Text to type Currency\."):
        _ev('CURRENCY("abc")')
