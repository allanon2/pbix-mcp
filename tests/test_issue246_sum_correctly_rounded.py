"""Issue #246: SUM, AVERAGE, AVERAGEA and AVERAGEX give the same total on
every Python version -- now the total Power BI Desktop's storage engine gives
(issue #243), no longer the correctly rounded one.

What was wrong in #246: they added with the builtin sum(), compensated
(Neumaier) from Python 3.12 and plain on 3.10 / 3.11, so one model gave
different totals on the supported interpreters. 0.9.147 made them correctly
rounded (math.fsum). Issue #243 measured the order Desktop really adds in: a
column holding one bit-packed run -- every table our builder writes, and every
table without a decoded layout -- of 16,384 rows or fewer adds left to right
in row order (Power BI Desktop 2.152, build_b210.py: ten rows of 0.1 sum to
0.9999999999999999 in SUM as in SUMX, f_sum10_bits = f_sumx10_bits =
-11.102230246251565, f_avg10_bits -13.877787807814457; TBig 2**53, 1, -2**53
sums to 0, f_big). That is a fixed sequence of IEEE additions, so #246's aim
holds: the same answer on every Python version. SUMX and AVERAGEX over the
table's own column add as SUM and AVERAGE (build_b243.py x_r_N = s_r_N).

Where the expected values come from: Desktop's answers by probe id where it
answered these rows, else exact IEEE arithmetic of that rule (a left fold of
the values in row order, from 0.0, computed here with plain float additions).
_sum_numbers, the correctly rounded sum still used for a column that is no
table's, keeps its own tests.
"""
from __future__ import annotations

import math
import random

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

AGGS = {"s": "SUM(T[v])", "a": "AVERAGE(T[v])", "aa": "AVERAGEA(T[v])", "ax": "AVERAGEX(T, T[v])",
        "x": "SUMX(T, T[v])"}


def _eval(measures: dict, values: list) -> dict:
    tables = {"T": {"columns": ["v"], "rows": [[v] for v in values]}}
    return de.evaluate_measures_batch(list(measures), tables, dict(measures), {})


def _fold(values) -> float:
    total = 0.0
    for v in values:
        total += v
    return total


def test_a_short_column_adds_left_to_right():
    # ((0 + 7e-17) + 1e16) + 1.0: 1e16 + 1 rounds back to 1e16 (the spacing
    # of the Doubles there is 2); the correctly rounded total of 0.9.147 was
    # 1.0000000000000002e16
    values = [7e-17, 1e16, 1.0]
    got = _eval(AGGS, values)
    assert got["s"] == got["x"] == 1e16 == _fold(values)
    assert got["a"] == got["aa"] == got["ax"] == 1e16 / 3


@pytest.mark.parametrize("seed", range(6))
def test_the_row_order_decides_and_every_spelling_agrees(seed):
    rng = random.Random(seed)
    values = [rng.choice([1e16, -1e16, 1.0, 0.1, 7e-17, 3.3e15]) * rng.uniform(0.5, 2) for _ in range(300)]
    first = _eval(AGGS, values)
    assert first["s"] == first["x"] == _fold(values)
    assert first["a"] == first["aa"] == first["ax"] == _fold(values) / len(values)
    rng.shuffle(values)
    again = _eval(AGGS, values)
    assert again["s"] == again["x"] == _fold(values)


def test_whole_numbers_stay_exact_whole_numbers():
    values = [2 ** 60, 1, -(2 ** 60), 3]
    got = _eval({"s": "SUM(T[v])"}, values)
    assert got["s"] == 4 and type(got["s"]) is int


def test_sum_and_sumx_add_ten_tenths_as_desktop():
    # Power BI Desktop 2.152, build_b210.py T10: f_sum10_bits and
    # f_sumx10_bits -11.102230246251565, f_sum10_eq1 0, f_sum_eq_sumx 1
    got = _eval({"s": "SUM(T[v])", "x": "SUMX(T, T[v])"}, [0.1] * 10)
    assert got["x"] == got["s"] == 0.9999999999999999
    assert (got["s"] - 1) * 1e17 == -11.102230246251565


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
