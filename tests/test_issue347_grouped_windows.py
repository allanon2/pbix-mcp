"""Issue #347: a query's grouped key as the running-total route's
window, comparisons on the fact table's own column kept in the base, a
grouped batch telling the route how many rows are to come, the scan's replay
made fast, and VAR blocks split once. The ideas of PR #253 by @allanon2,
re-implemented against 0.9.148 (the PR's code not used: it was written
against 0.9.147, whose correctly rounded Double totals are no longer the
scan's answer, issue #243).

What was slow (the pr253 benchmark: a running total and an income by date
over 4,017 dates, through pbix_evaluate_dax_batch): 7.2 s over 7,500 fact
rows of Doubles and 86 s over 100,000. Three causes, each measured:

- a base's totals waited for its scans to pay for them twenty times over
  (_range_pays): the running total built them after about 3,700 of the 4,017
  rows, and the income -- a comparison on the fact's own column under the
  date grouping -- never took the route at all;
- once built, a total of Doubles that do not add exactly replayed the scan
  over the window by comparing every kept row's position, 1.6 ms a window
  over 7,500 rows: slower than the scan it replaced;
- a VAR block was split again for every row (5% of a VAR running total).

Where the expected values come from: the engine's own plain path -- the
aggregate computed by the scan (_aggregate_scan) with the route switched
off, on a FRESH model cache for every cell -- never a cache shared with the
run under test. Every route answer must be the same value of the same type,
bit for bit, with the cells evaluated in order and reversed, twice each
(#231's differential). The plain scan itself is 0.9.148's: Desktop's storage
engine's order (#243: chunks of 16,384 rows, RLE products, segments, filter
cuts, fused scans; #340: grouped scans add row by row in a group-bit-packed
piece). Here SE_CHUNK is made 4 so that small random models cross chunk
boundaries, on both sides of the comparison.

Found on the way and fixed: the running-total route's replay added an RLE
piece longer than a chunk as ONE product, where the scan adds one per chunk
(#346): 0.1 x 3,960 rows over three chunks gave 396.0 where the scan gives
396.00000000000006 (#350).

Each defect a route could have is planted, and the differential must report
it.
"""
from __future__ import annotations

import contextlib
import json
import random
import threading
from array import array
from datetime import datetime, timedelta

import pytest

from pbix_mcp import server
from pbix_mcp.dax import engine as de
from pbix_mcp.dax import se_scan

pytestmark = pytest.mark.unit

FIRST = datetime(2024, 1, 1)


# ---- the plain path and the differential -----------------------------------------

def _plain_aggregate(self, kind, col, ctx, compute):
    return self._aggregate_scan(kind, col, self._agg_ctx(ctx), compute)


@contextlib.contextmanager
def _plain_path():
    """The aggregates by the scan alone: no memo, no route."""
    real = de.DAXEngine._column_aggregate
    de.DAXEngine._column_aggregate = _plain_aggregate
    try:
        yield
    finally:
        de.DAXEngine._column_aggregate = real


ROWS_TO_COME = 10_000


def _ev(name, tables, measures, fc, rels, group_by, pending: int = ROWS_TO_COME):
    """One measure in one context, as an answer to compare: inside a grouped
    batch (``pending`` rows of the visual to come), where a grouped key may
    be the route's window; 0 for a lone call."""
    try:
        with de.grouped_rows_pending(pending):
            got = de.evaluate_measures_smart([name], tables, measures, dict(fc), relationships=rels,
                                             group_by=set(group_by) if group_by else None,
                                             simulate_row_context=False)
        v = got[name]
    except Exception as e:  # noqa: BLE001 -- an error is an answer to compare too
        return ("error", type(e).__name__)
    return (type(v).__name__, repr(v))


def _differential(tables, measures, rels, cells, repeat: int = 2) -> list:
    """Mismatches between the plain path on a fresh cache per cell and the
    engine on one shared cache, the cells in order and reversed, each order
    ``repeat`` times. ``cells``: (measure, filter context, grouped keys)."""
    want = []
    with _plain_path():
        for name, fc, gb in cells:
            de._SHARED_FILTER_CACHES.clear()
            want.append(_ev(name, tables, measures, fc, rels, gb))
    bad = []
    for order in (list(range(len(cells))), list(reversed(range(len(cells))))):
        de._SHARED_FILTER_CACHES.clear()
        for _ in range(repeat):
            for i in order:
                name, fc, gb = cells[i]
                got = _ev(name, tables, measures, fc, rels, gb)
                if got != want[i]:
                    bad.append((name, fc, gb, got, want[i]))
    de._SHARED_FILTER_CACHES.clear()
    return bad


@pytest.fixture
def eager(monkeypatch):
    """Totals built on a base's second request whatever its scans cost."""
    monkeypatch.setattr(de, "_RANGE_BUILD_FACTOR", 0.0)


@pytest.fixture
def small_chunks(monkeypatch):
    """Chunks of 4 rows, so that small tables cross chunk boundaries (both
    the scan and the route read se_scan.SE_CHUNK when they run)."""
    monkeypatch.setattr(se_scan, "SE_CHUNK", 4)


@pytest.fixture
def keyed(monkeypatch):
    """How many answers a grouped key read off the totals (answer_key)."""
    count = [0]
    real = de._RangePartials.answer_key

    def spy(self, *a, **k):
        count[0] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(de._RangePartials, "answer_key", spy)
    return count


def _layout(rows: list, columns: list, r: random.Random) -> dict:
    """A random storage layout that tells the truth about the rows: 1 to 3
    segments; each column's runs cut at random, a run of equal values marked
    RLE half of the time."""
    n = len(rows)
    cuts = sorted(r.sample(range(1, n), min(n - 1, r.randint(0, 2)))) if n > 1 else []
    segments = tuple(b - a for a, b in zip([0] + cuts, cuts + [n]))
    out = {}
    for ci, name in enumerate(columns):
        starts, rle = [], []
        i = 0
        while i < n:
            j = i + 1
            while j < n and rows[j][ci] == rows[i][ci] and type(rows[j][ci]) is type(rows[i][ci]):
                j += 1
            if j - i >= 2 and r.random() < 0.5:
                starts.append(i)
                rle.append(1)
                i = j
            else:
                stop = min(n, i + r.randint(1, 6))
                starts.append(i)
                rle.append(0)
                i = stop
        out[name] = {"starts": array("q", starts), "rle": bytes(rle), "enc": "hash", "mag": 1.0}
    return {"rows": n, "segments": segments, "columns": out}


# ---- the random models -------------------------------------------------------------

MEASURES = {
    "S": "SUM(F[Amt])", "A": "AVERAGE(F[Amt])", "C": "COUNT(F[Amt])", "CA": "COUNTA(F[Amt])",
    "SQ": "SUM(F[Q])", "AQ": "AVERAGE(F[Q])",
    # a comparison on the fact's own column, in the base (P2)
    "Own": "CALCULATE(SUM(F[Amt]), F[Q] > 3)",
    "OwnAvg": 'CALCULATE(AVERAGE(F[Amt]), F[Q] <= 5, D[Cat] = "a")',
    "OwnRT": "CALCULATE(SUM(F[Amt]), F[Q] >= 2, Cal[Date] <= MAX(Cal[Date]))",
    "RT": "CALCULATE(SUM(F[Amt]), Cal[Date] <= MAX(Cal[Date]))",
    "RTX": "CALCULATE(COUNT(F[Amt]), D[X] > MIN(D[X]))",
    "UR": "CALCULATE(SUM(F[Amt]), USERELATIONSHIP(F[Ship], Cal[Date]))",
    "CF": "CALCULATE(SUM(F[Amt]), CROSSFILTER(F[DKey], D[Key], BOTH))",
    "PerCat": "SUMX(VALUES(D[Cat]), CALCULATE(SUM(F[Amt])))",
    "PerRow": "SUMX(F, CALCULATE(SUM(F[Q])))",
    "AllD": "CALCULATE(SUM(F[Amt]), ALL(D))",
    "Tup": "CALCULATE(SUM(F[Amt]), FILTER(ALL(D[Key], D[Cat]), D[Cat] <> \"b\"))",
}


def _model(seed: int):
    r = random.Random(25300 + seed)
    days = [FIRST + timedelta(days=i) for i in range(r.randint(5, 10))]
    keytype = ["int", "float", "str"][seed % 3]
    n = r.randint(3, 6)
    keys = {"int": list(range(1, n + 1)), "float": [float(k) for k in range(1, n + 1)],
            "str": [f"k{k}" for k in range(1, n + 1)]}[keytype]
    xkind = ["int", "float", "date", "blank"][seed % 4]
    xs = {"int": [0, 1, 2, 2, 3, 5], "float": [0.5, 2.5, 3.0, 3, -1.0],
          # a date and its own serial share a position, not a match
          "date": [days[0], days[1], days[1], 45293, days[2]],
          "blank": [None, 1, 2, 2, 5]}[xkind]
    d_rows = [[k, r.choice("abc"), r.choice(xs)] for k in keys]
    amt_kind = ["kernel", "int", "currency", "value", "exact", "dirty"][(seed // 2) % 6]
    fkeys = keys + ([99, None] if r.random() < 0.4 else [])
    rows = []
    for _ in range(r.randint(18, 48)):
        amt = {"kernel": lambda: r.choice([r.random() * 10 ** r.randint(-3, 6), 2.0 ** 53, 0.1, 1.0]),
               "int": lambda: r.randint(-50, 2 ** 20),
               "currency": lambda: round(r.uniform(-99, 999), r.randint(0, 4)),
               "value": lambda: round(r.uniform(-99, 999), 2),
               "exact": lambda: r.choice([0.5, 0.25, 1.0, 3.0, -2.5]),
               "dirty": lambda: r.choice([0.1, 0.2, None, "n/a", True, 3.0, 0.7])}[amt_kind]()
        when = r.choice(days)
        if seed % 5 == 4 and r.random() < 0.3:
            when = when + timedelta(hours=12, minutes=30)     # a time of day: date-part joins
        rows.append([r.choice(fkeys), when, amt, r.choice([r.randint(0, 9), None]), r.choice(days)])
    if seed % 2:
        rows.sort(key=lambda row: row[1])
    # runs of equal amounts, which RLE runs can hold
    for prev, row in zip(rows, rows[1:]):
        if r.random() < 0.3:      # three in ten repeat the amount before
            row[2] = prev[2]
    cols = ["DKey", "When", "Amt", "Q", "Ship"]
    f = {"columns": cols, "rows": rows}
    if amt_kind != "int":
        f["storage"] = _layout(rows, cols, r)
        if amt_kind == "value":
            f["storage"]["columns"]["Amt"]["enc"] = "value"
            f["storage"]["columns"]["Amt"]["mag"] = 100.0
    tables = {"D": {"columns": ["Key", "Cat", "X"], "rows": d_rows},
              "Cal": {"columns": ["Date", "Year"], "rows": [[d, d.year + (i % 2)] for i, d in enumerate(days)]},
              "F": f}
    if amt_kind == "currency":
        de.mark_currency_columns(tables, {"F": ["Amt"]})
    card = [(2, 1), (2, 1), (1, 1), (2, 2), (2, 1)][seed % 5]
    rels = [{"FromTable": "F", "FromColumn": "DKey", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
             "FromCardinality": card[0], "ToCardinality": card[1],
             "CrossFilteringBehavior": 2 if seed % 3 == 1 else 1},
            {"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1, "CrossFilteringBehavior": 1,
             **({"JoinOnDateBehavior": 2} if seed % 5 == 4 else {})},
            {"FromTable": "F", "FromColumn": "Ship", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 0,
             "FromCardinality": 2, "ToCardinality": 1, "CrossFilteringBehavior": 1}]
    xvals = list(dict.fromkeys(row[2] for row in d_rows))
    contexts = [({"Cal.Date": [d]}, ["Cal.Date"]) for d in days]
    contexts += [({"D.X": [x]}, ["D.X"]) for x in xvals]
    contexts += [({"D.X": [99]}, ["D.X"])]
    contexts += [({"Cal.Date": [d], "D.Cat": ["a", "b"]}, ["Cal.Date"]) for d in days[::2]]
    contexts += [({"D.X": [x], "Cal.Year": [days[0].year]}, ["D.X"]) for x in xvals[:3]]
    contexts += [({"D.X": [xvals[0]], "D.Cat": ["a"]}, ["D.X", "D.Cat"]),
                 ({"Cal.Date": [days[1]], "D.X": [xvals[-1]]}, ["Cal.Date", "D.X"]),
                 ({"F.Q": [3]}, ["F.Q"]),
                 ({"Cal.Date": [days[2]], "F.Q": {"op": ">", "value": 2}}, ["Cal.Date"])]
    return tables, rels, contexts


SEEDS = range(20)


@pytest.mark.parametrize("seed", SEEDS)
def test_differential_on_random_models(seed, eager, small_chunks):
    tables, rels, contexts = _model(seed)
    cells = [(m, fc, gb) for fc, gb in contexts for m in MEASURES]
    assert _differential(tables, MEASURES, rels, cells) == []


def test_the_differential_reaches_the_route(eager, small_chunks, keyed):
    """Not vacuous: the random models' grouped rows are read off totals, in
    every kind of column the models hold."""
    modes = set()
    real = de._RangePartials.answer_key

    def note(self, *a, **k):
        modes.add(self.mode)
        return real(self, *a, **k)

    de._RangePartials.answer_key = note
    try:
        for seed in SEEDS:
            tables, rels, contexts = _model(seed)
            cells = [(m, fc, gb) for fc, gb in contexts for m in ("S", "A", "C", "Own")]
            _differential(tables, MEASURES, rels, cells, repeat=1)
    finally:
        de._RangePartials.answer_key = real
    assert {"kernel", "int", "currency", "value", "float"} <= modes


# ---- planted defects: the differential must see each -------------------------------

def _rle_model():
    """F's amounts one RLE run of 0.1 over 40 rows (ten chunks of 4), the
    days bit-packed in random order: a window's rows of the run fall in
    several chunks."""
    days = [FIRST + timedelta(days=i) for i in range(8)]
    r = random.Random(346)
    n = 40
    rows = [[r.choice(days), 0.1] for _ in range(n)]
    lay = {"rows": n, "segments": (n,),
           "columns": {"Amt": {"starts": array("q", [0]), "rle": b"\x01", "enc": "hash", "mag": 1.0},
                       "When": {"starts": array("q", [0]), "rle": b"\x00", "enc": "hash", "mag": 1.0}}}
    tables = {"Cal": {"columns": ["Date"], "rows": [[d] for d in days]},
              "F": {"columns": ["When", "Amt"], "rows": rows, "storage": lay}}
    rels = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"RT": "CALCULATE(SUM(F[Amt]), Cal[Date] <= MAX(Cal[Date]))", "S": "SUM(F[Amt])"}
    cells = [(m, {"Cal.Date": [d]}, ["Cal.Date"]) for d in days for m in ms]
    return tables, ms, rels, cells


def _grouped_vs_cut_model():
    """2**53 then ones, one bit-packed run: in chunks of 4 the ones after
    2**53 are lost within chunk 0 and kept as a chunk total after it, while
    a grouped scan adds every row to the group's accumulator and loses them
    all -- so a date's total (a grouped scan) and a running total's window
    (a cut scan) over the same base differ."""
    days = [FIRST + timedelta(days=i) for i in range(3)]
    rows = [[days[0], 2.0 ** 53]] + [[days[i % 2], 1.0] for i in range(1, 12)]
    lay = {"rows": 12, "segments": (12,),
           "columns": {"Amt": {"starts": array("q", [0]), "rle": b"\x00", "enc": "hash", "mag": 1.0},
                       "When": {"starts": array("q", [0]), "rle": b"\x00", "enc": "hash", "mag": 1.0}}}
    tables = {"Cal": {"columns": ["Date"], "rows": [[d] for d in days]},
              "F": {"columns": ["When", "Amt"], "rows": rows, "storage": lay}}
    rels = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"S": f"SUM(F[Amt]) - {2 ** 53}", "RT": f"CALCULATE(SUM(F[Amt]), Cal[Date] <= MAX(Cal[Date])) - {2 ** 53}"}
    cells = [(m, {"Cal.Date": [d]}, ["Cal.Date"]) for d in days[:2] for m in ("RT", "S")]
    return tables, ms, rels, cells


def _serial_model():
    """D[X] holds a date and its serial number in two rows: the grouped key
    of the date keeps one of the two rows of its position."""
    days = [FIRST + timedelta(days=i) for i in range(3)]
    serial = de._dax_serial(days[1])
    d_rows = [[1, days[0]], [2, days[1]], [3, serial], [4, days[2]]]
    rows = [[k, float(k) * 1.5 + 0.1 * j] for j in range(3) for k in (1, 2, 3, 4)]
    tables = {"D": {"columns": ["Key", "X"], "rows": d_rows},
              "F": {"columns": ["DKey", "Amt"], "rows": rows}}
    rels = [{"FromTable": "F", "FromColumn": "DKey", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"Total": "SUM(F[Amt])", "Rows": "COUNT(F[Amt])"}
    cells = [(m, {"D.X": [x]}, ["D.X"]) for x in (days[0], days[1], serial, days[2]) for m in ms]
    return tables, ms, rels, cells


def _blank_group_model():
    """BLANK x's and the fact rows of no D row: a grouped BLANK keeps them."""
    d_rows = [[1, None], [2, 1], [3, 2], [4, None]]
    rows = [[k, (k or 7) * 10 + j] for j in range(3) for k in (1, 2, 3, 4, 99, None)]
    tables = {"D": {"columns": ["Key", "X"], "rows": d_rows},
              "F": {"columns": ["DKey", "Amt"], "rows": rows}}
    rels = [{"FromTable": "F", "FromColumn": "DKey", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"S": "SUM(F[Amt])", "C": "COUNTA(F[Amt])"}
    cells = [(m, {"D.X": [x]}, ["D.X"]) for x in (1, None, 2) for m in ms]
    return tables, ms, rels, cells


def _own_model():
    """A comparison on F's own column beside the date grouping and a
    running total (P2)."""
    days = [FIRST + timedelta(days=i) for i in range(6)]
    r = random.Random(2532)
    fact = {"columns": ["When", "Amt", "Q"],
            "rows": [[r.choice(days), r.randint(-40, 90), r.randint(0, 9)] for _ in range(40)]}
    tables = {"Cal": {"columns": ["Date"], "rows": [[d] for d in days]}, "F": fact}
    rels = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"Inc": "CALCULATE(SUM(F[Amt]), F[Amt] > 0, F[Q] >= 3)",
          "RT": "CALCULATE(SUM(F[Amt]), F[Amt] > 0, Cal[Date] <= MAX(Cal[Date]))"}
    cells = [(m, {"Cal.Date": [d]}, ["Cal.Date"]) for d in days for m in ms]
    return tables, ms, rels, cells


def _kernel_window_model():
    """Doubles that do not add exactly, windows growing and shrinking: a
    running total and its reverse ("after") over the same chunks."""
    days = [FIRST + timedelta(days=i) for i in range(8)]
    r = random.Random(2533)
    rows = [[r.choice(days), r.random() * 10 ** r.randint(-3, 6)] for _ in range(30)]
    tables = {"Cal": {"columns": ["Date"], "rows": [[d] for d in days]},
              "F": {"columns": ["When", "Amt"], "rows": rows}}
    rels = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"RT": "CALCULATE(SUM(F[Amt]), Cal[Date] <= MAX(Cal[Date]))",
          "Aft": "CALCULATE(SUM(F[Amt]), Cal[Date] > MAX(Cal[Date]))",
          "S": "SUM(F[Amt])"}
    cells = [(m, {"Cal.Date": [d]}, ["Cal.Date"]) for d in days for m in ms]
    return tables, ms, rels, cells


def _one_product_per_piece(mp):
    """0.9.148's replay: an RLE piece as one product, however many chunks."""
    real = de._RangePartials._kernel_terms

    def terms(pos, vals, se):
        out = []
        for t in real(pos, vals, se):
            if out and t[0] == "r" and out[-1][0] == "r" and out[-1][1] == t[1]:
                aps = sorted(out[-1][2] + t[2])
                out[-1] = ("r", t[1], aps, sum(1 for x in aps if x < 0))
            else:
                out.append(t)
        return out
    mp.setattr(de._RangePartials, "_kernel_terms", staticmethod(terms))


def _layout_not_in_key(mp):
    """The totals' key without the scans' layout: a grouped key and a
    window share a base's totals."""
    mp.setattr(de.DAXEngine, "_range_layout_key", staticmethod(lambda ctx, tname, tbl: None))


def _members_ignored(mp):
    """The axis's row counts made to agree with whatever a value keeps: a
    value holding some of its position's D rows passes for all of them."""
    real = de.DAXEngine._range_key_position

    def lenient(ctx, dtbl, x_idx, where, value):
        kept = ctx._column_filter_rows(dtbl, x_idx, value)
        saved = list(where.members)
        for spot in {where.pos[j] for j in kept if where.pos[j] >= 0}:
            where.members[spot] = len(kept)
        try:
            return real(ctx, dtbl, x_idx, where, value)
        finally:
            where.members[:] = saved
    mp.setattr(de.DAXEngine, "_range_key_position", staticmethod(lenient))


def _neighbour_included(mp):
    """A key's answer taken over its position and the next one."""
    def two_keys(self, kind, p, totals=None):
        return self.answer(kind, p, min(p + 2, len(self.keys)), False)
    mp.setattr(de._RangePartials, "answer_key", two_keys)


def _blank_key_admitted(mp):
    """A BLANK in the grouped value set read as a value keeping nothing."""
    keep = de.DAXEngine._range_key_position

    def accept_all(ctx, dtbl, x_idx, value):
        return bool(value)

    def nothing_for_blank(ctx, dtbl, x_idx, where, value):
        return -1 if None in value else keep(ctx, dtbl, x_idx, where, value)
    mp.setattr(de.DAXEngine, "_range_key_spec", staticmethod(accept_all))
    mp.setattr(de.DAXEngine, "_range_key_position", staticmethod(nothing_for_blank))


def _key_totals_folded_whole(mp):
    """Each key's 'kernel' total as one fold of its values in storage order,
    chunks, RLE products and segments ignored."""
    def totals(terms, n):
        per: dict = {}
        for t in terms:
            if t[0] == "r":
                for p in t[2]:
                    if p >= 0:
                        per.setdefault(p, []).append(t[1])
            elif t[0] in ("c", "g"):
                for v, p in zip(t[1], t[2]):
                    if p >= 0:
                        per.setdefault(p, []).append(v)
        return [se_scan.seq_sum(per[p]) if p in per else None for p in range(n)]
    mp.setattr(de._RangePartials, "_kernel_key_totals", staticmethod(totals))


def _own_filters_dropped(mp):
    """The base read without the comparisons on F's own column."""
    real = de.DAXEngine._range_partials

    def partials(ctx, tname, tbl, col_idx, rkey, dname, dtbl, owners, axis, nkey, layout=None):
        own = [k for k in ctx.filter_context if k.partition(".")[0] == tname]
        return real(ctx.without_filters(own), tname, tbl, col_idx, rkey, dname, dtbl, owners, axis, nkey,
                    layout)
    mp.setattr(de.DAXEngine, "_range_partials", staticmethod(partials))


def _masks_never_cleared(mp):
    """A kept mask whose rows are only ever added: a shrinking window keeps
    the rows of the larger one."""
    real = de._RangePartials._window_values

    def window(self, ti, cv, order, sp, lo, hi, blank):
        held = self.cursor.get(ti)
        if held is not None:
            a0, b0, blank0, old = held
            import bisect
            a, b = bisect.bisect_left(sp, lo), max(bisect.bisect_left(sp, lo), bisect.bisect_left(sp, hi))
            if a >= a0 and b <= b0 and (a, b) != (a0, b0):
                from itertools import compress
                return compress(cv, old), sum(old)
        return real(self, ti, cv, order, sp, lo, hi, blank)
    mp.setattr(de._RangePartials, "_window_values", window)


def _grouped_rle_model():
    """F sorted by date, each date one RLE run of F[When], the amounts
    bit-packed: a date's grouped scan adds its piece in chunks of 4 (rule
    2: a group-RLE piece adds as an ungrouped scan), so 2**53's ones are
    lost in its chunk and kept after it -- not one fold of the date's rows."""
    days = [FIRST + timedelta(days=i) for i in range(3)]
    rows = []
    for day in days:
        rows.append([day, 2.0 ** 53])
        rows.extend([day, 1.0] for _ in range(8))
    lay = {"rows": 27, "segments": (27,),
           "columns": {"Amt": {"starts": array("q", [0]), "rle": b"\x00", "enc": "hash", "mag": 1.0},
                       "When": {"starts": array("q", [0, 9, 18]), "rle": b"\x01\x01\x01",
                                "enc": "hash", "mag": 1.0}}}
    tables = {"Cal": {"columns": ["Date"], "rows": [[d] for d in days]},
              "F": {"columns": ["When", "Amt"], "rows": rows, "storage": lay}}
    rels = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"S": f"SUM(F[Amt]) - {2 ** 53}"}
    cells = [("S", {"Cal.Date": [d]}, ["Cal.Date"]) for d in days]
    return tables, ms, rels, cells


def _plant_cases():
    # (install, model, rows per chunk)
    return {
        "an RLE piece as one product (#346)": (_one_product_per_piece, _rle_model(), 4),
        "a grouped key and a window sharing totals": (_layout_not_in_key, _grouped_vs_cut_model(), 4),
        "a value's rows taken for all its position's": (_members_ignored, _serial_model(), 4),
        "a key read with its neighbour": (_neighbour_included, _kernel_window_model(), 4),
        "a BLANK key admitted": (_blank_key_admitted, _blank_group_model(), 4),
        "key totals folded whole": (_key_totals_folded_whole, _grouped_rle_model(), 4),
        "the fact's own filters dropped from the base": (_own_filters_dropped, _own_model(), 4),
        # the running total alone, so that the reversed order shrinks its
        # window from one row to the next
        "masks never cleared": (_masks_never_cleared, _only("RT", _kernel_window_model()), 16384),
    }


def _only(measure, model):
    tables, ms, rels, cells = model
    return tables, ms, rels, [c for c in cells if c[0] == measure]


PLANTS = list(_plant_cases())


@pytest.mark.parametrize("plant", PLANTS)
def test_planted_defects_are_seen(plant, monkeypatch, eager):
    install, (tables, ms, rels, cells), chunk = _plant_cases()[plant]
    monkeypatch.setattr(se_scan, "SE_CHUNK", chunk)
    assert _differential(tables, ms, rels, cells) == []
    install(monkeypatch)
    assert _differential(tables, ms, rels, cells) != [], plant


# ---- the fixes, pinned -----------------------------------------------------------

def _plain_answers(tables, ms, rels, cells):
    with _plain_path():
        out = []
        for name, fc, gb in cells:
            de._SHARED_FILTER_CACHES.clear()
            out.append(_ev(name, tables, ms, fc, rels, gb))
    return out


def test_a_long_rle_run_adds_a_product_per_chunk(eager):
    """#346 in the running-total route (#350): 40,000 rows of
    0.1, one RLE run, the days in random order -- every window's rows fall in
    three chunks of 16,384, and the route gives the scan's sum of three
    products (0.9.148 gave one product: 396.0 for 396.00000000000006)."""
    days = [FIRST + timedelta(days=i) for i in range(12)]
    r = random.Random(1)
    n = 40000
    rows = [[r.choice(days), 0.1] for _ in range(n)]
    lay = {"rows": n, "segments": (n,),
           "columns": {"Amt": {"starts": array("q", [0]), "rle": b"\x01", "enc": "hash", "mag": 1.0},
                       "When": {"starts": array("q", [0]), "rle": b"\x00", "enc": "hash", "mag": 1.0}}}
    tables = {"Cal": {"columns": ["Date"], "rows": [[d] for d in days]},
              "F": {"columns": ["When", "Amt"], "rows": rows, "storage": lay}}
    rels = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"RT": "CALCULATE(SUM(F[Amt]), Cal[Date] <= MAX(Cal[Date]))"}
    cells = [("RT", {"Cal.Date": [d]}, None) for d in days]
    want = _plain_answers(tables, ms, rels, cells)
    de._SHARED_FILTER_CACHES.clear()
    try:
        got = [_ev(*c[:1], tables, ms, c[1], rels, c[2]) for c in cells]
        built = [k for k in de._shared_filter_cache(tables)
                 if isinstance(k, tuple) and len(k) > 1 and k[1] == "range-partials"]
    finally:
        de._SHARED_FILTER_CACHES.clear()
    assert built
    assert got == want
    # the scan's own arithmetic, for the first window: products per chunk
    sel = [i for i, row in enumerate(rows) if row[0] <= days[0]]
    per_chunk = {}
    for i in sel:
        per_chunk[i // 16384] = per_chunk.get(i // 16384, 0) + 1
    total = 0
    for c in sorted(per_chunk):
        total += 0.1 * per_chunk[c]
    assert got[0] == ("float", repr(total))


def test_whole_numbers_beside_doubles_keep_their_type(eager):
    """#351: a window holding whole numbers only, in a column
    that holds Doubles elsewhere, is the scan's exact int total (_se_total),
    not the Double the scaled totals gave (3.0 for 3, 1.5 for an AVERAGE of
    1 and 2 alike) -- the running-total route of 0.9.148 and the grouped
    key both."""
    days = [FIRST + timedelta(days=i) for i in range(4)]
    rows = [[days[0], 1], [days[0], 2], [days[1], 0.5], [days[2], True], [days[3], 7], [days[3], 0.25]]
    tables = {"Cal": {"columns": ["Date"], "rows": [[d] for d in days]},
              "F": {"columns": ["When", "Amt"], "rows": rows}}
    rels = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    ms = {"RT": "CALCULATE(SUM(F[Amt]), Cal[Date] <= MAX(Cal[Date]))",
          "RTA": "CALCULATE(AVERAGE(F[Amt]), Cal[Date] <= MAX(Cal[Date]))",
          "S": "SUM(F[Amt])", "A": "AVERAGE(F[Amt])"}
    cells = [(m, {"Cal.Date": [d]}, ["Cal.Date"]) for d in days for m in ms]
    assert _differential(tables, ms, rels, cells) == []
    de._SHARED_FILTER_CACHES.clear()
    try:
        # the second request builds the totals, and the first day's window
        # is read off them
        got = [_ev("RT", tables, ms, {"Cal.Date": [d]}, rels, ["Cal.Date"]) for d in (days[1], days[2], days[0])]
        built = [k for k in de._shared_filter_cache(tables)
                 if isinstance(k, tuple) and len(k) > 1 and k[1] == "range-partials"]
    finally:
        de._SHARED_FILTER_CACHES.clear()
    assert built
    assert got == [("float", "3.5"), ("float", "4.5"), ("int", "3")]


def test_a_grouped_query_reads_each_key_off_the_totals(eager, small_chunks, keyed):
    """#347: a SUM grouped by the date reads each date's total
    off the base's per-key totals -- the fact rows read once per base, not
    once per date -- and each answer is the scan's."""
    tables, ms, rels, cells = _kernel_window_model()
    cells = [c for c in cells if c[0] == "S"]
    want = _plain_answers(tables, ms, rels, cells)
    seen = []
    real = de.DAXContext._get_column_data_uncached

    def spy(self, t, c):
        seen.append((t, c))
        return real(self, t, c)

    de._SHARED_FILTER_CACHES.clear()
    de.DAXContext._get_column_data_uncached = spy
    try:
        got = [_ev(m, tables, ms, fc, rels, gb) for m, fc, gb in cells]
    finally:
        de.DAXContext._get_column_data_uncached = real
        de._SHARED_FILTER_CACHES.clear()
    assert got == want
    assert keyed[0] == len(cells) - 1          # the first date is a scan, the rest read the totals
    assert seen.count(("F", "Amt")) == 1


def test_a_comparison_on_the_facts_own_column_stays_in_the_base(eager, keyed):
    """#347: CALCULATE(SUM(F[Amt]), F[Amt] > 0, Cal[Date] <=
    MAX(Cal[Date])) is a window over the positive amounts (0.9.148 refused
    any second comparison), and the income by date a grouped key."""
    tables, ms, rels, cells = _own_model()
    want = _plain_answers(tables, ms, rels, cells)
    built = [0]
    original_init = de._RangePartials.__init__

    def counting_init(self, *a, **k):
        built[0] += 1
        original_init(self, *a, **k)

    de._RangePartials.__init__ = counting_init
    de._SHARED_FILTER_CACHES.clear()
    try:
        got = [_ev(m, tables, ms, fc, rels, gb) for m, fc, gb in cells]
    finally:
        de._RangePartials.__init__ = original_init
        de._SHARED_FILTER_CACHES.clear()
    assert got == want
    assert built[0] == 2 and keyed[0] > 0


def test_a_key_totals_replay_is_the_windows_replay():
    """_kernel_key_totals gives _kernel_total(p, p + 1, False) for every
    position, over random terms of every kind (segments, RLE chunks, chunks,
    grouped pieces) -- the two are written apart."""
    r = random.Random(7)
    for _ in range(200):
        n = r.randint(1, 6)
        terms = []
        for _t in range(r.randint(1, 12)):
            kind = r.choice("srcg")
            if kind == "s":
                terms.append(("s",))
                continue
            m = r.randint(1, 6)
            ca = [r.randint(-1, n - 1) for _ in range(m)]
            cv = [r.choice([0.1, 0.2, 2.0 ** 53, 1.0, -3.3, 1e-9]) for _ in range(m)]
            if kind == "r":
                aps = sorted(ca)
                terms.append(("r", cv[0], aps, sum(1 for x in aps if x < 0)))
            elif kind == "c":
                nonneg = [x for x in ca if x >= 0]
                terms.append(("c", cv, ca, se_scan.seq_sum(cv), min(nonneg) if nonneg else 0,
                              max(nonneg) if nonneg else -1, len(nonneg) != len(ca))
                             + de._RangePartials._by_position(ca))
            else:
                terms.append(("g", cv, ca) + de._RangePartials._by_position(ca))
        part = de._RangePartials(list(range(n)), [], [])
        part.terms = terms
        want = [part._kernel_total(p, p + 1, False) for p in range(n)]
        got = de._RangePartials._kernel_key_totals(terms, n)
        assert [repr(v) for v in got] == [repr(v) for v in want]


def test_kept_masks_answer_any_order_of_windows():
    """_window_values' kept masks: any sequence of windows (growing,
    shrinking, moving, BLANK on and off) gives the values a fresh mask
    gives."""
    r = random.Random(11)
    for _ in range(100):
        n = r.randint(1, 8)
        m = r.randint(1, 30)
        ca = [r.randint(-1, n - 1) for _ in range(m)]
        cv = [float(i) for i in range(m)]
        order, sp = de._RangePartials._by_position(ca)
        part = de._RangePartials(list(range(n)), [], [])
        for _w in range(20):
            lo = r.randint(0, n)
            hi = r.randint(lo, n)
            blank = r.random() < 0.5
            vals, k = part._window_values(0, cv, order, sp, lo, hi, blank)
            vals = list(vals)
            want = [v for v, p in zip(cv, ca) if lo <= p < hi or (blank and p == -1)]
            assert vals == want and k == len(want)


# ---- the batch: rows still to come ---------------------------------------------

def test_rows_of_one_visual_are_counted_once_per_later_context():
    """#347: each context learns how many contexts after it
    belong to its visual -- same grouped columns, same other filters, same
    selected filters. Ungrouped and refused contexts belong to none."""
    def row(month, region="West", **extra):
        return {"filter_context": {"Cal.Month": [month], "Geo.Region": [region]},
                "group_by": ["Cal.Month"], **extra}

    batch = [
        row(1), row(2),
        row(3, region="East"),                                  # another visual: its slicer differs
        row(4),
        {"filter_context": {"Cal.Month": [5]}},                 # not grouped
        row(6, selected_filter_context={"Geo.Region": ["West", "East"]}),
        42,                                                     # refused: not an object
        row(7, bogus=True),                                     # refused: an unknown key
        row(8),
    ]
    assert server._rows_still_to_come(batch) == [3, 2, 0, 1, 0, 0, 0, 0, 0]
    assert server._rows_still_to_come([]) == []
    # the grouped columns' order and repeats do not make another visual
    twin = {"filter_context": {"Cal.Month": [1], "Cal.Year": [2024]}, "group_by": ["Cal.Year", "Cal.Month"]}
    swapped = {"filter_context": {"Cal.Month": [2], "Cal.Year": [2024]},
               "group_by": ["Cal.Month", "Cal.Year", "Cal.Month"]}
    assert server._rows_still_to_come([twin, swapped]) == [1, 0]


def test_the_build_rule_weighs_the_rows_to_come():
    """_range_pays with ``pending`` rows: the totals pay once those rows,
    each costing the mean of the base's later scans, come to
    _RANGE_AHEAD_FACTOR times the estimate. One recorded scan is never
    enough, and without pending rows the twenty-fold rule stands alone."""
    rule = de.DAXEngine._range_pays
    estimate = 2000 * 2e-6 * de._RANGE_PARTIALS_FACTOR       # the totals alone, 6 ms
    record = (5, 0.2 + 4 * 5e-4, 0.2, 2e-6)                  # four later scans of 0.5 ms
    threshold = de._RANGE_AHEAD_FACTOR * estimate / 5e-4      # rows whose scans cover it
    assert not rule(record, None, False, False, 2000, 50)
    assert rule(record, None, False, False, 2000, 50, round(threshold) + 2)
    assert not rule(record, None, False, False, 2000, 50, round(threshold) - 2)
    assert not rule((1, 0.2, 0.2, 2e-6), None, False, False, 2000, 50, 10 ** 8)
    long_record = (300, 0.2 + 299 * 5e-4, 0.2, 2e-6)          # paid twenty times over
    assert rule(long_record, None, False, False, 2000, 50)


def test_a_batch_brackets_each_context_and_builds_early(tmp_path, monkeypatch):
    """pbix_evaluate_dax_batch evaluates each context inside
    grouped_rows_pending with its visual's later rows, and leaves nothing
    pending after it, a raising context included. With the build rule
    reduced to the pending rows (the twenty-fold rule off) the batch builds
    the totals, and the same contexts sent one call at a time do not. Every
    answer is the plain path's, each call on a fresh cache."""
    from pbix_mcp.builder import PBIXBuilder

    dates = [FIRST + timedelta(days=i) for i in range(6)]
    pb = PBIXBuilder("Pending253")
    pb.add_table("Cal", [{"name": "Date", "data_type": "DateTime"}], rows=[{"Date": x} for x in dates])
    pb.add_table("F", [{"name": "Date", "data_type": "DateTime"}, {"name": "Amt", "data_type": "Double"},
                       {"name": "Q", "data_type": "Int64"}],
                 rows=[{"Date": dates[i % 6], "Amt": 0.1 * (i % 7) + 1.3, "Q": i % 5} for i in range(40)])
    pb.add_relationship("F", "Date", "Cal", "Date")
    pb.add_measure("F", "Total", "SUM(F[Amt])")
    pb.add_measure("F", "Income", "CALCULATE([Total], F[Amt] > 1.5, F[Q] >= 1)")
    pb.add_measure("F", "Running", "CALCULATE([Total], 'Cal'[Date] <= MAX('Cal'[Date]))")
    pb.add_page("Page 1")
    pbix = str(tmp_path / "pending253.pbix")
    pb.save(pbix)
    handle = "pending253"
    opened = json.loads(server.pbix_open(pbix, handle))
    assert opened["success"]
    names = "Total,Income,Running"
    visual = [{"filter_context": {"Cal.Date": [x.isoformat()]}, "group_by": ["Cal.Date"]} for x in dates]

    def one_call(context):
        return json.loads(server.pbix_evaluate_dax(
            handle, names, json.dumps(context["filter_context"]),
            apply_default_filters=False, group_by=json.dumps(context["group_by"])))

    made = [0]
    plain_init = de._RangePartials.__init__

    def tally_init(self, *a, **k):
        made[0] += 1
        plain_init(self, *a, **k)

    seen_pending = []
    plain_bracket = de.grouped_rows_pending

    def watched_bracket(count):
        seen_pending.append(count)
        return plain_bracket(count)

    try:
        with _plain_path():
            expected = []
            for context in visual:
                de._SHARED_FILTER_CACHES.clear()
                expected.append(one_call(context))
        monkeypatch.setattr(de, "_RANGE_BUILD_FACTOR", float("inf"))
        monkeypatch.setattr(de, "_RANGE_AHEAD_FACTOR", 0.0)
        monkeypatch.setattr(de._RangePartials, "__init__", tally_init)
        de._SHARED_FILTER_CACHES.clear()
        assert [one_call(context) for context in visual] == expected
        assert made[0] == 0                          # one call at a time: nothing pending
        monkeypatch.setattr(de, "grouped_rows_pending", watched_bracket)
        de._SHARED_FILTER_CACHES.clear()
        answered = json.loads(server.pbix_evaluate_dax_batch(handle, names, json.dumps(visual)))
        assert seen_pending == [5, 4, 3, 2, 1, 0]
        assert de._PENDING.count == 0
        assert answered["results"] == expected
        assert made[0] == 3                          # one base each for Total, Income, Running
        monkeypatch.setattr(server, "_evaluate_dax_for_context",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("raised")))
        server.pbix_evaluate_dax_batch(handle, "Total", json.dumps(visual))
        assert de._PENDING.count == 0
    finally:
        server.pbix_close(handle, force=True)


def test_pending_rows_are_per_thread_and_nest():
    with de.grouped_rows_pending(9):
        elsewhere = []
        worker = threading.Thread(target=lambda: elsewhere.append(de._PENDING.count))
        worker.start()
        worker.join()
        assert elsewhere == [0] and de._PENDING.count == 9
        with de.grouped_rows_pending(2):
            assert de._PENDING.count == 2
        assert de._PENDING.count == 9
    assert de._PENDING.count == 0


# ---- VAR blocks split once -----------------------------------------------------

def test_a_var_block_is_split_once(monkeypatch):
    """#349: _var_block_parts is a pure function of the text,
    split once; each caller gets a list of its own."""
    text = "VAR a = 1 VAR b = a + 1 RETURN a + b"
    de._var_block_split.cache_clear()
    calls = []
    real = de._var_block_keywords
    monkeypatch.setattr(de, "_var_block_keywords", lambda t: (calls.append(t), real(t))[1])
    first = de._var_block_parts(text)
    first[0].append(("x", "9"))
    second = de._var_block_parts(text)
    assert second == ([("a", "1"), ("b", "a + 1")], "a + b")
    assert len(calls) == 1
