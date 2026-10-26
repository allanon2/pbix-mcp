"""Issue #346: an RLE run longer than 16,384 rows adds value x rows
per 16,384-row chunk, not once.

What was wrong: a piece of the scan where the summed column is RLE added
value x (its selected rows) in one product. Power BI Desktop 2.152 cuts every
piece into chunks of 16,384 rows from the piece's start, an RLE piece too, and
adds value x (the chunk's selected rows) per chunk. Contoso's V_Fact (13
segments of up to 1,048,576 rows; build_b243b.py) has all-RLE columns whose
runs are up to a segment long: with the chunks, every answer of both Desktop
sessions is the segments' partials added in some order -- cs_cur
(CurrencyKey x 0.3, one run per segment) 0.400000003632158 in segment order,
0.400000004097819 in reverse; cs_salesquantity 0.599999994970858 in both;
cs_discountquantity 0.80800000205636 in segment order -- where one product
per run gave none of them (0.39999999897554517, 0.6000000014901161,
0.8080000001937151). The Contoso battery as a whole: 15 of 32 answers ->
19 of 32 (the rest differ only in the order of the segments' partials,
which varies from one Desktop session to the next).

Where the expected values come from: exact IEEE arithmetic of the rule on
hand-made layouts (the Contoso model is beyond the test suite's memory).
"""
from __future__ import annotations

from array import array

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

N = 60000                      # chunks of 16384 x 3 and 10848


def _tables(rle: bool, value: float = 0.7) -> dict:
    rows = [[value, i % 5] for i in range(N)]
    st = {"rows": N, "segments": (N,),
          "columns": {"v": {"starts": array("q", [0]), "rle": bytes([1 if rle else 0]),
                            "enc": "hash", "mag": 1.0}}}
    return {"T": {"columns": ["v", "k"], "rows": rows, "storage": st}}


def _chunks(v: float, sizes) -> float:
    acc = 0.0
    for k in sizes:
        acc += v * k
    return acc


def test_a_long_rle_run_adds_one_product_per_chunk():
    got = de.evaluate_measures_batch(["m"], _tables(True), {"m": "SUM(T[v])"}, {})["m"]
    want = _chunks(0.7, (16384, 16384, 16384, 10848))
    assert want == 41999.99999999999
    assert 0.7 * N == 42000.0                      # one product: what it was
    assert got == want


def test_the_chunks_count_the_selected_rows():
    # not probed -- a hypothesis: the chunks of a bit-packed piece count
    # their selected rows (rule 1), taken to hold for an RLE piece too
    got = de.evaluate_measures_batch(["m"], _tables(True, 0.3), {"m": "CALCULATE(SUM(T[v]), T[k] <> 2)"},
                                     {})["m"]
    # 13107, 13107, 13108 and 8678 rows: 14399.999999999998, one product 14400
    sizes = [sum(1 for i in range(a, min(a + 16384, N)) if i % 5 != 2) for a in range(0, N, 16384)]
    assert got == _chunks(0.3, sizes) == 14399.999999999998
    assert 0.3 * sum(sizes) == 14400.0


def test_a_run_of_16384_rows_or_fewer_is_one_product():
    rows = [[0.7] for _ in range(16384)]
    st = {"rows": 16384, "segments": (16384,),
          "columns": {"v": {"starts": array("q", [0]), "rle": b"\x01", "enc": "hash", "mag": 1.0}}}
    got = de.evaluate_measures_batch(["m"], {"T": {"columns": ["v"], "rows": rows, "storage": st}},
                                     {"m": "SUM(T[v])"}, {})["m"]
    assert got == 0.7 * 16384
