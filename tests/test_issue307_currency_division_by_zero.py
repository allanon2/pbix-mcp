"""#307 (#248): a Fixed Decimal (Currency) divided by an Int64,
Double, BLANK or FALSE zero is an error, "Division by zero was encountered.";
divided by a Currency zero it is the IEEE Double, as before.

What was wrong (0.9.147): CURRENCY(1) / 0, / BLANK() and / 0.0 were
+Infinity and CURRENCY(0) / 0 NaN, the bare `/` operator's IEEE rule.

Expected values: Power BI Desktop 2.152 over ADOMD. build_b254.py d0_ci
(CURRENCY(1) / 0), d0_cb (/ BLANK()), d0_zz (CURRENCY(0) / 0) and
build_b254b.py d0_cd (/ 0.0), d0_cf (/ FALSE()) fail the measure with
"Beregningsfeil i målet ...: Deling på null er funnet." -- in English
"Division by zero was encountered."; their tcode() versions fail too. d0_cc
(CURRENCY(1) / CURRENCY(0)) is Infinity typed D, d0_nc (-CURRENCY(1) /
CURRENCY(0)) -Infinity, d0_zc (CURRENCY(0) / CURRENCY(0)) NaN. ISERROR and
IFERROR catch it (build_b248c.py d_div0_iserror, d_div0_iferror); in a
storage-engine iteration the quotient is out of range instead and fails
the query (#318, d_div0_se_raw).
"""
from __future__ import annotations

import math

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext({}, {}, None, None, None, []))


@pytest.mark.parametrize("expr", ["CURRENCY(1) / 0", "CURRENCY(1) / BLANK()", "CURRENCY(0) / 0",
                                  "CURRENCY(1) / 0.0", "CURRENCY(1) / FALSE()"])
def test_division_by_a_zero_is_an_error(expr):
    with pytest.raises(DAXEvaluationError, match="Division by zero was encountered"):
        _ev(expr)


def test_the_formula_engines_error_is_caught():
    # build_b248c.py d_div0_iserror: g(CURRENCY(1) / 0) is "E";
    # d_div0_iferror: IFERROR((IFERROR(CURRENCY(1) / 0, -1)) & "", "E") is "-1"
    assert _ev('IF(ISERROR(CURRENCY(1) / 0), "E", "?")') == "E"
    assert _ev('IFERROR((IFERROR(CURRENCY(1) / 0, -1)) & "", "E")') == "-1"


def test_division_by_a_currency_zero_is_ieee():
    assert _ev("CURRENCY(1) / CURRENCY(0)") == math.inf
    assert type(_ev("CURRENCY(1) / CURRENCY(0)")) is float          # d0_cc_t: D
    assert _ev("-CURRENCY(1) / CURRENCY(0)") == -math.inf
    assert math.isnan(_ev("CURRENCY(0) / CURRENCY(0)"))
