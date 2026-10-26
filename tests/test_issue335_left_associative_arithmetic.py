"""#335: a mix of + and - (or of * and /) groups from the left.

The expression ladder split on '+' before '-' and on '*' before '/', so
a + b - c evaluated as a + (b - c) and a * b / c as a * (b / c): the same
in the reals, not in Doubles. Power BI Desktop answers POWER(2, 53) + 1 -
9007199254740992 with 0 -- (2**53.0 + 1) rounds back to 2**53 before the
subtraction -- where we answered 1. Microsoft's DAX operator reference:
operators of equal precedence are evaluated from left to right.

Expected values: Power BI Desktop 2.152 over ADOMD, battery
F:/_desktop_oracle/build_b257b.py z53_power (F:/_tmp_pbix/open2/b252-order53);
the other values are IEEE-754 Double arithmetic done left to right in
Python (exact arithmetic on the same operands).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"T": {"columns": ["x"], "rows": [[1]]}}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


def test_matches_desktop():
    assert _ev("POWER(2, 53) + 1 - 9007199254740992") == 0


@pytest.mark.parametrize("expr,want", [
    ("0.1 + 0.2 - 0.3", (0.1 + 0.2) - 0.3),
    ("1E16 + 1 - 1E16", (1e16 + 1) - 1e16),
    ("0.1 * 3 / 3", (0.1 * 3) / 3),
    ("1 - 0.9 + 0.1 - 0.2", ((1 - 0.9) + 0.1) - 0.2),
    ("2 * 0.1 / 0.3 * 3", ((2 * 0.1) / 0.3) * 3),
    ("10 - 3 - 2", 5),
    ("20 / 4 / 5", 1),
    ("1 + -2 - 3", -4),
    ("6 / -2 * 3", -9),
])
def test_left_to_right(expr, want):
    assert _ev(expr) == want


@pytest.mark.parametrize("expr,split", [
    ("a + b - c", (("+", "-"), ["a ", " b ", " c"])),
    ("a - b + c", (("-", "+"), ["a ", " b ", " c"])),
    ("a + b - c + d", (("+", "-", "+"), ["a ", " b ", " c ", " d"])),
    ("a - b - c", ("-", ["a ", " b ", " c"])),
    ("a + b + c", ("+", ["a ", " b ", " c"])),
    ("a * b / c", (("*", "/"), ["a ", " b ", " c"])),
    ("a / b * c / d", (("/", "*", "/"), ["a ", " b ", " c ", " d"])),
    ("a + -b", ("+", ["a ", " -b"])),
    ("a + -b - c", (("+", "-"), ["a ", " -b ", " c"])),
    ("a * -b - c", ("-", ["a * -b ", " c"])),
    ("1e-5 + x", ("+", ["1e-5 ", " x"])),
    ("1e-5 + x - 2E-3", (("+", "-"), ["1e-5 ", " x ", " 2E-3"])),
])
def test_split_points(expr, split):
    """One flat list of parts with the operator (or operators) between
    them; the fold goes left to right."""
    ops = ("+", "-") if split[0][0] in "+-" else ("*", "/")
    op, parts = de.DAXEngine._left_assoc_split(expr, ops)
    assert (op, list(parts)) == (split[0], split[1])


def _chain(n, first="1", even=" + 2", odd=" - 1"):
    return first + "".join(even if i % 2 == 0 else odd for i in range(n))


@pytest.mark.parametrize("n", [400, 500, 2000])
def test_long_mixed_chains_fold_flat(n):
    """A mixed chain is ONE fold, not a plan nested as deep as the chain:
    splitting at the last operator and analysing the left part again ran out
    of recursion at about 350 operators, and the measure came back BLANK
    (0.9.147 answered these). Expected: exact integer arithmetic, 1 + 2 - 1
    + 2 - ... with n operators."""
    want = 1 + 2 * ((n + 1) // 2) - (n // 2)
    assert _ev(_chain(n)) == want
    assert _ev("SUMX(T, " + _chain(n, first="T[x]") + ")") == want


def test_long_mixed_product_chain():
    """``2 * 3 / 3 * 3 / 3 ...`` with 600 operators: exact (each step of
    2 * 3 / 3 is a whole number in Doubles)."""
    assert _ev(_chain(600, first="2", even=" * 3", odd=" / 3")) == 2


@pytest.mark.parametrize("expr,want", [
    ("5 / 0 * -1", float("-inf")),
    ("5 / 0 / -1", float("-inf")),
    ("-5 / 0 * 2", float("-inf")),
    ("2 / BLANK() * 3", float("inf")),
])
def test_division_by_zero_keeps_folding(expr, want):
    """A division by zero is the running IEEE value, not the answer: the
    fold goes on, as (5 / 0) * -1 did when each pair was its own plan node.
    Expected: IEEE-754 arithmetic (inf * -1 = inf / -1 = -inf); the bare
    5 / 0 = inf and BLANK as 0 in a denominator are Desktop's (see
    _fold_arith)."""
    assert _ev(expr) == want


@pytest.mark.parametrize("expr", ["5 / 0 * 0", "0 / 0 * 1", "0 / 0 / 0"])
def test_division_by_zero_nan_keeps_folding(expr):
    """IEEE-754: inf * 0 and nan / 0 are nan."""
    r = _ev(expr)
    assert r != r
