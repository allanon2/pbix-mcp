"""FORMAT: a value that rounds to zero is formatted as zero; Currency parenthesises negatives.

Measured against Power BI Desktop 2.157 (engine 17.0.83.18, an en-US model):
FORMAT(value, picture) for 8 values x 15 pictures, 120 cells. Before this change
51 differed, for two reasons:

* Desktop rounds BEFORE choosing the sign and the section. A value that rounds
  to zero prints as zero -- no "-", no negative section -- and takes the zero
  section when there is one, even when the value was positive. The sign and
  section were chosen from the unrounded value, so a near-zero negative (a
  difference of two large, nearly equal floats) printed "-$0", "($0)" or "-0%".
* The named "Currency" format puts negatives in parentheses: "($0.40)".

Every expected string below is Desktop's.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de


def _fmt(expr):
    return de.evaluate_measures_smart(["M"], {}, {"M": expr}, {}, simulate_row_context=False)["M"]


@pytest.mark.parametrize("expr,desktop", [
    ('FORMAT(-0.004, "$#,##0")', "$0"),
    ('FORMAT(-0.4, "0")', "0"),
    ('FORMAT(-0.004, "0.00")', "0.00"),
    ('FORMAT(-0.004, "#,##0.00")', "0.00"),
    ('FORMAT(-0.004, "0%")', "0%"),
    ('FORMAT(-0.0000000001, "0.0%")', "0.0%"),
    ('FORMAT(-0.004, "#")', ""),
    ('FORMAT(-0.004, "0.##")', "0."),
    ('FORMAT(-0.004, "Fixed")', "0.00"),
    ('FORMAT(-0.004, "Standard")', "0.00"),
    ('FORMAT(-0.004, "Currency")', "$0.00"),
    ('FORMAT(-0.0000000001, "Percent")', "0.00%"),
])
def test_rounds_to_zero_prints_zero_without_a_sign(expr, desktop):
    assert _fmt(expr) == desktop


@pytest.mark.parametrize("expr,desktop", [
    ('FORMAT(-0.4, "$#,##0;($#,##0)")', "$0"),
    ('FORMAT(-0.4, "$#,##0;($#,##0);""zero""")', "zero"),
    ('FORMAT(0.004, "$#,##0;($#,##0);""zero""")', "zero"),
    ('FORMAT(-0.5, "$#,##0;($#,##0);""zero""")', "($1)"),
])
def test_rounds_to_zero_takes_the_zero_section(expr, desktop):
    assert _fmt(expr) == desktop


@pytest.mark.parametrize("expr,desktop", [
    ('FORMAT(-0.5, "0")', "-1"),
    ('FORMAT(-0.6, "$#,##0")', "-$1"),
    ('FORMAT(-0.004, "0.0%")', "-0.4%"),
    ('FORMAT(-0.0049, "Percent")', "-0.49%"),
    ('FORMAT(-0.4, "0.00")', "-0.40"),
])
def test_values_that_do_not_round_to_zero_keep_their_sign(expr, desktop):
    assert _fmt(expr) == desktop


@pytest.mark.parametrize("expr,desktop", [
    ('FORMAT(-0.4, "Currency")', "($0.40)"),
    ('FORMAT(-0.6, "Currency")', "($0.60)"),
    ('FORMAT(0.004, "Currency")', "$0.00"),
])
def test_named_currency_parenthesises_negatives(expr, desktop):
    assert _fmt(expr) == desktop
