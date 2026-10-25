"""Issue #239: MOD by 0, or by BLANK, is an error.

MOD(number, divisor) returned BLANK for a divisor of 0 or BLANK, so
ISERROR(MOD(2, 0)) was FALSE and IFERROR never took its alternate. Desktop
raises an error for both; a number argument reads BLANK as 0.

Expected values: Power BI Desktop 2.152 over ADOMD (battery
F:/_desktop_oracle/build_b234.py, measures m_mod_zero_err, m_mod_blank_err;
m_div_blank_err already matched). The remainders keep the divisor's sign,
the rule the DAX reference states for MOD (its examples: MOD(3, 2) is 1,
MOD(-3, 2) 1); tests/test_issue139 and tests/test_dax_accuracy pin the
argument conversions. A BLANK number, also with a divisor of 0, is not
measured; it stays BLANK and is deliberately not pinned.

&& and || stop at the side that decides, as AND() and OR() always did here:
they evaluated every side, so once MOD by 0 raised, a guard written with
them -- COUNTROWS(FILTER(T, T[d] <> 0 && MOD(T[n], T[d]) = 1)) -- failed the
measure where the AND() form, and 0.9.146, counted 2. Whether Desktop stops
early is not measured; the tests pin that the two forms agree.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"T": {"columns": ["n", "d"], "rows": [[7, 2], [7, 0], [-7, 2], [7, None]]}}


def _engine(dax):
    """The measure's answer; "ERROR" when the engine reports the measure
    failed."""
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    if "p" in eng.eval_errors:
        return "ERROR"
    return got


DESKTOP = [
    ("m_mod_zero_err", "ISERROR(MOD(2, 0))", True),
    ("m_mod_blank_err", "ISERROR(MOD(2, BLANK()))", True),
    ("m_div_blank_err", "ISERROR(1 / BLANK())", True),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _engine(expr) is want


@pytest.mark.parametrize("expr", ["MOD(2, 0)", "MOD(2, BLANK())", "MOD(2.5, 0.0)", 'MOD("7", 0)'])
def test_zero_or_blank_divisor_fails_the_measure(expr):
    assert _engine(expr) == "ERROR"


def test_iferror_takes_the_alternate():
    assert _engine("IFERROR(MOD(2, 0), -1)") == -1
    # per row: the row whose divisor is 0 or BLANK is the error
    assert _engine("SUMX(T, IFERROR(MOD(T[n], T[d]), 100))") == 1 + 100 + 1 + 100
    assert _engine("SUMX(T, MOD(T[n], T[d]))") == "ERROR"


@pytest.mark.parametrize("expr,want", [
    ("MOD(3, 2)", 1), ("MOD(-3, 2)", 1), ("MOD(3, -2)", -1), ("MOD(-3, -2)", -1),
    ("MOD(5.5, 2)", 1.5), ("MOD(10, 3)", 1), ("MOD(0, 5)", 0),
])
def test_remainder_takes_the_divisor_sign(expr, want):
    assert _engine(expr) == want


@pytest.mark.parametrize("ops,funcs", [
    ("COUNTROWS(FILTER(T, T[d] <> 0 && MOD(T[n], T[d]) = 1))",
     "COUNTROWS(FILTER(T, AND(T[d] <> 0, MOD(T[n], T[d]) = 1)))"),
    ("COUNTROWS(FILTER(T, T[d] = 0 || ISBLANK(T[d]) || MOD(T[n], T[d]) = 1))",
     "COUNTROWS(FILTER(T, OR(OR(T[d] = 0, ISBLANK(T[d])), MOD(T[n], T[d]) = 1)))"),
    ("SUMX(T, IF(T[d] <> 0 && MOD(T[n], T[d]) = 1, 1))",
     "SUMX(T, IF(AND(T[d] <> 0, MOD(T[n], T[d]) = 1), 1))"),
    ("IF(FALSE() && MOD(1, 0) = 1, 1, 0)", "IF(AND(FALSE(), MOD(1, 0) = 1), 1, 0)"),
    ("IF(TRUE() || MOD(1, 0) = 1, 1, 0)", "IF(OR(TRUE(), MOD(1, 0) = 1), 1, 0)"),
])
def test_a_guard_written_with_operators_guards_as_and_or_do(ops, funcs):
    got = _engine(ops)
    assert got != "ERROR"
    assert got == _engine(funcs)


def test_the_guard_must_come_first():
    assert _engine("COUNTROWS(FILTER(T, MOD(T[n], T[d]) = 1 && T[d] <> 0))") == "ERROR"
    assert _engine("TRUE() && TRUE() && FALSE()") is False
    assert _engine("FALSE() || BLANK() || 1") is True
