"""Issue #110: ROUND rounds half AWAY from zero, on the decimal value.

_fn_round was Python's round(): ties to the even neighbour, on the binary
double -- ROUND(2.5, 0) was 2, ROUND(1250, -2) 1200, ROUND(1.005, 2) 1.0.
Expected values: Power BI Desktop 2.152 over ADOMD, each probe a measure.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

DESKTOP = {   # expression -> Desktop 2.152; * = 0.9.119 differed
    "ROUND(2.5, 0)": 3,            # *
    "ROUND(-2.5, 0)": -3,          # *
    "ROUND(1250, -2)": 1300,       # *
    "ROUND(-1250, -2)": -1300,     # *
    "ROUND(1.005, 2)": 1.01,       # * the decimal value, not the double 1.00499...
    "ROUND(-1.005, 2)": -1.01,     # *
    "ROUND(0.125, 2)": 0.13,       # *
    "ROUND(2.675, 2)": 2.68,       # *
    "ROUND(0.285, 2)": 0.29,       # *
    "ROUND(1.45, 1)": 1.5,         # *
    "ROUND(0.5, 0)": 1,            # *
    "ROUND(-0.5, 0)": -1,          # *
    "ROUND(5, -1)": 10,            # *
    "ROUND(2.345, 2)": 2.35,
    "ROUND(1234.5678, 1)": 1234.6,
    "ROUND(15, -1)": 20,
    "ROUND(-15, -1)": -20,
    "ROUND(1.5, 0)": 2,
    "ROUND(123.456, -1)": 120,
}


@pytest.mark.parametrize("expr,want", list(DESKTOP.items()), ids=list(DESKTOP))
def test_round_matches_desktop(expr, want):
    got = de.evaluate_measures_smart(["m"], {}, {"m": expr}, {}, simulate_row_context=False)["m"]
    assert got == pytest.approx(want, abs=1e-12)


def test_round_of_blank_and_specials_does_not_raise():
    m = {"b": "ROUND(BLANK(), 2)"}
    assert de.evaluate_measures_smart(["b"], {}, m, {}, simulate_row_context=False)["b"] is None
