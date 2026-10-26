"""Issue #341: a column of several segments adds each segment's
partial, then the partials in segment order.

What was wrong: the engine had no notion of a VertiPaq segment (tables past
about 1,048,576 rows are cut into segments of that many rows). Power BI
Desktop 2.152's answers on Contoso's V_Fact (12,627,608 rows, 13 segments;
build_b243b.py cs_*) show that each segment is scanned on its own -- a run
never crosses a segment, and a piece starts at each segment -- but also that
Desktop does not combine the segments' totals in a fixed order: the same
query run in two Desktop sessions (desk_b243b_contoso.json and
desk_b243b_contoso_2.json), and even two spellings of one query in one session
(cs_pk_r1 vs cs_pk_r2: 0.200000047683716 and 0.200000286102295), gave
different last bits. Over the 32 answers of the two sessions, adding the
partials in segment order matched 15 (cs_pk_r2 / cs_pk_r3 / cs_pk_avgx /
cs_promotionkey in one session, cs_pk / cs_pk_r1 / cs_cust in the other, ...),
in a tree 15, correctly rounded 17; no rule matched cs_storekey, cs_cur,
cs_salesquantity or cs_discountquantity. The engine adds the partials in
segment order -- one fixed sequence of additions, the same on every Python
version, which Desktop matches when its segment jobs finish in order. The
decoder hands the segments over with the runs (read_table_from_abf
``storage['segments']``).

Where the expected values come from: exact IEEE arithmetic of that rule on a
hand-made layout, checked against the order it must not equal (one
accumulator across the segments).
"""
from __future__ import annotations

import struct
from array import array

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.formats import vertipaq_decoder as vd

pytestmark = pytest.mark.unit


def _layout(n: int, columns: dict, segments: tuple) -> dict:
    return {"rows": n, "segments": segments,
            "columns": {c: {"starts": array("q", [s for s, _r in runs]),
                            "rle": bytes(1 if r else 0 for _s, r in runs), "enc": "hash", "mag": 1.0}
                        for c, runs in columns.items()}}


def test_partials_in_segment_order():
    vals = [1e16, 1e16, 1.0, 2.0]
    t = {"columns": ["v"], "rows": [[v] for v in vals],
         "storage": _layout(4, {"v": [(0, True), (1, True), (2, True), (3, True)]}, (2, 2))}
    got = de.evaluate_measures_batch(["s", "x"], {"T": t}, {"s": "SUM(T[v])", "x": "SUMX(T, T[v])"}, {})
    parts = (0.0 + 1e16 + 1e16) + (0.0 + 1.0 + 2.0)
    one = ((1e16 + 1e16) + 1.0) + 2.0
    assert parts != one
    assert got["s"] == got["x"] == parts


def test_a_bit_packed_run_restarts_its_chunks_at_each_segment():
    # 2**54 then ones, segments of 10,000 rows: each segment's chunk starts
    # at its first row (one run per segment), so the second segment's
    # 10,000 ones count, where one run of 20,000 rows would chunk at 16,384
    n = 20000
    rows = [[1.0] for _ in range(n)]
    rows[0][0] = 2.0 ** 54
    t = {"columns": ["v"], "rows": rows, "storage": _layout(n, {"v": [(0, False), (10000, False)]}, (10000, 10000))}
    got = de.evaluate_measures_batch(["s"], {"T": t}, {"s": f"SUM(T[v]) - {2 ** 54}"}, {})
    assert got["s"] == 10000


def _idf_segment(entries: list, words: list) -> bytes:
    slots = entries + [(0, 0)] * (16 - len(entries))
    out = struct.pack("<Q", 16) + b"".join(struct.pack("<II", dv, rv) for dv, rv in slots)
    return out + struct.pack("<Q", len(words)) + b"".join(struct.pack("<Q", w) for w in words)


def test_the_decoder_keeps_the_segments():
    seg1 = _idf_segment([(0xFFFFFFFF, 3)], [0x030201])
    seg2 = _idf_segment([(9, 2)], [])
    runs: list = []
    vd.decode_idf(seg1 + seg2, 8, 5, segments=[(8, 0, 0, 3), (8, 0, 0, 2)], runs_out=runs)
    assert runs == [(0, 3, False), (3, 2, True)]
    st = vd._storage_layout({"v": (runs, "hash", 1.0)}, ["v"], 5, (3, 2))
    assert st["segments"] == (3, 2)
    # records that do not add up to the rows: one segment
    assert vd._storage_layout({"v": (runs, "hash", 1.0)}, ["v"], 5, (3, 3))["segments"] == (5,)
