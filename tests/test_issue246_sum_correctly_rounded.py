"""Issue #246: SUM, AVERAGE, AVERAGEA and AVERAGEX add correctly rounded
(math.fsum) on every Python version.

What was wrong: they added with the builtin sum(), compensated (Neumaier) from
Python 3.12 and plain on 3.10 / 3.11, so one model gave different totals on
the supported interpreters -- and even 3.12+'s compensation is not always the
correctly rounded total: over 7e-17, 1e16, 1.0 sum() gives 1e16 where the
exact total rounds to 1.0000000000000002e16.

Where the expected values come from: math.fsum and exact Fraction arithmetic
(the correctly rounded total of the Doubles). Why this rule and not
Desktop's row order (#228): against Power BI Desktop 2.152's answers on the
census's real models, the correctly rounded sum matched 65 of the 112 measures
the rules tell apart, sum() on 3.13 64 and a left-to-right fold 56; Desktop's
own order on large columns is #243. SUMX keeps adding left to right, as
Desktop does on a few rows (build_b210.py: ten rows of 0.1 sum to
0.9999999999999999 in SUMX).
"""
from __future__ import annotations

import math
import random
from fractions import Fraction

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

AGGS = {"s": "SUM(T[v])", "a": "AVERAGE(T[v])", "aa": "AVERAGEA(T[v])", "ax": "AVERAGEX(T, T[v])"}


def _eval(measures: dict, values: list) -> dict:
    tables = {"T": {"columns": ["v"], "rows": [[v] for v in values]}}
    return de.evaluate_measures_batch(list(measures), tables, dict(measures), {})


def _exact(values) -> float:
    return float(sum(Fraction(v) for v in values))


def test_where_compensation_is_not_correct_rounding():
    values = [7e-17, 1e16, 1.0]
    got = _eval(AGGS, values)
    assert got["s"] == 1.0000000000000002e16 == _exact(values)
    assert got["a"] == got["aa"] == got["ax"] == 1.0000000000000002e16 / 3


@pytest.mark.parametrize("seed", range(6))
def test_correctly_rounded_in_any_row_order(seed):
    rng = random.Random(seed)
    values = [rng.choice([1e16, -1e16, 1.0, 0.1, 7e-17, 3.3e15]) * rng.uniform(0.5, 2) for _ in range(300)]
    want = _exact(values)
    first = _eval(AGGS, values)
    rng.shuffle(values)
    again = _eval(AGGS, values)
    assert first == again
    assert first["s"] == want == math.fsum(values)
    assert first["a"] == first["aa"] == first["ax"] == want / len(values)


def test_whole_numbers_stay_exact_whole_numbers():
    values = [2 ** 60, 1, -(2 ** 60), 3]
    got = _eval({"s": "SUM(T[v])"}, values)
    assert got["s"] == 4 and type(got["s"]) is int


def test_sumx_keeps_adding_left_to_right():
    # Desktop, build_b210.py T10: SUMX of ten rows of 0.1 is 0.9999999999999999
    got = _eval({"s": "SUM(T[v])", "x": "SUMX(T, T[v])"}, [0.1] * 10)
    assert got["x"] == 0.9999999999999999
    assert got["s"] == 1.0


@pytest.mark.parametrize("values, want", [
    ([math.inf, 1.0], math.inf),
    ([math.inf, -math.inf], None),                # NaN, as IEEE addition gives
    ([1e308, 1e308, -1e308], math.inf),           # overflow: IEEE addition, as before
])
def test_infinities_nan_and_overflow_keep_ieee_addition(values, want):
    got = de._sum_numbers(values)
    if want is None:
        assert got != got
    else:
        assert got == want


def test_the_helper():
    assert de._sum_numbers([]) == 0
    assert de._sum_numbers([True, 2]) == 3
    assert de._sum_numbers(iter([0.1] * 10)) == 1.0
    assert de._sum_numbers([1, 0.5]) == 1.5
