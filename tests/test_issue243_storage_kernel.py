"""Issue #243: SUM, AVERAGE, AVERAGEA, SUMX and AVERAGEX of a Double column add
as Power BI Desktop's storage engine does.

What was wrong: SUM and AVERAGE added a Double column correctly rounded
(math.fsum, issue #246), SUMX and AVERAGEX left to right across every row.
Desktop adds along the column's PHYSICAL layout (pbix_mcp.dax.se_scan): an RLE
run adds value x rows once, a bit-packed run adds in chunks of 16,384 rows
counted by storage position from the piece's start, each chunk folded left to
right and its total added; the pieces are cut at the runs of every column the
scan reads -- the filters' columns among them -- and a value-encoded column
adds its stored integers exactly. So the ten rows of 0.1 that 0.9.147 summed
to 1 are 0.9999999999999999 in Desktop, and 100,000 ones after 2**54 lose a
chunk's worth (83616, not 100000).

Where the expected values come from:
- Power BI Desktop 2.152's answers, by probe id: build_b210.py (T10, T3,
  TBig), build_b243.py (built tables of 1,000 to 200,000 rows, desk_built.json),
  build_b243b.py's x_run_mul / af_* / rc_* (Agents, Root Cause), and the
  Desktop-saved Agents and Root Cause files themselves (skipped when the
  community corpus is not present; PBIX_TEST_SAMPLES may point at it).
- exact IEEE arithmetic of the measured rule where a hand-made layout shows a
  part of it on a few rows: the expected value is the rule's own sequence of
  float additions, written out here (and compared with the other orders it
  must not equal, so each test tells the rule apart).
"""
from __future__ import annotations

import json
import math
import os
import random
import struct
import warnings
from array import array

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.formats import vertipaq_decoder as vd

pytestmark = pytest.mark.unit

BIG = 2.0 ** 54
# the corpus as the other corpus tests find it (test_cross_report._find_pbix):
# PBIX_TEST_SAMPLES, else ../test_samples; the name
# scripts/download_test_corpus.py gives a file, else its legacy path
_SAMPLES = os.environ.get("PBIX_TEST_SAMPLES") or os.path.join(
    os.path.dirname(__file__), "..", "test_samples")


def _corpus(clean, legacy: str) -> str:
    for p in (clean, legacy):
        if p and os.path.exists(os.path.join(_SAMPLES, p)):
            return os.path.join(_SAMPLES, p)
    return os.path.join(_SAMPLES, legacy)


AGENTS = _corpus("Agents_Performance.pbix", os.path.join(
    "temp_dl", "Full Dashboards", "Agents Performance - Dashboard",
    "Agents Performance - Dashboard.pbix"))
ROOT_CAUSE = _corpus(None, os.path.join(
    "temp_dl", "Full Dashboards", "Root Cause Analysis Dashboard",
    "Root Cause Analysis Dashboard.pbix"))


def _batch(measures: dict, tables: dict, fc: dict | None = None, **kw) -> dict:
    return de.evaluate_measures_batch(list(measures), tables, dict(measures), dict(fc or {}), **kw)


def _fold(values) -> float:
    total = 0.0
    for v in values:
        total += v
    return total


def _g15(v: float) -> float:
    """A value as the battery printed it: 15 significant digits."""
    return float(f"{v:.15g}")


# ---- (a) build_b210.py: tables of a few rows ----------------------------------

B210 = {
    # probe: (measure, Desktop)
    "f_sum10_eq1": ("IF(SUM(T10[v]) = 1, 1, 0)", 0),
    "f_sumx10_eq1": ("IF(SUMX(T10, T10[v]) = 1, 1, 0)", 0),
    "f_sum_eq_sumx": ("IF(SUM(T10[v]) = SUMX(T10, T10[v]), 1, 0)", 1),
    "f_sum10_bits": ("(SUM(T10[v]) - 1) * 1E17", -11.102230246251565),
    "f_sumx10_bits": ("(SUMX(T10, T10[v]) - 1) * 1E17", -11.102230246251565),
    "f_sum3_bits": ("(SUM(T3[v]) - 0.3) * 1E17", 5.551115123125783),
    "f_big": ("SUM(TBig[v])", 0),
    "f_bigx": ("SUMX(TBig, TBig[v])", 0),
    "f_avg10_bits": ("(AVERAGE(T10[v]) - 0.1) * 1E18", -13.877787807814457),
}


def _b210_tables() -> dict:
    return {"T10": {"columns": ["v"], "rows": [[0.1]] * 10},
            "T3": {"columns": ["v"], "rows": [[0.1]] * 3},
            "TBig": {"columns": ["v"], "rows": [[2.0 ** 53], [1.0], [-(2.0 ** 53)]]}}


@pytest.mark.parametrize("probe", list(B210))
def test_build_b210_few_rows(probe):
    expr, want = B210[probe]
    got = _batch({"m": expr}, _b210_tables())["m"]
    assert got == want, f"{probe}: {got!r}, Desktop {want!r}"


# ---- (b) build_b243.py: ones after 2**54 name the chunks ----------------------

B243_BIG = {
    # (rows, position of 2**54): Desktop's SUM - 2**54 (b_first / b_mid / b_last / b_7)
    (1000, 0): 0, (1000, 503): 504, (1000, 999): 1000, (1000, 7): 8,
    (100000, 0): 83616, (100000, 50003): 84468, (100000, 99999): 100000, (100000, 7): 83624,
    (200000, 0): 183616, (200000, 100003): 185316, (200000, 199999): 200000, (200000, 7): 183624,
}


@pytest.mark.parametrize("n, p", list(B243_BIG))
def test_build_b243_chunks_of_16384_rows(n, p):
    col = [[1.0]] * n
    col[p] = [BIG]
    tables = {"T": {"columns": ["b"], "rows": col}}
    got = _batch({"s": f"SUM(T[b]) - {int(BIG)}", "x": f"SUMX(T, T[b]) - {int(BIG)}"}, tables)
    assert got["s"] == got["x"] == B243_BIG[(n, p)]


# ---- (c) build_b243.py: random doubles, stored forward / reversed / shuffled --

def _b243_table(n: int) -> dict:
    """build_b243.table_data(n), as the battery built it."""
    rnd = random.Random(24300 + n)
    r = [rnd.uniform(1.0, 20000.0) * (1.0 if rnd.random() < 0.8 else 0.001) for _ in range(n)]
    shuf = r[:]
    random.Random(7 + n).shuffle(shuf)
    palette = [round(rnd.uniform(1.0, 5000.0), 3) for _ in range(40)]
    rd = [palette[rnd.randrange(40)] for _ in range(n)]
    cols = {"k": list(range(1, n + 1)), "g8": [i % 8 for i in range(n)], "r": r, "r_rev": r[::-1],
            "r_shuf": shuf, "rd": rd, "rd_sorted": sorted(rd)}
    names = list(cols)
    return {"columns": names, "rows": [[cols[c][i] for c in names] for i in range(n)]}


B243_RANDOM = {
    1000: {"s_r": "0.304653698578477", "s_rrev": "0.304653709754348", "s_rshuf": "0.30465370323509",
           "s_rd": "0.911999997682869", "s_rdsorted": "0.911999996285886", "x_r": "0.304653698578477",
           "f_half": "0.358560797292739", "f_g8": "0.734127986594103", "f_odd": "0.781470929272473"},
    100000: {"s_r": "0.883952736854553", "s_rrev": "0.883949995040894", "s_rshuf": "0.883949756622314",
             "s_rd": "0.6109978556633", "s_rdsorted": "0.610990285873413", "x_r": "0.883952736854553",
             "f_half": "0.136351823806763", "f_g8": "0.548661693930626", "f_odd": "0.229601383209229"},
    200000: {"s_r": "0.583879232406616", "s_rrev": "0.583875417709351", "s_rshuf": "0.583876848220825",
             "s_rd": "0.635004818439484", "s_rdsorted": "0.634989023208618", "x_r": "0.583879232406616",
             "f_half": "0.0844734907150269", "f_g8": "0.933166414499283", "f_odd": "0.0289812088012695"},
}


@pytest.mark.parametrize("n", list(B243_RANDOM))
def test_build_b243_random_doubles(n):
    t = _b243_table(n)
    cols = t["columns"]
    data = {c: [row[i] for row in t["rows"]] for i, c in enumerate(cols)}
    # each probe returns SUM - C, C = the floor of the exact total (the query's constant)
    masks = {"f_half": [k <= n // 2 for k in data["k"]], "f_g8": [g == 3 for g in data["g8"]],
             "f_odd": [g != 0 for g in data["g8"]]}
    probes = {}
    for pid, c in (("s_r", "r"), ("s_rrev", "r_rev"), ("s_rshuf", "r_shuf"), ("s_rd", "rd"),
                   ("s_rdsorted", "rd_sorted")):
        probes[pid] = (f"SUM(T[{c}])", math.floor(math.fsum(data[c])))
    probes["x_r"] = ("SUMX(T, T[r])", math.floor(math.fsum(data["r"])))
    for pid, flt in (("f_half", f"T[k] <= {n // 2}"), ("f_g8", "T[g8] = 3"), ("f_odd", "T[g8] <> 0")):
        sel = [v for v, s in zip(data["r"], masks[pid]) if s]
        probes[pid] = (f"CALCULATE(SUM(T[r]), {flt})", math.floor(math.fsum(sel)))
    got = _batch({pid: f"{e} - {c}" for pid, (e, c) in probes.items()}, {"T": t})
    for pid, want in B243_RANDOM[n].items():
        assert _g15(got[pid]) == float(want), f"{pid}_{n}: {got[pid]!r}, Desktop {want}"


# ---- hand-made layouts: one part of the rule each -------------------------------

def _layout(n: int, columns: dict, segments: tuple | None = None) -> dict:
    """A table's storage: columns = {name: [(start, is_rle), ...]}."""
    return {"rows": n, "segments": segments or (n,),
            "columns": {c: {"starts": array("q", [s for s, _r in runs]),
                            "rle": bytes(1 if r else 0 for _s, r in runs), "enc": "hash", "mag": 1.0}
                        for c, runs in columns.items()}}


def test_an_rle_run_adds_value_times_rows():
    # Agents a_run_0 (2999.9 x 518 once, 0.199999999953434) and x_run_mul
    # (SalesAmount * 1.1 over that run: expr(v) x k, 0.0200000002514571):
    # here 0.7 x 10 once, where ten additions differ, and (0.7 x 1.1) x 10,
    # which is neither ten additions nor (0.7 x 10) x 1.1
    t = {"columns": ["v"], "rows": [[0.7]] * 10, "storage": _layout(10, {"v": [(0, True)]})}
    got = _batch({"s": "SUM(T[v])", "x": "SUMX(T, T[v])", "m": "SUMX(T, T[v] * 1.1)",
                  "a": "AVERAGE(T[v])"}, {"T": t})
    assert got["s"] == got["x"] == 0.7 * 10 != _fold([0.7] * 10)
    assert got["m"] == (0.7 * 1.1) * 10
    assert got["m"] != _fold([0.7 * 1.1] * 10) and got["m"] != (0.7 * 10) * 1.1
    assert got["a"] == (0.7 * 10) / 10


def _big_first(n: int) -> list:
    rows = [[1.0, 0] for _ in range(n)]
    rows[0][0] = BIG
    return rows


def test_a_filter_column_cuts_the_pieces():
    # Agents af_sk_all 0.215399503707886 (all rows through a filter on
    # StoreKey's 171 RLE runs, not a_total's 0.215398073196411), af_dimstore
    # through the relationship, x_filter through FILTER: here f has a run
    # boundary at 10000, so the bit-packed v starts a new chunk there
    n = 20000
    t = {"columns": ["v", "f"], "rows": _big_first(n),
         "storage": _layout(n, {"v": [(0, False)], "f": [(0, True), (10000, True)]})}
    got = _batch({"plain": f"SUM(T[v]) - {int(BIG)}",
                  "cut": f"CALCULATE(SUM(T[v]), T[f] = 0) - {int(BIG)}",
                  "filter": f"SUMX(FILTER(T, T[f] = 0), T[v]) - {int(BIG)}"}, {"T": t})
    # uncut: chunk [0, 16384) keeps 2**54 (each 1.0 rounds away), [16384, 20000) 3616
    assert got["plain"] == 3616
    # cut at 10000: [0, 10000) 2**54, [10000, 20000) 10000
    assert got["cut"] == got["filter"] == 10000


def test_values_and_positions_are_read_in_one_pass():
    # a sum reads the visible values with their storage positions
    # (DAXContext.get_column_data_at): the filters' row sets are built once
    # for both, and the answers are get_column_data's and get_row_positions'
    rows = [[i % 4, float(i) / 4] for i in range(12)]
    tables = {"T": {"columns": ["k", "v"], "rows": rows}}
    ctx = de.DAXContext(tables, {}, filter_context={"T.k": [1, 3]})
    calls = []
    real = ctx._surviving_indices

    def counted(*a):
        calls.append(a[0])
        return real(*a)
    ctx._surviving_indices = counted
    vals, pos = ctx.get_column_data_at("T", "v")
    assert pos == [1, 3, 5, 7, 9, 11]
    assert vals == [0.25, 0.75, 1.25, 1.75, 2.25, 2.75]
    assert ctx.get_column_data("T", "v") is vals and ctx.get_row_positions("T") is pos
    assert ctx.get_column_data_at("t", "V") == (vals, pos)       # names in another case (#226)
    assert ctx.get_column_data_at("T", "nope") == ([], pos)
    assert calls == ["T", "T"]                                   # "T" once, then "t"
    every = de.DAXContext(tables, {})
    assert every.get_column_data_at("T", "k") == ([r[0] for r in rows], None)


def test_a_filter_through_a_relationship_cuts_at_the_join_column():
    n = 20000
    t = {"columns": ["v", "f"], "rows": _big_first(n),
         "storage": _layout(n, {"v": [(0, False)], "f": [(0, True), (10000, True)]})}
    d = {"columns": ["f", "name"], "rows": [[0, "zero"]]}
    rels = [{"FromTable": "T", "FromColumn": "f", "ToTable": "D", "ToColumn": "f", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    got = _batch({"m": f"CALCULATE(SUM(T[v]), D[name] = \"zero\") - {int(BIG)}"},
                 {"T": t, "D": d}, relationships=rels)
    assert got["m"] == 10000


def test_a_value_encoded_column_adds_its_integers():
    # Root Cause rc_sum_profit 0.0216999999829568, rc_avg_profit, rc_sumx_profit
    # (DictionaryStorage.Type 2, Magnitude 10000): the integers added exactly
    # and divided once; rc_sumx_mul 0.723870001267642: an expression over the
    # column adds Doubles
    lay = _layout(10, {"v": [(0, False)]})
    lay["columns"]["v"]["enc"] = "value"
    lay["columns"]["v"]["mag"] = 10000.0
    t = {"columns": ["v"], "rows": [[0.1]] * 10, "storage": lay}
    got = _batch({"s": "SUM(T[v])", "x": "SUMX(T, T[v])", "a": "AVERAGE(T[v])",
                  "m": "SUMX(T, T[v] * 1.1)"}, {"T": t})
    assert got["s"] == got["x"] == 10000 / 10000.0 == 1.0
    assert got["a"] == 1.0 / 10
    assert got["m"] == _fold([0.1 * 1.1] * 10)


def test_sumx_of_an_expression_adds_over_the_pieces():
    # Agents a_profit 0.985398292541504 (SUMX(SA - TC)), x_mul / x_div /
    # x_addc / x_if: per row in IEEE, then the scan's chunks
    n = 20000
    rows = [[1.0, 0.5] for _ in range(n)]
    rows[0][0] = BIG
    t = {"columns": ["a", "b"], "rows": rows}
    got = _batch({"m": f"SUMX(T, T[a] - T[b]) - {int(BIG)}",
                  "i": f"SUMX(T, IF(T[a] > 2, T[a], T[a] - T[b])) - {int(BIG)}"}, {"T": t})
    # chunk [0, 16384): 2**54 - 0.5 = 2**54 (rounds to even), then 0.5s round away;
    # chunk [16384, 20000): 3616 x 0.5 = 1808
    assert got["m"] == got["i"] == 1808


def test_an_expression_around_one_column_is_no_bare_column():
    # SUMX of 1 - T[v] or -T[v] evaluates the expression per row; only T[v]
    # itself is read straight off the rows (exact IEEE arithmetic: the fold)
    vals = [0.1, 0.2, 0.3]
    t = {"columns": ["v"], "rows": [[v] for v in vals]}
    got = _batch({"a": "SUMX(T, 1 - T[v])", "b": "SUMX(T, -T[v])", "c": "SUMX(T, (T[v]))",
                  "d": "AVERAGEX(T, 2 * [v])"}, {"T": t})
    assert got["a"] == _fold([1 - v for v in vals])
    assert got["b"] == _fold([-v for v in vals])
    assert got["c"] == _fold(vals)
    assert got["d"] == _fold([2 * v for v in vals]) / 3


# ---- (d) the builder's layout through the server --------------------------------

def test_the_builder_writes_one_bit_packed_run(tmp_path):
    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    warnings.simplefilter("ignore")
    path = str(tmp_path / "b243.pbix")
    b = PBIXBuilder("b243")
    b.add_table("TDbl", [{"name": "v", "data_type": "Double"}], rows=[{"v": 0.1}] * 10)
    b.add_measure("TDbl", "S", "SUM(TDbl[v])")
    b.add_page("Page 1")
    b.save(path)
    assert json.loads(S.pbix_open(path, "i243"))["success"]
    try:
        out = json.loads(S.pbix_evaluate_dax("i243", "S", apply_default_filters=False))
        ctx = S._get_dax_context("i243")
    finally:
        S.pbix_close("i243")
    col = ctx["tables"]["TDbl"]["storage"]["columns"]["v"]
    assert list(col["starts"]) == [0] and col["rle"] == b"\x00"
    assert out["results"][0]["value"] == 0.9999999999999999        # Desktop, b210 T10


# ---- (e) the decoder's runs ------------------------------------------------------

def _idf_segment(entries: list, words: list) -> bytes:
    """One IDF segment: 16 primary (value, repeat) slots, then the words."""
    slots = entries + [(0, 0)] * (16 - len(entries))
    out = struct.pack("<Q", 16) + b"".join(struct.pack("<II", dv, rv) for dv, rv in slots)
    return out + struct.pack("<Q", len(words)) + b"".join(struct.pack("<Q", w) for w in words)


def test_the_decoder_hands_over_the_runs():
    # RLE (5, x3), bit-packed x5 (marker 0xFFFFFFFF), RLE (7, x2); 8-bit values
    seg = _idf_segment([(5, 3), (0xFFFFFFFF, 5), (7, 2)], [0x0504030201])
    runs: list = []
    idx = vd.decode_idf(seg, 8, 10, runs_out=runs)
    assert idx == [5, 5, 5, 1, 2, 3, 4, 5, 7, 7]
    assert runs == [(0, 3, True), (3, 5, False), (8, 2, True)]
    # two segments: the second's runs start after the first's rows
    seg2 = _idf_segment([(0xFFFFFFFF, 4)], [0x04030201])
    runs = []
    vd.decode_idf(seg + seg2, 8, 14, segments=[(8, 0, 0, 10), (8, 0, 0, 4)], runs_out=runs)
    assert runs == [(0, 3, True), (3, 5, False), (8, 2, True), (10, 4, False)]


def test_layouts_that_do_not_cover_the_rows_are_left_out():
    runs = {"a": ([(0, 3, True), (3, 2, False)], "hash", 1.0), "b": ([(0, 4, False)], "hash", 1.0)}
    st = vd._storage_layout(runs, ["a", "b"], 5, None)
    assert set(st["columns"]) == {"a"} and st["segments"] == (5,)
    assert list(st["columns"]["a"]["starts"]) == [0, 3] and st["columns"]["a"]["rle"] == b"\x01\x00"


# ---- (g) a running total over rows that do not add exactly ----------------------

def test_a_running_total_replays_the_scan(monkeypatch):
    days = list(range(1, 41))
    rnd = random.Random(243)
    n = 40000
    rows = [[days[i * len(days) // n], rnd.random() * 10 ** rnd.randint(-2, 6)] for i in range(n)]
    tables = {"Cal": {"columns": ["Day"], "rows": [[d] for d in days]},
              "F": {"columns": ["Day", "Amt"], "rows": rows}}
    rels = [{"FromTable": "F", "FromColumn": "Day", "ToTable": "Cal", "ToColumn": "Day", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"RT": "CALCULATE(SUM(F[Amt]), Cal[Day] <= MAX(Cal[Day]))"}
    # the totals built on the second request (the first is always a scan)
    monkeypatch.setattr(de.DAXEngine, "_range_pays", staticmethod(lambda *a, **k: True))
    de._SHARED_FILTER_CACHES.clear()
    try:
        got = [de.evaluate_measure("RT", tables, ms, {"Cal.Day": [d]}, relationships=rels) for d in days]
        built = [k for k in de._shared_filter_cache(tables)
                 if isinstance(k, tuple) and len(k) > 1 and k[1] == "range-partials"]
    finally:
        de._SHARED_FILTER_CACHES.clear()
    assert built
    # each day's total is the plain sum over the days up to it: the rows of a
    # window lie first in storage order, one bit-packed run, chunks of 16384
    for d, g in zip(days, got):
        sel = [r[1] for r in rows if r[0] <= d]
        want = 0.0
        for c in range(0, len(sel), 16384):
            want += _fold(sel[c:c + 16384])
        assert g == want, d


# ---- (h) evaluate_per_dimension's buckets keep the rows' places -----------------

def test_buckets_add_as_the_per_value_path():
    n = 40000
    rows = [[1.0, i % 2] for i in range(n)]
    rows[0][0] = BIG
    rows[1][0] = BIG
    t = {"columns": ["v", "g"], "rows": rows, "storage": _layout(n, {"v": [(0, False)], "g": [(0, False)]})}
    tables = {"T": t}
    ms = {"S": "SUM(T[v])"}
    fast = de.evaluate_per_dimension(["S"], tables, ms, {}, "T.g", "T", "g", [0, 1])
    for g in (0, 1):
        per_value = de.evaluate_measures_batch(["S"], tables, ms, {"T.g": [g]}, group_keys={"T.g"})["S"]
        assert fast["S"][g] == per_value
        # grouped, the group column bit-packed: every row added on its own
        assert per_value == BIG


# ---- (f) the Desktop-saved files themselves --------------------------------------

def _server_eval(path: str, alias: str, exprs: dict) -> dict:
    from pbix_mcp import server as S
    warnings.simplefilter("ignore")
    assert json.loads(S.pbix_open(path, alias))["success"]
    try:
        ctx = S._get_dax_context(alias)
        for k, e in exprs.items():
            ctx["measure_defs"]["__" + k] = e
        out = json.loads(S.pbix_evaluate_dax(alias, ",".join("__" + k for k in exprs),
                                             apply_default_filters=False))
    finally:
        S.pbix_close(alias)
    return {x["name"][2:]: x.get("value") for x in out["results"]}


@pytest.mark.skipif(not os.path.exists(AGENTS), reason="community corpus file not present")
def test_agents_desktop_answers():
    # build_b243.py / build_b243b.py on the Desktop-saved Agents file
    exprs = {
        "a_total": ("SUM(FactSales[SalesAmount]) - 729782919", "0.215398073196411"),
        "a_total_sumx": ("SUMX(FactSales, FactSales[SalesAmount]) - 729782919", "0.215398073196411"),
        "a_profit": ("[Profit] - 414617394", "0.985398292541504"),
        "a_disc": ("SUM(FactSales[DiscountAmount]) - 12056352", "0.896599821746349"),
        "af_sk_all": ("CALCULATE(SUM(FactSales[SalesAmount]), FactSales[StoreKey] <> 0) - 729782919",
                      "0.215399503707886"),
        "af_dimstore": ("CALCULATE(SUM(FactSales[SalesAmount]), DimStore[StoreKey] <> 0) - 729782919",
                        "0.215399503707886"),
        "x_if": ("SUMX(FactSales, IF(FactSales[SalesAmount] > 1000, FactSales[SalesAmount], 0)) - 702047819",
                 "0.865999579429626"),
        "av_sa": ("AVERAGE(FactSales[SalesAmount]) - 3648", "0.932840741194013"),
    }
    got = _server_eval(AGENTS, "i243ag", {k: e for k, (e, _w) in exprs.items()})
    for k, (_e, want) in exprs.items():
        assert _g15(got[k]) == float(want), f"{k}: {got[k]!r}, Desktop {want}"


@pytest.mark.skipif(not os.path.exists(ROOT_CAUSE), reason="community corpus file not present")
def test_root_cause_desktop_answers():
    exprs = {
        "rc_sum_profit": ("SUM(fct_Orders[Profit]) - 286397", "0.0216999999829568"),
        "rc_sum_discount": ("SUM(fct_Orders[Discount]) - 1561", "0.0900000000017371"),
        "rc_avg_discount": ("AVERAGE(fct_Orders[Discount])", "0.15620272163298"),
        "rc_sumx_profit": ("SUMX(fct_Orders, fct_Orders[Profit]) - 286397", "0.0216999999829568"),
    }
    got = _server_eval(ROOT_CAUSE, "i243rc", {k: e for k, (e, _w) in exprs.items()})
    for k, (_e, want) in exprs.items():
        assert _g15(got[k]) == float(want), f"{k}: {got[k]!r}, Desktop {want}"
