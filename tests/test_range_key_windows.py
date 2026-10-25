"""A grouped key as the running-total route's window, comparisons on the
fact table's own columns in its base, and a grouped batch building at once.

What was slow: a visual grouped by a dimension key -- CALCULATE([Total],
Transactions[Amount] > 0, Transactions[Category] = "Income") by
'Calendar'[Date] -- scanned the fact rows once per row: the route answered
only an ordering comparison on another table (a running total's window), and
refused a filter set with any comparison on the fact table itself. And a
base's totals were built only once its scans had paid for them twenty times
over (_range_pays), so a visual's first several hundred rows were scans even
where every row asks the same base.

Power BI Desktop 2.157 answers such a visual with one storage-engine scan per
aggregation, grouped by the query's columns, and evaluates each row over that
scan: a running total and an income by date over 4,017 dates took 9 scans
(SUM of the amount by date with the filters pushed in; SUM by date over the
dates the window reaches; MIN and MAX of the date), the formula engine doing
the rest. Here:

- under the query's grouping (GroupByValues) by a key of another table D,
  the window is that key's one position of D's sorted keys, read off the
  same exact per-key totals as a running total (_range_key_window);
- an ordering comparison on the fact table's own column stays in the base,
  which the scan's own filtering applies;
- pbix_evaluate_dax_batch tells the route how many later contexts of the
  call are rows of the same grouped query (set_batch_ahead), and while any
  are, a base's totals are built on its first request, as Desktop scans once
  for the query; a lone call keeps the paying rule.

Where the expected values come from: the engine's plain path on a fresh
cache per cell (test_issue231_aggregate_memo's _differential), and for the
batch, pbix_evaluate_dax's own answer per context. Every value must be equal
in type and bit for bit. Each new condition is also planted broken, and the
differential must report it.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

import pytest

from pbix_mcp import server
from pbix_mcp.dax import engine as de
from tests.test_issue231_aggregate_memo import _differential, _fuzz_model
from tests.test_issue233_evaluate_dax_batch import DAY, _one_call, _write_shop_model

pytestmark = pytest.mark.unit

G = de.GroupByValues


@pytest.fixture
def eager(monkeypatch):
    """A lone call's totals built on a base's second request (the paying
    rule with a factor of 0)."""
    monkeypatch.setattr(de, "_RANGE_BUILD_FACTOR", 0.0)


@pytest.fixture
def grouped(monkeypatch):
    """Every request a row of a grouped batch with rows to come."""
    monkeypatch.setattr(de, "_batch_ahead", lambda: 1)


@pytest.fixture
def builds(monkeypatch):
    count = [0]
    real = de._RangePartials.__init__

    def init(self, *a, **k):
        count[0] += 1
        real(self, *a, **k)

    monkeypatch.setattr(de._RangePartials, "__init__", init)
    return count


def _measures() -> dict:
    m = {}
    for a in ("SUM", "AVERAGE", "COUNT", "COUNTA"):
        for col in ("Amt", "Q"):
            m[f"{a}{col}"] = f"{a}(F[{col}])"
            m[f"Own{a}{col}"] = f"CALCULATE({a}(F[{col}]), F[Q] > 3)"
            m[f"OwnRT{a}{col}"] = f"CALCULATE({a}(F[{col}]), F[Q] >= 2, Cal[Date] <= MAX(Cal[Date]))"
    m["OwnIn"] = 'CALCULATE(SUM(F[Amt]), F[Q] <= 5, D[Cat] = "a")'
    m["Nested"] = "IF([SUMQ] > 0, [OwnSUMAmt])"
    return m


MEASURES = _measures()


def _contexts(tables, seed: int) -> list:
    """Rows of grouped queries over the model: by Cal[Date] (with and without
    a slicer on D), by D[X] (BLANK, and keys spelled as int and float), the
    BLANK group, and a key no row holds."""
    r = random.Random(seed)
    days = [row[0] for row in tables["Cal"]["rows"]]
    xs = sorted({row[2] for row in tables["D"]["rows"]}, key=lambda x: (x is None, x if x is not None else 0))
    out: list = []
    for d in days:
        out.append({"Cal.Date": G([d])})
        if r.random() < 0.3:
            out.append({"Cal.Date": G([d]), "D.Cat": r.sample("abc", r.randint(1, 2))})
    out += [{"D.X": G([x])} for x in xs]
    out += [{"Cal.Date": G([None])}, {"D.X": G([None])}, {"D.X": G([123456])},
            {"Cal.Date": G([days[0] + timedelta(days=400)])}]
    return out


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("mode", ["eager", "grouped"])
def test_differential_on_random_models(seed, mode, request):
    request.getfixturevalue(mode)
    tables, rels, _ = _fuzz_model(seed)
    cells = [(m, fc, {}) for fc in _contexts(tables, seed) for m in MEASURES]
    assert _differential(tables, MEASURES, rels, cells, repeat=1) == []


# ---- where the route answers, and builds ---------------------------------------

DAYS = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(30)]
RELS = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
         "FromCardinality": 2, "ToCardinality": 1}]


def _model() -> dict:
    r = random.Random(7)
    rows = [[r.choice(DAYS), r.random() * 10 ** r.randint(-2, 6), r.randint(0, 9)] for _ in range(600)]
    return {"Cal": {"columns": ["Date"], "rows": [[d] for d in DAYS]},
            "F": {"columns": ["When", "Amt", "Q"], "rows": rows}}


SIMPLE = {"Income": "CALCULATE(SUM(F[Amt]), F[Q] > 3)", "Total": "SUM(F[Amt])"}


def test_a_grouped_query_builds_once_and_answers_every_row(builds, grouped, scans):
    tables = _model()
    cells = [(m, {"Cal.Date": G([d])}, {}) for d in DAYS for m in SIMPLE]
    assert _differential(tables, SIMPLE, RELS, cells, repeat=1) == []
    # one build per base (Income's and Total's) per pass of the differential's two orders
    assert 0 < builds[0] <= 4
    # the engine alone, on a fresh cache: the fact rows read once per base, not once per day
    de._SHARED_FILTER_CACHES.clear()
    scans.clear()
    for name, fc, _kw in cells:
        de.evaluate_measure(name, tables, SIMPLE, dict(fc), relationships=RELS)
    assert len([s for s in scans if s[0] == "F"]) <= 2


def test_a_lone_call_keeps_the_paying_rule(builds):
    tables = _model()
    fc = {"Cal.Date": G([DAYS[3]])}
    de._SHARED_FILTER_CACHES.clear()
    for d in DAYS[:3]:
        de.evaluate_measure("Income", tables, SIMPLE, {"Cal.Date": G([d])}, relationships=RELS)
    de.evaluate_measure("Income", tables, SIMPLE, fc, relationships=RELS)
    assert builds[0] == 0


def test_a_comparison_on_another_table_is_still_the_window_not_the_base(builds, grouped):
    """A running total under a grouping by date: the CALCULATE replaces the
    date's filter, so the comparison is the window and nothing is grouped."""
    tables = _model()
    ms = {"RT": "CALCULATE(SUM(F[Amt]), F[Q] > 3, Cal[Date] <= MAX(Cal[Date]))"}
    cells = [("RT", {"Cal.Date": G([d])}, {}) for d in DAYS]
    assert _differential(tables, ms, RELS, cells, repeat=1) == []
    assert builds[0] > 0


@pytest.fixture
def scans(monkeypatch):
    seen: list = []
    real = de.DAXContext._get_column_data_uncached

    def spy(self, table_name, column_name):
        seen.append((table_name, column_name))
        return real(self, table_name, column_name)

    monkeypatch.setattr(de.DAXContext, "_get_column_data_uncached", spy)
    return seen


# ---- planted defects: the differential must see each ----------------------------

def _window_plant(change):
    real = de.DAXEngine._range_key_window

    def window(ctx, dtbl, x_idx, axis, spec):
        return change(real, ctx, dtbl, x_idx, axis, spec)
    return staticmethod(window)


def _blank_admitted(real, ctx, dtbl, x_idx, axis, spec):
    """A BLANK in the set read as an empty window, not left to the scan."""
    if isinstance(spec, list) and any(v is None for v in spec):
        return 0, 0
    return real(ctx, dtbl, x_idx, axis, spec)


def _one_too_wide(real, ctx, dtbl, x_idx, axis, spec):
    got = real(ctx, dtbl, x_idx, axis, spec)
    return None if got is None else (got[0], got[1] + 1)


def _no_coverage_check(real, ctx, dtbl, x_idx, axis, spec):
    """The window taken from the first row the set keeps, without checking
    that it keeps every D row of that key."""
    if not isinstance(spec, list) or any(v is None for v in spec):
        return None
    rows = ctx._column_filter_rows(dtbl, x_idx, spec)
    if not rows:
        return 0, 0
    p = axis.pos[min(rows)]
    return (p, p + 1) if p >= 0 else None


def _shared_key_model():
    """D[X] keys that two D rows share, one of them filtered away by D[Cat]:
    a window must be a key every kept D row holds."""
    d = [[1, "a", 1], [2, "b", 1], [3, "a", 2], [4, "b", 3]]
    f = [[1, 10.5, 1], [2, 0.25, 2], [3, 4.0, 3], [4, 1.5, 4], [2, 8.0, 5], [None, 2.0, 6]]
    tables = {"D": {"columns": ["Key", "Cat", "X"], "rows": d},
              "F": {"columns": ["DKey", "Amt", "Q"], "rows": f}}
    rels = [{"FromTable": "F", "FromColumn": "DKey", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    return tables, rels


def _plant_cases():
    tables, rels, _ = _fuzz_model(3)
    fuzz_cells = [(m, fc, {}) for fc in _contexts(tables, 3) for m in ("SUMAmt", "OwnSUMAmt", "COUNTQ")]
    st, srels = _shared_key_model()
    ms = {"S": "SUM(F[Amt])", "C": "COUNT(F[Amt])"}
    shared_cells = [(m, fc, {}) for m in ms
                    for fc in ({"D.X": G([1])}, {"D.X": G([1]), "D.Cat": ["a"]}, {"D.X": G([None])})]
    return {
        "a BLANK group read as an empty window": (_blank_admitted, tables, MEASURES, rels, fuzz_cells),
        "a window one key too wide": (_one_too_wide, tables, MEASURES, rels, fuzz_cells),
        "no check that the set keeps every row of its key": (
            _no_coverage_check, *_with_subset_spec(st, srels, ms, shared_cells)),
    }


def _with_subset_spec(tables, rels, ms, cells):
    """The shared-key model with D[X] holding a date and its own serial
    number: one position of the sorted keys (_as_number), but the scan's
    In-set keeps the date's row alone, or the number's."""
    t = {k: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]} for k, v in tables.items()}
    t["D"]["rows"][0][2] = datetime(2023, 3, 15)
    t["D"]["rows"][1][2] = 45000.0          # 2023-03-15's serial
    extra = [(m, {"D.X": G([v])}, {}) for m in ms for v in (datetime(2023, 3, 15), 45000.0)]
    return t, ms, rels, cells + extra


PLANTS = list(_plant_cases())


@pytest.mark.parametrize("plant", PLANTS)
def test_planted_defects_are_seen(plant, monkeypatch, grouped):
    change, tables, ms, rels, cells = _plant_cases()[plant]
    assert _differential(tables, ms, rels, cells, repeat=1) == []
    monkeypatch.setattr(de.DAXEngine, "_range_key_window", _window_plant(change))
    assert _differential(tables, ms, rels, cells, repeat=1) != [], plant


# ---- the batch -------------------------------------------------------------------

def test_ahead_counts_the_later_rows_of_the_same_grouped_query():
    items = [
        {"filter_context": {"A.x": [1], "B.y": [2]}, "group_by": ["A.x"]},
        {"filter_context": {"A.x": [2], "B.y": [2]}, "group_by": ["A.x"]},
        {"filter_context": {"A.x": [3], "B.y": [3]}, "group_by": ["A.x"]},   # another slicer
        {"filter_context": {"A.x": [4], "B.y": [2]}, "group_by": ["A.x"]},
        {"filter_context": {"A.x": [4], "B.y": [2]}},                        # not grouped
        "not an object",
    ]
    assert server._batch_ahead_counts(items) == [2, 1, 0, 0, 0, 0]


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("shopskw") / "shopskw.pbix")
    _write_shop_model(path)
    alias = "shopskw"
    assert json.loads(server.pbix_open(path, alias))["success"]
    yield alias
    server.pbix_close(alias, force=True)


def test_a_grouped_batch_answers_as_single_calls_and_builds_at_once(shop, builds):
    measures = "Units,UnitsToDate,PerDay,Revenue"
    contexts = [{"filter_context": {"Days.Day": [d], "Store.Region": ["North", "South"]}, "group_by": ["Days.Day"]}
                for d in DAY]
    got = json.loads(server.pbix_evaluate_dax_batch(shop, measures, json.dumps(contexts)))
    assert got["success"] and len(got["results"]) == len(contexts)
    assert builds[0] > 0
    assert de._batch_ahead() == 0
    for c, answer in zip(contexts, got["results"]):
        assert answer == _one_call(shop, c, measures)


@pytest.mark.parametrize("mode", ["eager", "grouped"])
def test_the_differential_reaches_the_route(mode, request, builds):
    """Not vacuous: the random models' grouped rows are read off totals."""
    request.getfixturevalue(mode)
    tables, rels, _ = _fuzz_model(0)
    cells = [(m, fc, {}) for fc in _contexts(tables, 0) for m in ("OwnSUMAmt", "SUMQ")]
    assert _differential(tables, MEASURES, rels, cells, repeat=1) == []
    assert builds[0] > 0
