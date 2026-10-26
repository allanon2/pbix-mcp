"""Issue #231: column aggregates are memoized per filter context, and running
totals are read off per-key totals where that is exactly the scan's answer.

What was wrong: SUM, AVERAGE, COUNT, COUNTA, MIN, MAX, DISTINCTCOUNT and
DISTINCTCOUNTNOBLANK of a column scanned the column's visible rows on every
call. A measure that asks for the same aggregate under the same filters from
every row of a visual -- IF(MAX('Calendar'[Date]) <= CALCULATE(MAX(Sales[Date]),
REMOVEFILTERS()), [Total]) -- scanned the whole fact table once per row, and a
running total CALCULATE([Total], 'Calendar'[Date] <= MAX('Calendar'[Date]))
scanned all the rows before each date again, about 100 ms a row over 100k
fact rows (PR #206, by @allanon2; re-implemented, PR code not used).

Where the expected values come from: the engine's own plain path -- the
aggregate computed from the visible values by the scan (_aggregate_scan), as
0.9.146 computed it from get_column_data -- on a FRESH model cache for every
cell, never a cache shared with the run under test (the memos of a shared
cache depend on evaluation order; issues #217, #218). The memoized and
running-total routes must give the same value of the same type, bit for bit,
with the cells evaluated in order and in reverse, twice each. Facts behind
the plain path: SUM adds Doubles as Power BI Desktop's storage engine does
(issue #243: along the column's storage layout, in the scan's order --
build_b243.py, build_b210.py), a key the one side holds twice joins its last
row (#162, Desktop), text keys stay text (#222, Desktop).

The counterexamples of the PR review must not come back. Totals kept per
one-side row counted a fact row once for every one-side row it joins: across a
many-to-many relationship (cx3: 10 / 1 / 15 expected, the PR's route gave 20 /
2 / 25) and where two one-side keys reach the same fact key ('01' and '1' both
read as a numeric fact key 1). A window's total of Doubles is the scan's,
added in the scan's order (issue #243): the totals are kept as exact integers
over a power of two only where every partial sum is exact, and otherwise the
scan is replayed over the window -- the correctly rounded total that 0.9.147
read off the exact integers differs from it in the last bits. Each defect is
also planted, and the differential must report it.

The review of the first version found three more, each covered here. A
USERELATIONSHIP over a DatePartOnly relationship read the base through a
context with other date-part joins than the scan's: 300, 628, 1028 where the
scan (and 0.9.146) said 300, 600, 1000. Issue #250 settled which joins are
right: Power BI Desktop 2.152 joins the activated relationship by date part
(build_b256.py), so every context now takes the joins of its own relationship
list and the answers are 303, 606, 1010 (exact arithmetic over the model:
day k adds 101 * (k + 1)). Totals built on a base's second
request cost far more than the scans they replaced over a large dimension
(20 s for four requests that took 9 s): they now wait until the scans have
paid for them twenty times over (_range_pays), which is tested on the rule
itself and on a dimension much larger than its windows. A row transition over
the fact table asked for a base per fact row and left a marker for each: no
route and no marker there. Most route tests build on the second request
(the ``eager`` fixture) to test the answers without waiting.
"""
from __future__ import annotations

import contextlib
import math
import random
from array import array
from datetime import datetime, timedelta

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

DAYS = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(90)]
RELS = [{"FromTable": "Sales", "FromColumn": "Date", "ToTable": "Calendar", "ToColumn": "Date",
         "IsActive": 1, "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
MEASURES = {
    "Total": "SUM(Sales[Amount])",
    "RT": "CALCULATE([Total], 'Calendar'[Date] <= MAX('Calendar'[Date]))",
    "RTlt": "CALCULATE(SUM(Sales[Amount]), 'Calendar'[Date] < MAX('Calendar'[Date]))",
    "RTavg": "CALCULATE(AVERAGE(Sales[Amount]), 'Calendar'[Date] <= MAX('Calendar'[Date]))",
    "RTcnt": "CALCULATE(COUNT(Sales[Amount]), 'Calendar'[Date] <= MAX('Calendar'[Date]))",
    "RTcnta": "CALCULATE(COUNTA(Sales[Amount]), 'Calendar'[Date] > MAX('Calendar'[Date]))",
    "Qty": "CALCULATE(SUM(Sales[Qty]), 'Calendar'[Date] <= MAX('Calendar'[Date]))",
    "QtyAfter": "CALCULATE(SUM(Sales[Qty]), 'Calendar'[Date] > MAX('Calendar'[Date]))",
    "QtyWin": ("CALCULATE(SUM(Sales[Qty]), 'Calendar'[Date] >= MIN('Calendar'[Date]) - 6, "
               "'Calendar'[Date] <= MAX('Calendar'[Date]))"),
    "QtyAvgWin": ("CALCULATE(AVERAGE(Sales[Qty]), 'Calendar'[Date] >= MIN('Calendar'[Date]) - 6, "
                  "'Calendar'[Date] <= MAX('Calendar'[Date]))"),
    "AmtWin": ("CALCULATE(SUM(Sales[Amount]), 'Calendar'[Date] >= MIN('Calendar'[Date]) - 6, "
               "'Calendar'[Date] <= MAX('Calendar'[Date]))"),
    "GTMax": "CALCULATE(MAX(Sales[Date]), REMOVEFILTERS())",
    "Capped": "IF(MAX('Calendar'[Date]) <= CALCULATE(MAX(Sales[Date]), REMOVEFILTERS()), [Total])",
}


def _sales_model(sort: bool = True, n: int = 1500, seed: int = 231) -> dict:
    """Calendar of 90 days, Sales of Doubles whose sum depends on the order
    they are added in (magnitudes 1e-2 to 1e6) and whole-number quantities."""
    r = random.Random(seed)
    sales = [[r.choice(DAYS), r.random() * 10 ** r.randint(-2, 6), r.randint(1, 9)] for _ in range(n)]
    if sort:
        sales.sort(key=lambda row: row[0])
    return {"Calendar": {"columns": ["Date", "Year", "Month"], "rows": [[d, d.year, d.month] for d in DAYS]},
            "Sales": {"columns": ["Date", "Amount", "Qty"], "rows": sales}}


def _plain_aggregate(self, kind, col, ctx, compute):
    return self._aggregate_scan(kind, col, self._agg_ctx(ctx), compute)


@contextlib.contextmanager
def _plain_path():
    """The aggregates as 0.9.146 computed them: from the visible values,
    every call. (An engine without the memo is that path already.)"""
    real = getattr(de.DAXEngine, "_column_aggregate", None)
    if real is not None:
        de.DAXEngine._column_aggregate = _plain_aggregate
    try:
        yield
    finally:
        if real is not None:
            de.DAXEngine._column_aggregate = real


def _ev(name, tables, measures, fc, rels, **kw):
    try:
        v = de.evaluate_measure(name, tables, measures, dict(fc), relationships=rels, **kw)
    except Exception as e:  # noqa: BLE001 -- an error is an answer to compare too
        return ("error", type(e).__name__)
    return (type(v).__name__, repr(v))


def _differential(tables, measures, rels, cells, repeat: int = 2) -> list:
    """Mismatches between the plain path on a fresh cache per cell and the
    engine on one shared cache, the cells in order and reversed, each order
    ``repeat`` times. ``cells`` are (measure, filter context, kwargs)."""
    want = []
    with _plain_path():
        for name, fc, kw in cells:
            de._SHARED_FILTER_CACHES.clear()
            want.append(_ev(name, tables, measures, fc, rels, **kw))
    bad = []
    for order in (list(range(len(cells))), list(reversed(range(len(cells))))):
        de._SHARED_FILTER_CACHES.clear()
        for _ in range(repeat):
            for i in order:
                name, fc, kw = cells[i]
                got = _ev(name, tables, measures, fc, rels, **kw)
                if got != want[i]:
                    bad.append((name, fc, kw, got, want[i]))
    return bad


@pytest.fixture
def scans(monkeypatch):
    """Every column read (table, column, its filter context) that reached
    the rows -- get_column_data's uncached path."""
    seen: list = []
    real = de.DAXContext._get_column_data_uncached

    def spy(self, table_name, column_name):
        seen.append((table_name, column_name, dict(self.filter_context)))
        return real(self, table_name, column_name)

    monkeypatch.setattr(de.DAXContext, "_get_column_data_uncached", spy)
    return seen


@pytest.fixture
def builds(monkeypatch):
    """How many per-key totals were built (_RangePartials; none by an engine
    without them)."""
    count = [0]
    cls = getattr(de, "_RangePartials", None)
    if cls is not None:
        real = cls.__init__

        def init(self, *a, **k):
            count[0] += 1
            real(self, *a, **k)

        monkeypatch.setattr(cls, "__init__", init)
    return count


@pytest.fixture
def eager(monkeypatch):
    """Totals built on a base's second request, whatever its scans cost
    (_range_pays with a factor of 0): the route's answers, tested without
    waiting for the scans to pay for a build."""
    if hasattr(de, "_RANGE_BUILD_FACTOR"):
        monkeypatch.setattr(de, "_RANGE_BUILD_FACTOR", 0.0)


def _ranged(fc: dict) -> bool:
    return any(isinstance(v, dict) for v in fc.values())


def _model_cache(tables) -> dict:
    return de._shared_filter_cache(tables)


def _kinds(tables, kind: str) -> list:
    return [k for k in _model_cache(tables) if isinstance(k, tuple) and len(k) > 1 and k[1] == kind]


# ---- the two patterns the issue is about ------------------------------------

def test_capped_reads_the_fact_once(scans):
    tables = _sales_model()
    rows = DAYS[20:60]
    want = []
    with _plain_path():
        for d in rows:
            de._SHARED_FILTER_CACHES.clear()
            want.append(_ev("Capped", tables, MEASURES, {"Calendar.Date": [d]}, RELS))
    de._SHARED_FILTER_CACHES.clear()
    scans.clear()
    got = [_ev("Capped", tables, MEASURES, {"Calendar.Date": [d]}, RELS) for d in rows]
    assert got == want
    whole = [s for s in scans if s[:2] == ("Sales", "Date") and not s[2]]
    # 0.9.146 scanned all 1,500 dates once per row (40 times)
    assert len(whole) == 1


@pytest.mark.parametrize("measure", ["RT", "RTlt", "RTavg", "RTcnt", "RTcnta", "Qty", "QtyAfter",
                                     "QtyWin", "QtyAvgWin"])
def test_running_totals_read_the_totals_not_the_rows(measure, scans, builds, eager):
    tables = _sales_model(sort=True)
    rows = DAYS[10:70]
    want = []
    with _plain_path():
        for d in rows:
            de._SHARED_FILTER_CACHES.clear()
            want.append(_ev(measure, tables, MEASURES, {"Calendar.Date": [d]}, RELS))
    de._SHARED_FILTER_CACHES.clear()
    scans.clear()
    got = [_ev(measure, tables, MEASURES, {"Calendar.Date": [d]}, RELS) for d in rows]
    assert got == want                     # bit for bit, type included
    ranged = [s for s in scans if s[0] == "Sales" and _ranged(s[2])]
    # the first request scans, the second builds the totals once, every
    # later one reads them; 0.9.146 scanned once per row (60 times)
    assert len(ranged) == 1
    assert builds[0] == 1


def test_whole_numbers_take_any_window_in_any_row_order(scans, eager):
    tables = _sales_model(sort=False)
    rows = DAYS[10:50]
    for measure in ("Qty", "QtyAfter", "QtyWin", "QtyAvgWin", "RTcnt"):
        cells = [(measure, {"Calendar.Date": [d]}, {}) for d in rows]
        assert _differential(tables, MEASURES, RELS, cells, repeat=1) == []
        de._SHARED_FILTER_CACHES.clear()
        scans.clear()
        for d in rows:
            _ev(measure, tables, MEASURES, {"Calendar.Date": [d]}, RELS)
        assert len([s for s in scans if s[0] == "Sales" and _ranged(s[2])]) == 1, measure


@pytest.mark.parametrize("sort, measure", [(False, "RT"), (False, "RTavg"), (True, "AmtWin")],
                         ids=["unsorted", "unsorted average", "window not from the first date"])
def test_doubles_take_any_window_in_any_row_order(sort, measure, scans, eager):
    # SUM adds Doubles in the storage engine's order (#243). These Doubles do
    # not add exactly, so a window's total replays the scan over the window's
    # rows (_RangePartials 'kernel'): the scan's answer bit for bit -- in any
    # row order, for any window
    tables = _sales_model(sort=sort)
    rows = DAYS[10:40]
    cells = [(measure, {"Calendar.Date": [d]}, {}) for d in rows]
    assert _differential(tables, MEASURES, RELS, cells, repeat=1) == []
    de._SHARED_FILTER_CACHES.clear()
    scans.clear()
    for d in rows:
        _ev(measure, tables, MEASURES, {"Calendar.Date": [d]}, RELS)
    assert len([s for s in scans if s[0] == "Sales" and _ranged(s[2])]) == 1


def test_the_totals_are_built_on_the_second_request_only(scans, builds, eager):
    """A visual grouped by category asks for each category's running total
    once: building totals for one use cost more than the scan (perf_cat of
    the PR review). Asked again under the same other filters, they are built
    once and read from then on (here as early as the rule allows)."""
    r = random.Random(5)
    tables = _sales_model()
    tables["Product"] = {"columns": ["PK", "Cat"], "rows": [[i, f"C{i % 6}"] for i in range(30)]}
    tables["Sales"]["columns"].append("PK")
    for row in tables["Sales"]["rows"]:
        row.append(r.randrange(30))
    rels = RELS + [{"FromTable": "Sales", "FromColumn": "PK", "ToTable": "Product", "ToColumn": "PK",
                    "IsActive": 1, "FromCardinality": 2, "ToCardinality": 1}]
    for c in range(6):
        _ev("RT", tables, MEASURES, {"Product.Cat": [f"C{c}"]}, rels)
    assert builds[0] == 0
    assert len(_kinds(tables, "range-seen")) == 6
    scans.clear()
    for d in DAYS[30:40]:
        _ev("RT", tables, MEASURES, {"Product.Cat": ["C1"], "Calendar.Date": [d]}, rels)
    assert builds[0] == 1
    assert len([s for s in scans if s[0] == "Sales" and _ranged(s[2])]) == 0
    cells = [("RT", {"Product.Cat": [f"C{c}"], "Calendar.Date": [d]}, {})
             for c in range(6) for d in DAYS[30:36]]
    assert _differential(tables, MEASURES, rels, cells) == []


# ---- regressions from the PR review ------------------------------------------

def _mm_model():
    return {"D": {"columns": ["k", "x"], "rows": [[1, 1], [1, 2], [2, 3]]},
            "T": {"columns": ["k", "v"], "rows": [[1, 10.0], [2, 5.0]]}}


MM_RELS = [{"FromTable": "T", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
            "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 2}]
MM_MEASURES = {"S": "CALCULATE(SUM(T[v]), D[x] <= 2)", "C": "CALCULATE(COUNT(T[v]), D[x] <= 2)",
               "S3": "CALCULATE(SUM(T[v]), D[x] <= 3)", "S1": "CALCULATE(SUM(T[v]), D[x] <= 1)"}


def test_many_to_many_counts_a_fact_row_once(builds, eager):
    # cx3: D's two rows of key 1 join T's one row of key 1, which the hop
    # takes once (a union); per-D-row totals counted it twice (20 / 2 / 25)
    tables = _mm_model()
    for _ in range(3):
        got = {m: de.evaluate_measure(m, tables, MM_MEASURES, relationships=MM_RELS) for m in MM_MEASURES}
        assert got == {"S": 10.0, "C": 1, "S3": 15.0, "S1": 10.0}
    assert builds[0] == 0


TEXT_MEASURES = {"Eq1": "CALCULATE(SUM(T[v]), D[x] = 1)", "Le2": "CALCULATE(SUM(T[v]), D[x] <= 2)",
                 "C2": "CALCULATE(COUNT(T[v]), D[x] <= 2)", "Le3": "CALCULATE(SUM(T[v]), D[x] <= 3)",
                 "Lt3": "CALCULATE(SUM(T[v]), D[x] < 3)"}
TEXT_RELS = [{"FromTable": "T", "FromColumn": "code", "ToTable": "D", "ToColumn": "code", "IsActive": 1,
              "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]


def test_text_keys_whose_spellings_overlap(eager):
    # The review's H1: '01' and '1' are two keys of a Text column, each
    # reaching its own fact row (#222; Desktop 2.152: D[code] = "01" is 10)
    tables = {"D": {"columns": ["code", "x"], "rows": [["01", 1], ["1", 2], ["7", 3]]},
              "T": {"columns": ["code", "v"], "rows": [["01", 10.0], ["1", 5.0], ["7", 1.0]]}}
    for _ in range(3):
        got = {m: de.evaluate_measure(m, tables, TEXT_MEASURES, relationships=TEXT_RELS)
               for m in TEXT_MEASURES}
        assert got == {"Eq1": 10.0, "Le2": 15.0, "C2": 2, "Le3": 16.0, "Lt3": 15.0}


def _numeric_fact_model():
    # a numeric fact key: the text keys '01' and '1' both read as 1 and both
    # reach the fact row of key 1, which no per-D-row total may hold twice
    return {"D": {"columns": ["code", "x"], "rows": [["01", 1], ["1", 2], ["7", 3]]},
            "T": {"columns": ["code", "v"], "rows": [[1, 10.0], [1, 5.0], [7, 1.0], [2, 100.0]]}}


def test_overlapping_sets_take_the_scan(builds, eager):
    tables = _numeric_fact_model()
    cells = [(m, {}, {}) for m in TEXT_MEASURES]
    assert _differential(tables, TEXT_MEASURES, TEXT_RELS, cells, repeat=3) == []
    assert builds[0] == 0


def _blank_model():
    """D[x] holds BLANK, one fact key matches no D row (D's blank row) and
    one is BLANK: BLANK <= 3 is TRUE (BLANK compares as 0), BLANK >= 2 FALSE."""
    return {"D": {"columns": ["k", "x"], "rows": [[1, 1], [2, None], [3, 3], [4, 5], [5, 2]]},
            "T": {"columns": ["k", "v", "n"],
                  "rows": [[1, 1.5, 1], [2, 2.25, 2], [3, 4.0, 3], [9, 8.5, 4], [None, 16.0, 5],
                           [4, 32.0, 6], [5, 0.125, 7], [1, 64.0, 8]]}}


BLANK_RELS = [{"FromTable": "T", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
               "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
BLANK_MEASURES = {
    "Le3": "CALCULATE(SUM(T[v]), D[x] <= 3)", "Ge2": "CALCULATE(SUM(T[v]), D[x] >= 2)",
    "Lt0": "CALCULATE(SUM(T[v]), D[x] < 0)", "Le3n": "CALCULATE(SUM(T[n]), D[x] <= 3)",
    "Ge2n": "CALCULATE(SUM(T[n]), D[x] >= 2)", "Gt1c": "CALCULATE(COUNT(T[v]), D[x] > 1)",
    "Le1a": "CALCULATE(AVERAGE(T[v]), D[x] <= 1)",
}


def test_blank_keys_and_the_blank_row(builds, eager):
    tables = _blank_model()
    cells = [(m, fc, {}) for m in BLANK_MEASURES for fc in ({}, {"D.k": [1, 2, 3]}, {"T.n": [1, 2, 4, 5, 8]})]
    assert _differential(tables, BLANK_MEASURES, BLANK_RELS, cells, repeat=3) == []
    assert builds[0] > 0


def test_one_to_one_takes_the_totals_and_date_part_joins_do_not(builds, eager):
    tables = {"D": {"columns": ["k", "x"], "rows": [[i, i % 4] for i in range(1, 9)]},
              "T": {"columns": ["k", "v"], "rows": [[i, i * 1.25] for i in (1, 2, 3, 5, 8, 11)]}}
    one = [{"FromTable": "T", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
            "CrossFilteringBehavior": 2, "FromCardinality": 1, "ToCardinality": 1}]
    ms = {"S": "CALCULATE(SUM(T[v]), D[x] <= 2)", "C": "CALCULATE(COUNT(T[v]), D[x] > 0)"}
    cells = [(m, fc, {}) for m in ms for fc in ({}, {"T.k": [1, 2, 3, 5]})]
    assert _differential(tables, ms, one, cells, repeat=3) == []
    assert builds[0] > 0
    builds[0] = 0
    dated = {"Cal": {"columns": ["Date", "Year"], "rows": [[d, d.year] for d in DAYS[:20]]},
             "F": {"columns": ["When", "v"],
                   "rows": [[d + timedelta(hours=h), float(h)] for d in DAYS[:20] for h in (0, 9)]}}
    part = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1, "JoinOnDateBehavior": 2}]
    ms = {"S": "CALCULATE(SUM(F[v]), Cal[Date] <= MAX(Cal[Date]))"}
    cells = [("S", {"Cal.Date": [d]}, {}) for d in DAYS[2:12]]
    assert _differential(dated, ms, part, cells, repeat=2) == []
    assert builds[0] == 0


def test_the_route_steps_aside(builds, scans, eager):
    """Two comparisons in the filter set, a comparison on the aggregated
    table itself, a filter on column combinations, ALL() of the table in
    force: the scan answers, however often it is asked."""
    tables = _sales_model()
    ms = dict(MEASURES)
    ms.update({
        "Two": "CALCULATE(SUM(Sales[Qty]), 'Calendar'[Date] <= MAX('Calendar'[Date]), 'Calendar'[Month] >= 2)",
        "Own": "CALCULATE(SUM(Sales[Qty]), Sales[Date] <= MAX('Calendar'[Date]))",
        "Tup": ("CALCULATE(SUM(Sales[Qty]), 'Calendar'[Date] <= MAX('Calendar'[Date]), "
                "TREATAS({(1, 2024), (2, 2024)}, 'Calendar'[Month], 'Calendar'[Year]))"),
        "All": "CALCULATE(SUM(Sales[Qty]), ALL(Sales), 'Calendar'[Date] <= MAX('Calendar'[Date]))",
        "Keep": "CALCULATE(SUM(Sales[Qty]), KEEPFILTERS('Calendar'[Date] <= MAX('Calendar'[Date])))",
    })
    for m in ("Two", "Own", "Tup", "All", "Keep"):
        cells = [(m, {"Calendar.Date": [d]}, {}) for d in DAYS[40:46]]
        assert _differential(tables, ms, RELS, cells, repeat=2) == [], m
    assert builds[0] == 0


# ---- the review of the first version ----------------------------------------

def _dp_model():
    """dp_userel4.py of the review: F's Ship joins Cal[Date] only through an
    inactive DatePartOnly relationship, and half of F's ships are at 12:30.
    P is a product dimension, joined by value. Day k holds two rows, v = k + 1
    shipped at 12:30 and v = 100 * (k + 1) shipped at midnight."""
    days = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(10)]
    cal = {"columns": ["Date", "Year"], "rows": [[d, 2024 if i < 7 else 2025] for i, d in enumerate(days)]}
    rows = []
    for i, d in enumerate(days):
        rows.append([d, d + timedelta(hours=12, minutes=30), i + 1, i % 4 + 1])
        rows.append([d, d, 100 * (i + 1), (i + 1) % 4 + 1])
    return {"Cal": cal, "F": {"columns": ["When", "Ship", "v", "pk"], "rows": rows},
            "P": {"columns": ["pk", "x"], "rows": [[k, k] for k in range(1, 5)]}}


DP_DAYS = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(10)]
DP_RELS = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
            "FromCardinality": 2, "ToCardinality": 1, "CrossFilteringBehavior": 1},
           {"FromTable": "F", "FromColumn": "Ship", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 0,
            "FromCardinality": 2, "ToCardinality": 1, "CrossFilteringBehavior": 1, "JoinOnDateBehavior": 2},
           {"FromTable": "F", "FromColumn": "pk", "ToTable": "P", "ToColumn": "pk", "IsActive": 1,
            "FromCardinality": 2, "ToCardinality": 1}]
DP_MEASURES = {
    "A": "CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]))",
    "B": "CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), Cal[Date] <= MAX(Cal[Date]))",
    "C": "CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date])), Cal[Date] <= MAX(Cal[Date]))",
    # a running total over P under a day of Cal, the USERELATIONSHIP in an
    # inner CALCULATE (whose context 0.9.147 left with the joins of the set it
    # came from: "stale") or beside the window ("fresh")
    "Pstale": "CALCULATE(CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date])), P[x] <= MAX(P[x]))",
    "Pfresh": "CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]), P[x] <= MAX(P[x]))",
}
DP_CELLS = [("A", {"Cal.Year": [2024], "Cal.Date": {"op": "<=", "value": d}}, {}) for d in DP_DAYS[1:7]]
DP_P_CELLS = [("Pstale", {"Cal.Date": [d], "P.x": [k]}, {}) for d in DP_DAYS[2:5] for k in range(1, 5)]


def test_a_userelationship_context_joins_by_date_part(eager, builds):
    """A USERELATIONSHIP context joins the activated DatePartOnly
    relationship by date part, as every other context over its list does
    (issue #250). 0.9.147's with_relationships kept the joins of the set the
    context came from, so it joined the 12:30 ships by value (300, 600, ...)
    while the route's base, read through without_filters, joined them by date
    part (628 for 600), and the route had to step aside for such a context
    (_range_joins_current, now gone). Power BI Desktop 2.152 (build_b256.py)
    answers the date-part reading in every CALCULATE shape: here day k adds
    101 * (k + 1) -- exact arithmetic over the model -- whether the window is
    beside the USERELATIONSHIP (B) or outside it (C), each on a fresh cache
    and in one cache in both orders (0.9.146 handed C B's answer in one
    cache: 303 for 355)."""
    tables = _dp_model()
    want = [303, 606, 1010, 1515, 2121, 2828]
    assert _differential(tables, DP_MEASURES, DP_RELS, DP_CELLS) == []
    de._SHARED_FILTER_CACHES.clear()
    got = [de.evaluate_measure("A", tables, DP_MEASURES, dict(fc), relationships=DP_RELS)
           for _m, fc, _kw in DP_CELLS]
    assert got == want
    for m in ("B", "C"):
        cells = [(m, {"Cal.Year": [2024], "Cal.Date": [d]}, {}) for d in DP_DAYS[1:7]]
        assert _differential(tables, DP_MEASURES, DP_RELS, cells) == [], m
        fresh_cache = []
        for _m, fc, _kw in cells:
            de._SHARED_FILTER_CACHES.clear()
            fresh_cache.append(de.evaluate_measure(m, tables, DP_MEASURES, dict(fc), relationships=DP_RELS))
        assert fresh_cache == want, m
    # A window on P under a day of Cal: both contexts join the day's ships by
    # date part, so both take the route. Days 2..4, P[x] <= 1..4: day k's
    # ships are (k + 1, pk k % 4 + 1) and (100 * (k + 1), pk (k + 1) % 4 + 1).
    assert _differential(tables, DP_MEASURES, DP_RELS, DP_P_CELLS) == []
    assert builds[0] > 0
    fresh = [("Pfresh", fc, kw) for _m, fc, kw in DP_P_CELLS]
    assert _differential(tables, DP_MEASURES, DP_RELS, fresh) == []
    p_want = [None, None, 3, 303, 400, 400, 400, 404, 5, 505, 505, 505]
    for m in ("Pstale", "Pfresh"):
        got = []
        for _m, fc, _kw in DP_P_CELLS:
            de._SHARED_FILTER_CACHES.clear()
            got.append(de.evaluate_measure(m, tables, DP_MEASURES, dict(fc), relationships=DP_RELS))
        assert got == p_want, m


def _big_dimension_model(nd: int = 4000, nt: int = 8000):
    """D's x is its key, so a window x <= k keeps k + 1 of 4,000 D rows (half
    of them under D[Seg] = "A"): the owners hop all 4,000, what hundreds of
    such scans hop."""
    r = random.Random(7)
    return {"D": {"columns": ["k", "x", "Seg"], "rows": [[k, k, "AB"[k % 2]] for k in range(nd)]},
            "T": {"columns": ["k", "v"], "rows": [[r.randrange(nd), r.randint(1, 9)] for _ in range(nt)]}}


BIG_RELS = [{"FromTable": "T", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1, "CrossFilteringBehavior": 1}]
BIG_MEASURES = {f"S{k}": f"CALCULATE(SUM(T[v]), D[x] <= {k})" for k in range(11)}


def test_a_large_dimension_waits_for_the_scans_to_pay(builds, monkeypatch):
    """bigdim.py of the review: totals built on a base's second request cost
    20 s where four scans took 9 s. A base whose scans are cheap next to the
    build is answered by the scans."""
    tables = _big_dimension_model()
    cells = [(f"S{k}", {"D.Seg": ["A"]}, {}) for k in range(10)]
    assert _differential(tables, BIG_MEASURES, BIG_RELS, cells, repeat=1) == []
    assert builds[0] == 0
    assert _kinds(tables, "range-owners") == [] and _kinds(tables, "range-partials") == []
    # the same base, the build no longer waiting: built on the next request
    monkeypatch.setattr(de, "_RANGE_BUILD_FACTOR", 0.0)
    _ev("S10", tables, BIG_MEASURES, {"D.Seg": ["A"]}, BIG_RELS)
    assert builds[0] == 1 and len(_kinds(tables, "range-owners")) == 1


def test_the_cost_rule():
    """_range_pays: build once the scans after the first have cost twenty
    times the estimate, the estimate made of the scans' own rates."""
    pays = de.DAXEngine._range_pays
    # 11 scans, the first 500 ms (it built the indexes) and ten of 10 ms:
    # 100 ms that count, 0.5 us per visible fact row
    seen = (11, 0.6, 0.5, 5e-7)
    # totals over 4,000 fact rows: 4,000 * 0.5 us * 1.5 = 3 ms, 20x is 60 ms
    assert pays(seen, None, False, False, 4000, 50)
    # over 10,000: 7.5 ms, 150 ms > 100 ms
    assert not pays(seen, None, False, False, 10_000, 50)
    # and the axis of 50 D rows: 0.1 ms more, still within
    assert pays(seen, None, False, True, 4000, 50)
    # the owners: 50 D rows at 10 us * 2.5 and 4,000 claims at 0.5 us = 3.25 ms
    assert not pays(seen, 1e-5, True, False, 4000, 50)
    assert pays((11, 1.0, 0.5, 5e-7), 1e-5, True, False, 4000, 50)
    # a D row's hop costs at least 16 fact rows' time, measured or not
    assert pays(seen, None, True, False, 1000, 50) == pays(seen, 1e-9, True, False, 1000, 50)
    assert not pays((11, 1.0, 0.5, 5e-7), None, True, False, 1000, 40_000)
    # one scan never pays: it is the first
    assert not pays((1, 0.5, 0.5, 1e-9), None, False, False, 1, 1)


def test_the_scans_are_recorded():
    tables = _sales_model()
    for d in DAYS[20:25]:
        de.evaluate_measure("RT", tables, MEASURES, {"Calendar.Date": [d]}, relationships=RELS)
    cache = _model_cache(tables)
    (marker,) = _kinds(tables, "range-seen")
    n, total, first, per_row = cache[marker]
    assert n == 5 and total > first > 0 and 0 < per_row < first
    # the hop's rate, the least of the first three scans of the relationship
    (hops,) = _kinds(tables, "range-hops")
    rate, looked = cache[hops]
    assert looked == 3 and 0 < rate < first


def _cust_model():
    r = random.Random(11)
    days = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    cust = {"columns": ["CustKey", "Name", "Town"], "rows": [[k, f"c{k}", f"t{k % 4}"] for k in range(12)]}
    fact = {"columns": ["Date", "CustKey", "Amt"],
            "rows": [[r.choice(days), r.randrange(12), r.randint(1, 50)] for _ in range(120)]}
    cal = {"columns": ["Date", "Month"], "rows": [[d, d.month] for d in days]}
    return {"Cal": cal, "Cust": cust, "F": fact}


CUST_RELS = [{"FromTable": "F", "FromColumn": "Date", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
              "FromCardinality": 2, "ToCardinality": 1},
             {"FromTable": "F", "FromColumn": "CustKey", "ToTable": "Cust", "ToColumn": "CustKey", "IsActive": 1,
              "FromCardinality": 2, "ToCardinality": 1}]
CUST_MEASURES = {"A3": "CALCULATE(SUM('F'[Amt]), 'Cust'[CustKey] <= 5)",
                 "X3": "SUMX('F', [A3])",
                 "XT": "SUMX(VALUES('Cust'[Town]), CALCULATE(SUM('F'[Amt]), 'Cal'[Date] <= MAX('Cal'[Date])))"}


def test_row_transitions_over_the_fact_skip_the_route(monkeypatch):
    """mem_rowrt.py of the review: SUMX(F, CALCULATE(SUM(F[Amt]),
    Cust[CustKey] <= 5)) asks for a base per fact row, each once; the route
    was tried and left a marker for every one, 9% more time and 23% more
    memory than the scans. A transition over a dimension's values keeps the
    route: its bases come back for every date."""
    tables = _cust_model()
    cells = [("X3", {"Cal.Month": [m]}, {}) for m in (1, 2)]
    assert _differential(tables, CUST_MEASURES, CUST_RELS, cells, repeat=1) == []
    assert _kinds(tables, "range-seen") == []
    tried = [0]
    real = de.DAXEngine._range_scan

    def counted(self, *a, **k):
        tried[0] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(de.DAXEngine, "_range_scan", counted)
    de._SHARED_FILTER_CACHES.clear()
    for m in (1, 2):
        _ev("XT", tables, CUST_MEASURES, {"Cal.Month": [m], "Cal.Date": [datetime(2024, m, 9)]}, CUST_RELS)
    assert tried[0] > 0 and _kinds(tables, "range-seen")
    # planted: the transition over F not recognized -- a marker per fact row
    monkeypatch.setattr(de.DAXEngine, "_range_row_transition", staticmethod(lambda fc, tname: False))
    de._SHARED_FILTER_CACHES.clear()
    _ev("X3", tables, CUST_MEASURES, {"Cal.Month": [1]}, CUST_RELS)
    assert len(_kinds(tables, "range-seen")) > 20


def _dim_filter_model():
    """D with BLANK names, a blank row (T keys 99 and BLANK) and a third
    table E reaching T; whole-number and Double fact columns."""
    r = random.Random(13)
    d_rows = [[k, k % 7, "AB"[k % 2], [None, "ann", "bob", "cat"][k % 4]] for k in range(1, 15)]
    t_rows = [[r.choice(list(range(1, 15)) + [99, None]), r.randint(-5, 40), r.random() * 10 ** r.randint(0, 4),
               r.randrange(3)] for _ in range(200)]
    return {"D": {"columns": ["k", "x", "Seg", "Name"], "rows": d_rows},
            "T": {"columns": ["k", "n", "w", "e"], "rows": t_rows},
            "E": {"columns": ["e", "grp"], "rows": [[0, "g0"], [1, "g1"], [2, "g0"]]}}


DIM_RELS = [{"FromTable": "T", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1},
            {"FromTable": "T", "FromColumn": "e", "ToTable": "E", "ToColumn": "e", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
DIM_MEASURES = {"Sn": "CALCULATE(SUM(T[n]), D[x] <= 3)", "Cn": "CALCULATE(COUNT(T[n]), D[x] > 2)",
                "An": "CALCULATE(AVERAGE(T[n]), D[x] >= 1)", "Sw": "CALCULATE(SUM(T[w]), D[x] <= 4)",
                "Bn": "CALCULATE(SUM(T[n]), D[x] < 0)"}
DIM_CONTEXTS = [{}, {"D.Seg": ["A"]}, {"D.Name": {"contains": "a"}}, {"D.Seg": {"not_in": ["B"]}},
                {"D.Name": {"is_blank": True}}, {"D.Seg": ["A"], "E.grp": ["g1"]},
                {"E.grp": ["g0"], "T.e": [0, 1]}, {"D.Name": ["ann", "cat"], "D.Seg": ["A", "B"]}]


def test_the_bases_filters_on_the_dimension(eager, builds):
    """The base's filters on D are read off the owners (the D rows they keep,
    and D's blank row when every one keeps BLANK), the others as the scan
    reads them: In-sets, predicates, BLANK, another table's filters and T's
    own, each alone and together."""
    tables = _dim_filter_model()
    cells = [(m, fc, {}) for m in DIM_MEASURES for fc in DIM_CONTEXTS]
    assert _differential(tables, DIM_MEASURES, DIM_RELS, cells) == []
    assert builds[0] > 0


# ---- the memo's key ----------------------------------------------------------

def _pair_model():
    return {"Sales": {"columns": ["Date", "Ship", "Amount", "Cat", "Note"],
                      "rows": [[DAYS[i], DAYS[(i + 40) % 90], float(2 ** (i % 7)), "ab"[i % 2], ["x", None, "y"][i % 3]]
                               for i in range(60)]},
            "Calendar": {"columns": ["Date", "Year", "Month"], "rows": [[d, d.year, d.month] for d in DAYS]},
            "D": {"columns": ["Cat", "Grp"], "rows": [["a", "g1"], ["b", "g2"]]}}


PAIR_RELS = [
    {"FromTable": "Sales", "FromColumn": "Date", "ToTable": "Calendar", "ToColumn": "Date", "IsActive": 1,
     "FromCardinality": 2, "ToCardinality": 1},
    {"FromTable": "Sales", "FromColumn": "Ship", "ToTable": "Calendar", "ToColumn": "Date", "IsActive": 0,
     "FromCardinality": 2, "ToCardinality": 1},
    {"FromTable": "Sales", "FromColumn": "Cat", "ToTable": "D", "ToColumn": "Cat", "IsActive": 1,
     "FromCardinality": 2, "ToCardinality": 1},
]
PAIRS = {
    "userelationship": ("SUM(Sales[Amount])",
                        "CALCULATE(SUM(Sales[Amount]), USERELATIONSHIP(Sales[Ship], 'Calendar'[Date]))",
                        {"Calendar.Month": [2]}),
    "all of the table": ("SUM(Sales[Amount])", "CALCULATE(SUM(Sales[Amount]), ALL(Sales))", {"D.Grp": ["g1"]}),
    "keepfilters": ('CALCULATE(SUM(Sales[Amount]), D[Grp] = "g2")',
                    'CALCULATE(SUM(Sales[Amount]), KEEPFILTERS(D[Grp] = "g2"))', {"D.Grp": ["g1"]}),
    "row context": ("SUMX(VALUES(D[Grp]), SUM(Sales[Amount]))",
                    "SUMX(VALUES(D[Grp]), CALCULATE(SUM(Sales[Amount])))", {}),
    "min and max": ("MIN(Sales[Amount])", "MAX(Sales[Amount])", {"D.Grp": ["g2"]}),
    "distinct counts": ("DISTINCTCOUNT(Sales[Note])", "DISTINCTCOUNTNOBLANK(Sales[Note])", {}),
    "count and counta": ("COUNT(Sales[Note])", "COUNTROWS(Sales)", {"D.Grp": ["g2"]}),
}


@pytest.mark.parametrize("pair", list(PAIRS))
def test_memo_keys_keep_apart_what_differs(pair):
    a, b, fc = PAIRS[pair]
    ms = {"A": a, "B": b}
    tables = _pair_model()
    cells = [("A", fc, {}), ("B", fc, {})]
    assert _differential(tables, ms, PAIR_RELS, cells) == []
    with _plain_path():
        de._SHARED_FILTER_CACHES.clear()
        va = de.evaluate_measure("A", tables, ms, dict(fc), relationships=PAIR_RELS)
        de._SHARED_FILTER_CACHES.clear()
        vb = de.evaluate_measure("B", tables, ms, dict(fc), relationships=PAIR_RELS)
    assert va != vb                          # the pair does tell the key apart


def _fallback_model():
    # Sales has no relationship: a Cal filter reaches it through the date
    # table's date column only (#218)
    return {"Sales": {"columns": ["Date", "Amount"],
                      "rows": [[datetime(2019, 5, 1), 1.0], [datetime(2020, 5, 1), 10.0],
                               [datetime(2020, 6, 1), 100.0]]},
            "Cal": {"columns": ["Date", "Year", "Shifted"],
                    "rows": [[datetime(2019, 5, 1), 2019, datetime(2020, 5, 1)],
                             [datetime(2020, 5, 1), 2020, datetime(2020, 6, 1)],
                             [datetime(2020, 6, 1), 2020, datetime(2021, 1, 1)]]},
            "Other": {"columns": ["Date", "Year"], "rows": [[datetime(2019, 5, 1), 2019]]}}


FALLBACK_MEASURES = {"S": "SUM(Sales[Amount])", "M": "MIN(Sales[Amount])",
                     # ALL(Sales) keeps the date-table filter away from Sales
                     # without removing it from the filter set (_all_snapshot)
                     "SA": "CALCULATE(SUM(Sales[Amount]), ALL(Sales))"}
FALLBACK_CELLS = [("S", {"Cal.Year": [2020]}, {"date_table": "Cal"}),
                  ("S", {"Cal.Year": [2020]}, {"date_table": "Other"}),
                  ("S", {"Cal.Year": [2020]}, {"date_table": "Cal", "date_column": "Shifted"}),
                  ("M", {"Cal.Year": [2020]}, {"date_table": "Cal"}),
                  ("M", {"Cal.Year": [2020]}, {"date_table": "Other"}),
                  ("SA", {"Cal.Year": [2020]}, {"date_table": "Cal"})]


def test_date_table_date_column_and_all_snapshot_are_in_the_key():
    tables = _fallback_model()
    assert _differential(tables, FALLBACK_MEASURES, [], FALLBACK_CELLS) == []
    with _plain_path():
        got = []
        for name, fc, kw in FALLBACK_CELLS:
            de._SHARED_FILTER_CACHES.clear()
            got.append(de.evaluate_measure(name, tables, FALLBACK_MEASURES, dict(fc), relationships=[], **kw))
    # the cells do tell the key's parts apart (#218)
    assert got == [110.0, 111.0, 100.0, 10.0, 1.0, 111.0]


def test_a_table_named_in_another_case_shares_the_entry():
    tables = _pair_model()
    ms = {"A": "SUM(Sales[Amount])", "B": "SUM(sales[amount])", "C": "SUM('SALES'[Amount])"}
    got = {m: de.evaluate_measure(m, tables, ms, {"D.Grp": ["g1"]}, relationships=PAIR_RELS) for m in ms}
    assert got["A"] == got["B"] == got["C"] == sum(2.0 ** (i % 7) for i in range(0, 60, 2))
    assert len(_kinds(tables, "agg")) == 1


def test_a_missing_column_is_never_memoized():
    tables = {"T": {"columns": ["v"], "rows": [[1.0], [2.0]]}}
    assert de.evaluate_measure("M", tables, {"M": "SUM(T[w])"}, relationships=[]) is None
    assert not [k for k in _kinds(tables, "agg") if k[3] < 0]
    assert de.evaluate_measure("M", tables, {"M": "SUM(T[v])"}, relationships=[]) == 3.0
    # the model's schema refuses it, memo entries for the table or not (#132)
    ctx = de.DAXContext(tables, {}, relationships=[])
    ctx.model_columns = {"T": ["v"]}
    with pytest.raises(de._DAXEvaluationError):
        de.DAXEngine()._eval_expr("SUM(T[w])", ctx)
    tables["T"]["columns"].append("w")
    for row, w in zip(tables["T"]["rows"], (10, 20)):
        row.append(w)
    assert de.evaluate_measure("M", tables, {"M": "SUM(T[w])"}, relationships=[]) == 30


def test_a_floating_relative_date_is_not_memoized():
    today = datetime.combine(datetime.today().date(), datetime.min.time())
    tables = {"F": {"columns": ["When", "v"], "rows": [[today - timedelta(days=i), 1.0] for i in range(20)]}}
    ms = {"S": "SUM(F[v])"}
    floating = {"F.When": {"relative_date": {"last": 6, "unit": "day"}}}
    assert de.evaluate_measure("S", tables, ms, floating, relationships=[]) == 7.0
    assert _kinds(tables, "agg") == []
    anchored = {"F.When": {"relative_date": {"last": 6, "unit": "day", "anchor": today.date().isoformat()}}}
    assert de.evaluate_measure("S", tables, ms, anchored, relationships=[]) == 7.0
    assert len(_kinds(tables, "agg")) == 1


def test_a_memo_hit_charges_the_budget():
    tables = {"T": {"columns": ["v"], "rows": [[1.0], [2.0]]}}
    engine = de.DAXEngine()
    ctx = de.DAXContext(tables, {}, relationships=[])
    used = []
    for _ in range(3):
        before = ctx._eval_calls
        assert engine._eval_expr("SUM(T[v])", ctx) == 3.0
        used.append(ctx._eval_calls - before)
    assert used[0] == used[1] == used[2] >= 1


def test_entries_are_named_and_weighed(eager):
    tables = _sales_model()
    for d in DAYS[10:14]:
        de.evaluate_measure("RT", tables, MEASURES, {"Calendar.Date": [d]}, relationships=RELS)
    cache = _model_cache(tables)
    owners = [cache[k] for k in _kinds(tables, "range-owners")]
    axes = [cache[k] for k in _kinds(tables, "range-axis")]
    partials = [(k, cache[k]) for k in _kinds(tables, "range-partials")]
    assert len(owners) == 1 and len(axes) == 1 and len(partials) == 1
    # weighed by what they hold (#229): an owner per fact row; a position
    # per date, the blank row and "no row", and the 90 keys; two running
    # counts over the 90 dates, the base filter set the key holds, and the
    # scan replayed over the window (#243: these Doubles do not add exactly
    # in any order) -- a term per segment, run and chunk, with a position
    # per fact row, and for a chunk its rows by position (order, sorted
    # positions) and its last window's mask (_RangePartials._window_values,
    # #348); the axis holds each key's number of D rows as
    # well (_range_key_position)
    assert len(owners[0]) == len(tables["Sales"]["rows"])
    assert len(axes[0]) == 90 + 2 + 90 + 90
    pkey, part = partials[0]
    assert part.mode == "kernel" and part.sums is None
    terms = part.terms
    assert sum(len(t[2]) for t in terms if t[0] != "s") == len(tables["Sales"]["rows"])
    assert len(part) == 2 * 91 + len(pkey[5]) + len(terms) + sum(
        len(t[2]) * (1 if t[0] == "r" else 4) for t in terms if len(t) > 2)
    assert all(isinstance(k[1], str) for k in cache if isinstance(k, tuple) and len(k) > 1
               and k[1] in ("agg", "range-seen", "range-partials", "range-owners", "range-axis",
                            "range-hops"))


def test_markers_hold_no_filter_set():
    """A base asked once leaves a marker of what its scan cost. It is keyed by
    the base's hash, not its filter set: the frozenset a marker key held cost
    1 to 2.6 kB per marker with 12 to 30 dimension columns (mem_rowrt.py)."""
    r = random.Random(3)
    tables = _sales_model()
    tables["Product"] = {"columns": ["PK", "Cat"] + [f"A{i}" for i in range(12)],
                         "rows": [[i, f"C{i % 6}"] + [f"p{i}_{a}" for a in range(12)] for i in range(30)]}
    tables["Sales"]["columns"].append("PK")
    for row in tables["Sales"]["rows"]:
        row.append(r.randrange(30))
    rels = RELS + [{"FromTable": "Sales", "FromColumn": "PK", "ToTable": "Product", "ToColumn": "PK",
                    "IsActive": 1, "FromCardinality": 2, "ToCardinality": 1}]
    for row in tables["Product"]["rows"]:
        fc = {f"Product.{c}": [v] for c, v in zip(tables["Product"]["columns"], row)}
        _ev("RT", tables, MEASURES, fc, rels)
    cache = _model_cache(tables)
    markers = _kinds(tables, "range-seen")
    assert len(markers) == 30
    for k in markers:
        # nothing per filter set: the one frozenset is the model's date-part
        # joins, one object per relationship set
        assert not any(isinstance(part, (set, dict, list)) for part in k)
        assert [part for part in k if isinstance(part, frozenset)] == [frozenset()]
        n, total, first, per_row = cache[k]
        assert n == 1 and total == first > 0


# ---- the differential over random models -------------------------------------

AGGS = ["SUM", "AVERAGE", "COUNT", "COUNTA", "MIN", "MAX", "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK"]


def _fuzz_measures() -> dict:
    m = {a: f"{a}(F[Amt])" for a in AGGS}
    for a in ("SUM", "COUNT", "AVERAGE"):
        for col in ("Amt", "Q"):
            m[f"RT{a}{col}"] = f"CALCULATE({a}(F[{col}]), Cal[Date] <= MAX(Cal[Date]))"
            m[f"Aft{a}{col}"] = f"CALCULATE({a}(F[{col}]), Cal[Date] > MIN(Cal[Date]))"
            m[f"DX{a}{col}"] = f"CALCULATE({a}(F[{col}]), D[X] <= 3)"
    m["Win"] = "CALCULATE(SUM(F[Q]), Cal[Date] >= MIN(Cal[Date]) - 3, Cal[Date] <= MAX(Cal[Date]))"
    m["Capped"] = "IF(MAX(Cal[Date]) <= CALCULATE(MAX(F[When]), REMOVEFILTERS()), SUM(F[Amt]))"
    m["RowCtx"] = "SUMX(VALUES(D[Cat]), SUM(F[Amt]))"
    m["AllF"] = "CALCULATE(SUM(F[Amt]), ALL(F))"
    return m


FUZZ_MEASURES = _fuzz_measures()


def _fuzz_model(seed: int):
    r = random.Random(seed)
    first = datetime(2024, 1, 1)
    days = [first + timedelta(days=i) for i in range(r.randint(8, 20))]
    keytype = ["int", "str01", "float", "str"][seed % 4]
    n = r.randint(3, 6)
    keys = {"int": list(range(1, n + 1)), "float": [float(k) for k in range(1, n + 1)],
            "str": [f"k{k}" for k in range(1, n + 1)], "str01": ["01", "1", "7", "07", "2", "3"][:n]}[keytype]
    d_keys = keys + ([keys[0]] if r.random() < 0.3 else [])
    xs = r.choice([[0, 1, 2, 3, 4, 5], [0.5, 2.5, 3.0, -1.0], [None, 1, 2, 3, 5]])
    d_rows = [[k, r.choice("abc"), r.choice(xs)] for k in d_keys]
    amt_kind = ["float", "int", "mixed", "dirty", "nonfinite", "big"][seed % 6]
    when = days + ([datetime(2024, 1, 3, 12, 30), None] if r.random() < 0.3 else [])
    fkeys = keys + ([99, None] if r.random() < 0.5 else []) + ([1] if keytype == "str01" else [])
    rows = []
    for _ in range(r.randint(20, 80)):
        amt = {"float": lambda: r.random() * 10 ** r.randint(-3, 9),
               "int": lambda: r.randint(-50, 2 ** 20),
               "mixed": lambda: r.choice([r.randint(0, 9), round(r.uniform(0, 9), 3)]),
               "dirty": lambda: r.choice([0.1, 0.2, None, "n/a", True, 3.0, ""]),
               "nonfinite": lambda: r.choice([0.1, 1.5, math.inf, -math.inf, math.nan]),
               "big": lambda: r.choice([2.0 ** 53, 1.0, -2.0 ** 53, 0.1])}[amt_kind]()
        rows.append([r.choice(fkeys), r.choice(when), amt, r.choice([r.randint(0, 9), None])])
    if seed % 2:
        rows.sort(key=lambda row: (row[1] is None, row[1] or first))
    card = [(2, 1), (2, 1), (1, 1), (2, 2)][seed % 4 if seed % 3 else 0]
    rels = [{"FromTable": "F", "FromColumn": "DKey", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
             "FromCardinality": card[0], "ToCardinality": card[1],
             "CrossFilteringBehavior": 2 if card == (1, 1) else 1},
            {"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    tables = {"D": {"columns": ["Key", "Cat", "X"], "rows": d_rows},
              "Cal": {"columns": ["Date", "Year"], "rows": [[d, d.year] for d in days]},
              "F": {"columns": ["DKey", "When", "Amt", "Q"], "rows": rows}}
    contexts = []
    for _ in range(5):
        fc: dict = {}
        if r.random() < 0.8:
            fc["Cal.Date"] = [r.choice(days)]
        if r.random() < 0.4:
            fc["D.Cat"] = r.sample("abc", r.randint(1, 2))
        contexts.append(fc)
    return tables, rels, contexts


@pytest.mark.parametrize("seed", range(12))
def test_differential_on_random_models(seed, eager):
    tables, rels, contexts = _fuzz_model(seed)
    cells = [(m, fc, {}) for fc in contexts for m in FUZZ_MEASURES]
    assert _differential(tables, FUZZ_MEASURES, rels, cells) == []


# ---- planted defects: the differential must see each --------------------------

def _lax_owners(ctx, tname, tbl, fk_idx, dname, dtbl, pk_idx):
    """_range_owners without the disjointness check: the last claim wins."""
    nd = len(dtbl["rows"])
    own = array("i", [nd + 1]) * len(tbl["rows"])
    vmap = ctx._value_index_map(tbl, fk_idx)
    numeric = not ctx._text_column(tbl, fk_idx)
    for j, r in enumerate(dtbl["rows"]):
        for k in ctx._hop_keys(dname, [r], False, pk_idx, tname, fk_idx):
            for s in de._index_spellings(k, numeric):
                for i in vmap.get(s, ()):
                    own[i] = j
    return de._RangeOwners(own, nd)


def _any_hop(ctx, tname, tbl, dname, dtbl):
    """_range_hop admitting any relationship, many-to-many included."""
    rel = ctx._rel_dir.get((tname, dname))
    if not rel:
        return None
    return (ctx._find_col_idx(tbl["columns"], rel["from_col"]),
            ctx._find_col_idx(dtbl["columns"], rel["to_col"]))


def _rounded_totals(real):
    """_RangePartials answering a Double column's window with its correctly
    rounded total -- exact integers over a power of two, rounded once, as
    0.9.147 did (#246) -- where the scan adds in its own order (#243)."""
    def init(self, keys, pos, vals, nkey=0, currency=False, se=None):
        real(self, keys, pos, vals, nkey, currency, se)
        if self.mode == "kernel":
            nums = [(p, v) for p, v in zip(pos, vals) if isinstance(v, (int, float))]
            ratios = [v.as_integer_ratio() for _p, v in nums]
            shift = max(den.bit_length() for _n, den in ratios) - 1
            self._exact(len(keys), [p for p, _v in nums],
                        [num << (shift - den.bit_length() + 1) for num, den in ratios])
            self.mode, self.scale = "float", shift
    return init


def _base_dimension_filters_ignored(real):
    """_range_partials reading the base without its filters on D."""
    def partials(ctx, tname, tbl, col_idx, rkey, dname, dtbl, owners, axis, nkey, layout=None):
        drop = [k for k in ctx.filter_context if k.startswith(dname + ".") and k != rkey]
        return real(ctx.without_filters(drop), tname, tbl, col_idx, rkey, dname, dtbl, owners, axis, nkey,
                    layout)
    return staticmethod(partials)


def _stale_date_part_joins(mp):
    """0.9.147's joins, planted (issue #250): the DatePartOnly joins of the
    ACTIVE relationships only, and a USERELATIONSHIP context keeping those
    of the set it came from."""
    real_ri = de._relationship_indexes
    real_wr = de.DAXContext.with_relationships

    def active_only(rels):
        dp = set()
        for r in rels:
            if r.get('IsActive') and r.get('JoinOnDateBehavior') == 2:
                dp.add((r['FromTable'], r['FromColumn'], r['ToTable'], r['ToColumn']))
                dp.add((r['ToTable'], r['ToColumn'], r['FromTable'], r['FromColumn']))
        return real_ri(rels)[:6] + (frozenset(dp),)

    def stale(self, relationships):
        ctx = real_wr(self, relationships)
        ctx._date_part_joins = self._date_part_joins
        return ctx
    mp.setattr(de, "_relationship_indexes", active_only)
    mp.setattr(de.DAXContext, "with_relationships", stale)


def _key_without(position):
    real = de.DAXEngine._agg_memo_key

    def key(ctx, kind, tname, tbl, idx, *rest):
        got = real(ctx, kind, tname, tbl, idx, *rest)
        return None if got is None else got[:position] + (None,) + got[position + 1:]
    return staticmethod(key)


def _plant_cases():
    sorted_sales = _sales_model(sort=True)
    return {
        "no disjointness check": (
            lambda mp: mp.setattr(de.DAXEngine, "_range_owners_uncached", staticmethod(_lax_owners)),
            _numeric_fact_model(), TEXT_MEASURES, TEXT_RELS, [(m, {}, {}) for m in TEXT_MEASURES]),
        "many-to-many admitted": (
            lambda mp: (mp.setattr(de.DAXEngine, "_range_hop", staticmethod(_any_hop)),
                        mp.setattr(de.DAXEngine, "_range_owners_uncached", staticmethod(_lax_owners))),
            _mm_model(), MM_MEASURES, MM_RELS, [(m, {}, {}) for m in MM_MEASURES]),
        "Double totals correctly rounded": (
            lambda mp: mp.setattr(de._RangePartials, "__init__", _rounded_totals(de._RangePartials.__init__)),
            sorted_sales, MEASURES, RELS, [("AmtWin", {"Calendar.Date": [d]}, {}) for d in DAYS[10:30]]),
        "the blank row ignored": (
            lambda mp: mp.setattr(de._RangePartials, "answer",
                                  (lambda real: lambda self, kind, lo, hi, blank: real(self, kind, lo, hi, False))(
                                      de._RangePartials.answer)),
            _blank_model(), BLANK_MEASURES, BLANK_RELS, [(m, {}, {}) for m in BLANK_MEASURES]),
        "the base's filters on the dimension ignored": (
            lambda mp: mp.setattr(de.DAXEngine, "_range_partials",
                                  _base_dimension_filters_ignored(de.DAXEngine._range_partials)),
            _blank_model(), BLANK_MEASURES, BLANK_RELS,
            [(m, {"D.k": [1, 2, 3]}, {}) for m in BLANK_MEASURES]),
        "stale date-part joins admitted": (
            _stale_date_part_joins, _dp_model(), DP_MEASURES, DP_RELS, DP_P_CELLS),
        "memo key without the date table": (
            lambda mp: mp.setattr(de.DAXEngine, "_agg_memo_key", _key_without(7)),
            _fallback_model(), FALLBACK_MEASURES, [], FALLBACK_CELLS),
        "memo key without the ALL() snapshot": (
            lambda mp: mp.setattr(de.DAXEngine, "_agg_memo_key", _key_without(5)),
            _fallback_model(), FALLBACK_MEASURES, [], [FALLBACK_CELLS[0], FALLBACK_CELLS[-1]]),
        "memo key without the relationship set": (
            lambda mp: mp.setattr(de.DAXEngine, "_agg_memo_key", _key_without(6)),
            _pair_model(), {"A": PAIRS["userelationship"][0], "B": PAIRS["userelationship"][1]}, PAIR_RELS,
            [("A", {"Calendar.Month": [2]}, {}), ("B", {"Calendar.Month": [2]}, {})]),
    }


PLANTS = list(_plant_cases())


@pytest.mark.parametrize("plant", PLANTS)
def test_planted_defects_are_seen(plant, monkeypatch, eager):
    install, tables, ms, rels, cells = _plant_cases()[plant]
    assert _differential(tables, ms, rels, cells) == []
    install(monkeypatch)
    assert _differential(tables, ms, rels, cells) != [], plant
