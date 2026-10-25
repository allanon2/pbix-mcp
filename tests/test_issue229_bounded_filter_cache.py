"""Issue #229 (reported in PR #205, by @allanon2): one model's filter-index
cache grew without bound.

_shared_filter_cache bounded the NUMBER of models (four) and nothing else:
every context over a model writes memo entries into its cache, many keyed by
the filter set, and none was ever removed. A running total asked once per
date (CALCULATE(SUM(F[Amt]), Cal[Date] <= MAX(Cal[Date])), 4,017 dates and
20,085 fact rows) grew the process by 1.5 GB by date 2,267 on 0.9.145, and
the FILTER(ALL(Cal), ...) form by 1.5 GB at date 1,404; PR #205 reports 16 GB
on a richer model. Keys alone carried up to 4,096 values per filter, and a
KEEPFILTERS predicate's JSON grew to 120,053 characters by date 3,000.

The cache is now a _FilterIndexCache: entries a filter set names are weighed
and dropped oldest first past a bound; facts about the model (value maps,
key spellings, blank-row tables) are kept and never counted; a dimension
member's related rows ('hop-rows', 'row-expansion') have a cap of their own,
so the one-shot entries of a per-row iteration cannot flush what every group
of a query reuses; long signatures are digests; and the registered models
share one budget, by need. The row-expansion slots, which 0.9.146 kept in the
model's cache, live on the context that runs the iteration and go with it.

Every entry is a pure function of its key, so eviction can only cost time.
The expected answers are computed here from the model rows (running sums
by date) or are the engine's own answers on a fresh model with the default
bound; what is checked is the memory the cache holds, measured independently
of its own accounting (a getsizeof walk of the droppable entries), and that
its accounting holds. Tests marked "guard" pin a property of the design and
pass on 0.9.146 too (structural and member entries survive eviction, answers
under constant eviction, a member's expansion built once for a calculated
table -- which the first draft of this change broke), except the registry's
identity check, which plants a state 0.9.146 cannot reach.
"""
from __future__ import annotations

import gc
import sys
import threading
import weakref
from datetime import datetime, timedelta

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

# The kinds a bounded cache never drops, spelled here so the measurement also
# runs on a cache that knows nothing of them (0.9.146).
KEPT = {'__tables_ref__', 'vmap', 'tmap', 'dpmap', 'key-aliases', 'non-owners', 'dp-keys',
        'unmatched', 'blank-row-tables', 'every-cell-valued', '__related_idx__',
        '__bare_columns__', 'text-col', 'inset-aliases', '__index_unions__',
        '__row_expansion__', 'hop-rows', 'row-expansion',
        # 0.9.147's structural kinds (#230 - #232) and self-bounded memos (#230)
        'is-datetime', 'shield-memo', 'hop-buckets', 'ineq-order', 'range-owners', 'range-axis',
        'xtf-src', 'xtf-rows', 'ineq'}

RUNNING = {
    "cmp": "CALCULATE(SUM('F'[Amt]), 'Cal'[Date] <= MAX('Cal'[Date]))",
    "fcol": "CALCULATE(SUM('F'[Amt]), FILTER(ALL('Cal'[Date]), 'Cal'[Date] <= MAX('Cal'[Date])))",
    "ftab": "CALCULATE(SUM('F'[Amt]), FILTER(ALL('Cal'), 'Cal'[Date] <= MAX('Cal'[Date])))",
}
KEEP = "CALCULATE(SUM('F'[Amt]), KEEPFILTERS(FILTER(ALL('Cal'[Date]), 'Cal'[Date] <= MAX('Cal'[Date]))))"
RICH = dict(RUNNING, **{
    "keep": KEEP,
    "ytd": "TOTALYTD(SUM('F'[Amt]), 'Cal'[Date])",
    "rank": "RANKX(ALL('F'[Acc]), CALCULATE(SUM('F'[Amt])))",
    "share": "DIVIDE(SUM('F'[Amt]), CALCULATE(SUM('F'[Amt]), ALLSELECTED('Cal')))",
    "related": "SUMX('F', RELATED('Cal'[Year]))",
    "trans": "SUMX(VALUES('Cal'[Date]), CALCULATE(COUNTROWS('F')))",
    "rowx": "SUMX('F', [amt])",
    "amt": "SUM('F'[Amt])",
})


def _kind(key):
    if isinstance(key, str):
        return key
    if isinstance(key, tuple) and key:
        if isinstance(key[0], str):
            return key[0]
        if len(key) > 1 and isinstance(key[1], str):
            return key[1]
    return None


_LEAF = (str, bytes, int, float, bool, type(None), datetime)


def _walk(o, seen) -> int:
    stack, n = [o], 0
    while stack:
        x = stack.pop()
        if id(x) in seen:
            continue
        seen.add(id(x))
        n += sys.getsizeof(x)
        if isinstance(x, _LEAF):
            continue
        d = getattr(x, '__dict__', None)
        if isinstance(d, dict):
            stack.append(d)
        if isinstance(x, dict):
            stack.extend(x.keys())
            stack.extend(x.values())
        elif isinstance(x, (list, tuple, set, frozenset)):
            stack.extend(x)
    return n


def droppable_bytes(cache, tables) -> int:
    """What the cache's droppable entries hold, keys included, beyond the
    model and what its kept entries hold anyway."""
    seen = {id(tables)}
    for t in tables.values():
        _walk(t, seen)
    items = list(cache.items())
    for k, v in items:
        if _kind(k) in KEPT:
            _walk(k, seen)
            _walk(v, seen)
    return sum(_walk(k, seen) + _walk(v, seen) for k, v in items if _kind(k) not in KEPT)


def longest_text_in_droppable_keys(cache) -> int:
    best = 0
    for k in list(cache.keys()):
        if _kind(k) in KEPT:
            continue
        stack = [k]
        while stack:
            x = stack.pop()
            if isinstance(x, str):
                best = max(best, len(x))
            elif isinstance(x, (tuple, frozenset)):
                stack.extend(x)
    return best


def model(n=300, per=3):
    days = [datetime(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    tables = {
        "Cal": {"columns": ["Date", "Year"], "rows": [[d, d.year] for d in days]},
        "YearT": {"columns": ["Year"], "rows": [[y] for y in sorted({d.year for d in days})]},
        "F": {"columns": ["Date", "Acc", "Amt"],
              "rows": [[d, f"A{j}", i * per + j] for i, d in enumerate(days) for j in range(per)]},
    }
    rels = [{"FromTable": "F", "FromColumn": "Date", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1},
            {"FromTable": "Cal", "FromColumn": "Year", "ToTable": "YearT", "ToColumn": "Year", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    return days, tables, rels


def wide(members=60, cols=8, facts=600):
    """A fact table over a wide dimension: a row transition expands every
    dimension column (issue #115)."""
    dcols = ["K"] + [f"C{i}" for i in range(cols - 1)]
    tables = {
        "Cal": {"columns": ["Date", "Month"],
                "rows": [[datetime(2020, m, 1), m] for m in range(1, 13)]},
        "D": {"columns": dcols, "rows": [[k] + [f"m{k}_{i}" for i in range(cols - 1)] for k in range(members)]},
        "F": {"columns": ["Date", "K", "Amt"],
              "rows": [[datetime(2020, 1 + i % 12, 1), (i * 7) % members, i] for i in range(facts)]},
    }
    rels = [{"FromTable": "F", "FromColumn": "Date", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1},
            {"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "K", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    return tables, rels


def one_hop(members=30, cols=6, facts=900):
    """A fact table over one dimension and nothing else: a row's expansion
    is its member's memo entry itself (DAXContext._row_expansion)."""
    dcols = ["K"] + [f"C{i}" for i in range(cols - 1)]
    tables = {
        "D": {"columns": dcols, "rows": [[k] + [f"m{k}_{i}" for i in range(cols - 1)] for k in range(members)]},
        "F": {"columns": ["K", "Amt", "Id"], "rows": [[(i * 7) % members, i % 97, i] for i in range(facts)]},
    }
    rels = [{"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "K", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    return tables, rels


def slots_in_cache(tables) -> list:
    """The row-expansion slots 0.9.146 kept in the model's cache."""
    return [k for k in list(de._shared_filter_cache(tables)) if _kind(k) == '__row_expansion__']


def evaluate(tables, rels, measures, day, names=None):
    return de.evaluate_measures_batch(list(names or measures), tables, measures, {"Cal.Date": [day]}, "Cal", "Date",
                                      rels, group_keys={"Cal.Date"}, selected_filters={}, cache={})


def small_limit(monkeypatch, limit, members=None):
    monkeypatch.setattr(de, "_FILTER_CACHE_MIN_WEIGHT", limit, raising=False)
    monkeypatch.setattr(de, "_FILTER_CACHE_CELLS_FACTOR", 0, raising=False)
    if members is not None:
        monkeypatch.setattr(de, "_FILTER_CACHE_MEMBER_MIN", members, raising=False)
        monkeypatch.setattr(de, "_FILTER_CACHE_MEMBER_ROWS_FACTOR", 0, raising=False)


def queued_sum(cache) -> int:
    assert len(cache._keys) == len(cache._weights)
    return sum(cache._weights)


# --- the bound ---------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(RUNNING))
def test_a_running_total_asked_per_date_keeps_the_model_cache_bounded(monkeypatch, name):
    """0.9.146 holds every date's entries: 24 MB here at date 300 (76 MB for
    the FILTER(ALL(Cal), ...) form), growing with the square of the dates.
    20,000 units are about 2 MB. Over the whole-filter-set propagation, which
    stores an entry per date's filter set: #230's per-source route (on by
    default) and #231's column memo store far less on this model, and the
    bound must hold whichever route a model takes."""
    small_limit(monkeypatch, 20_000)
    monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    days, tables, rels = model()
    cache = de._shared_filter_cache(tables)
    running = 0
    for i, d in enumerate(days):
        running += sum(i * 3 + j for j in range(3))
        assert evaluate(tables, rels, {name: RUNNING[name]}, d)[name] == running
        if i % 100 == 99:
            assert droppable_bytes(cache, tables) <= 3_000_000, i
    assert cache.weight <= cache.limit and cache.evicted > 0
    assert cache.weight == queued_sum(cache)


def test_a_keepfilters_predicate_is_keyed_by_a_digest():
    """KEEPFILTERS(FILTER(ALL(Cal[Date]), ...)) is a predicate on every date
    so far: its JSON grew by a date's spelling per date in every key. Asked
    for 3,000 dates on 0.9.146, its longest key text was 120,053 characters
    and 348 MB of keys held 10 MB of values. Past 256 characters the
    signature is a digest. KEEPFILTERS keeps the grouped date, so each
    answer is that date's own total."""
    days, tables, rels = model(200)
    for i, d in enumerate(days):
        assert evaluate(tables, rels, {"keep": KEEP}, d)["keep"] == sum(i * 3 + j for j in range(3))
    assert longest_text_in_droppable_keys(de._shared_filter_cache(tables)) <= 256


def test_long_signatures_are_digests_and_stay_exact():
    sig, xsig = de.DAXContext._filter_sig, de.DAXContext._filter_sig_exact
    dates = [datetime(2020, 1, 1) + timedelta(days=i) for i in range(200)]
    long_pred = {"all": [{"op": "<=", "value": dates[-1]}, {"in": dates[:150]}]}
    s = sig(long_pred)
    assert s[0] == 'D' and len(repr(s)) < 120
    # the same predicate in another key order, and one date apart
    assert sig({"all": long_pred["all"]}) == s == xsig(long_pred)
    assert sig({"all": [{"value": dates[-1], "op": "<="}, {"in": dates[:150]}]}) == s
    assert sig({"all": [{"op": "<=", "value": dates[-1]}, {"in": dates[1:151]}]}) != s
    assert sig({"op": "<=", "value": dates[0]})[0] == 'd'                    # short: as it was
    # an In-set past 64 values: its count and a digest of its typed spellings
    vals = [f"k{i:03d}" for i in range(65)]
    s65 = sig(vals)
    assert s65[0] == 'L' and s65[1] == 65 and s65 == xsig(vals) == sig(list(reversed(vals)))
    assert sig(vals[:-1] + ["k999"]) != s65
    assert sig(list(range(64)) + [1.0]) != sig(list(range(64)) + ["1.0"])     # types apart (#217)
    assert sig(vals[:64])[0] == 'l'
    assert sig([f"v{i}" for i in range(5000)]) is None                          # past 4096: as it was


def test_a_relative_window_keeps_its_day_in_a_digest():
    # a window without an anchor selects other rows after midnight (#220)
    win = {"all": [{"relative_date": {"last": 2, "unit": "day"}}, {"in": [f"v{i:04d}" for i in range(60)]}]}
    s = de.DAXContext._filter_sig(win)
    assert s[0] == 'D' and s[-1] == de.date.today().isoformat()


def test_the_oldest_entries_go_first_and_the_newest_stays(monkeypatch):
    small_limit(monkeypatch, 100)
    _, tables, _ = model(5)
    cache = de._shared_filter_cache(tables)
    for i in range(30):
        cache[("probe", i)] = frozenset(range(10))
    assert ("probe", 29) in cache and ("probe", 28) in cache
    assert ("probe", 0) not in cache
    assert cache.weight <= 100 and cache.weight == queued_sum(cache)


def test_an_entry_larger_than_the_bound_is_kept(monkeypatch):
    small_limit(monkeypatch, 100)
    _, tables, _ = model(5)
    cache = de._shared_filter_cache(tables)
    cache[("probe", "small")] = frozenset(range(5))
    big = frozenset(range(1_000))
    cache[("probe", "big")] = big
    assert cache.get(("probe", "big")) is big
    assert ("probe", "small") not in cache


def test_a_key_is_weighed_by_its_texts(monkeypatch):
    small_limit(monkeypatch, 5_000)
    _, tables, _ = model(5)
    cache = de._shared_filter_cache(tables)
    text = "x" * 200_000
    cache[(1, "probe", text)] = []
    cache[(2, "probe", text + "y")] = []
    assert (1, "probe", text) not in cache and (2, "probe", text + "y") in cache


def test_a_value_is_weighed_one_level_down(monkeypatch):
    """An entry stored by plain assignment (a kind no site weighs) counts
    the collections it holds: a dict of row sets weighs its rows."""
    small_limit(monkeypatch, 1_000_000)
    _, tables, _ = model(5)
    cache = de._shared_filter_cache(tables)
    w0 = getattr(cache, "weight", 0)
    cache[("probe", "rows")] = {"a": frozenset(range(500)), "b": frozenset(range(700))}
    cache[("probe", "list")] = [list(range(300)), "x" * 6_400]
    assert getattr(cache, "weight", 0) - w0 >= 1_200 + 300 + 100
    assert cache.weight == queued_sum(cache)


def test_setdefault_and_update_go_through_the_accounting(monkeypatch):
    small_limit(monkeypatch, 10_000)
    _, tables, _ = model(5)
    cache = de._shared_filter_cache(tables)
    w0 = cache.weight
    got = cache.setdefault((7, "probe"), frozenset(range(50)))
    assert cache.setdefault((7, "probe"), None) is got
    cache.update({(8, "probe"): frozenset(range(20))})
    cache |= {(9, "probe"): [1, 2]}
    assert cache.weight > w0 + 70 and cache.weight == queued_sum(cache)
    # held, structural and member entries are stored, never queued
    n = len(cache._keys)
    cache.setdefault("__index_unions__", {})
    cache[(3, "vmap", 0)] = {"a": frozenset({0})}
    cache[("hop-rows", "F", "Cal", "Date", "int", 1, False, 0)] = ({}, False)
    assert len(cache._keys) == n and len(cache._members) == 1
    cache.clear()
    assert cache.weight == 0 and dict(cache) == {"__tables_ref__": tables}


def test_member_entries_are_cleared_as_a_class_past_their_cap(monkeypatch):
    small_limit(monkeypatch, 1_000_000, members=10)
    _, tables, _ = model(5)
    cache = de._shared_filter_cache(tables)
    for i in range(10):
        cache[("row-expansion", ("F", "D", "K", "int", i))] = i
    assert len(cache._members) == 10
    cache[("row-expansion", ("F", "D", "K", "int", 10))] = 10
    left = [k for k in cache if _kind(k) == "row-expansion"]
    assert left == [("row-expansion", ("F", "D", "K", "int", 10))]
    assert cache.weight == 0            # never weighed against the filter-named bound


# --- what is kept ------------------------------------------------------------

def test_facts_about_the_model_outlive_an_eviction(monkeypatch):
    """Guard: value maps, typed maps, key spellings and blank-row tables are
    never rebuilt because filter-named entries filled the cache."""
    small_limit(monkeypatch, 5_000)
    days, tables, rels = model()
    cache = de._shared_filter_cache(tables)
    evaluate(tables, rels, {"fcol": RUNNING["fcol"]}, days[0])
    held = {k: v for k, v in cache.items() if _kind(k) in KEPT and _kind(k) != '__row_expansion__'}
    assert any(_kind(k) == 'vmap' for k in held)
    for d in days:
        evaluate(tables, rels, {"fcol": RUNNING["fcol"]}, d)
    for k, v in held.items():
        assert cache.get(k) is v, k


def test_members_survive_the_one_shot_entries_of_every_row(monkeypatch):
    """Guard: a query grouped by month whose measure iterates fact rows asks
    every group for the same dimension members. In one FIFO with the
    per-row cross-filter entries they were flushed between groups and
    rebuilt (12 months, 12,000 members: 49 s against 20 s in the review's
    prototype). Each member is built once for the whole query here, under
    a bound the per-row entries pass many times over."""
    small_limit(monkeypatch, 2_000)
    tables, rels = wide()
    measures = {"A": "SUM('F'[Amt])", "X": "SUMX('F', [A])"}
    calls = []
    orig = de.DAXContext._hop_rows

    def counting(self, cur, nxt, rel, keys, cur_blank):
        if len(keys) == 1:
            calls.append((cur, nxt, keys[0], cur_blank))
        return orig(self, cur, nxt, rel, keys, cur_blank)

    monkeypatch.setattr(de.DAXContext, "_hop_rows", counting)
    qc: dict = {}
    for _ in range(2):
        for m in range(1, 13):
            got = de.evaluate_measures_batch(["X"], tables, measures, {"Cal.Month": [m]}, "Cal", "Date", rels,
                                             group_keys={"Cal.Month"}, selected_filters={}, cache=qc)["X"]
            assert got == sum(r[2] for r in tables["F"]["rows"] if r[0].month == m)
    cache = de._shared_filter_cache(tables)
    assert getattr(cache, "evicted", 1) > 0
    assert len(calls) == len(set(calls))          # no member built twice


def _host_spy(hosts, seen=None):
    """A spy on DAXContext._row_expansion that keeps a weak reference to each
    context its slots live on (``host``; 0.9.146 kept them in the model's
    cache and passes none)."""
    orig = de.DAXContext._row_expansion

    def spy(self, t, part, shadow, *host):
        out = orig(self, t, part, shadow, *host)
        if host:
            hosts.append(weakref.ref(host[0]))
        if seen is not None:
            holder = host[0].__dict__ if host else self._filter_idx_cache
            seen.append(bool(holder.get('_rx_slots') or holder.get('__row_expansion__')))
        return out
    return spy


def no_slots_left(hosts) -> bool:
    gc.collect()
    return not any('_rx_slots' in c.__dict__ for c in (h() for h in hosts) if c is not None)


def test_the_row_expansion_slots_go_with_the_evaluation(monkeypatch):
    """0.9.146 kept the last four iterations' slots in the model's cache after
    the query: each pins its iteration's rows and every member's expanded
    columns (395 MB for one SUMX over 40,000 rows of a 20,000 x 30 dimension),
    and a slot only ever serves its own iteration."""
    tables, rels = wide()
    measures = {"A": "SUM('F'[Amt])", "X": "SUMX('F', [A])", "Y": "SUMX('F', [A]) + 1"}
    hosts: list = []
    seen: list = []
    monkeypatch.setattr(de.DAXContext, "_row_expansion", _host_spy(hosts, seen))
    total = sum(r[2] for r in tables["F"]["rows"])
    got = de.evaluate_measures_batch(["X", "Y"], tables, measures, {}, None, None, rels)
    assert got == {"X": total, "Y": total + 1}
    assert de.evaluate_measures_smart(["X"], tables, measures, {}, None, None, rels)["X"] == total
    assert seen and all(seen)                     # they existed while it ran
    assert not slots_in_cache(tables)
    assert no_slots_left(hosts)


def test_a_callers_context_gives_its_slots_back(monkeypatch):
    """A context the caller keeps hosts the iterations of the measure's own
    body; the outermost measure takes their slots back from it."""
    tables, rels = wide()
    measures = {"A": "SUM('F'[Amt])", "X": "SUMX('F', [A])"}
    hosts: list = []
    monkeypatch.setattr(de.DAXContext, "_row_expansion", _host_spy(hosts))
    ctx = de.DAXContext(tables, measures, None, None, {}, rels)
    assert de.DAXEngine().evaluate_measure("X", ctx) == sum(r[2] for r in tables["F"]["rows"])
    assert '_rx_slots' not in ctx.__dict__
    assert not slots_in_cache(tables)
    assert no_slots_left(hosts)


def test_a_calculated_table_builds_each_members_expansion_once(monkeypatch):
    """Guard (fails on the first draft of this change). An iteration of a
    calculated table runs outside any measure, so each row's measure
    reference is an outermost measure. The draft dropped the slots at the
    end of every outermost measure: every row rebuilt its member's
    expansion (30,000 rebuilds for 15,000 rows twice, 11-16% slower). One
    relationship, so a row's expansion is the memo's own entry: the same
    object for every row of a member."""
    from pbix_mcp.dax.calc_tables import evaluate_calc_table_expression
    tables, rels = one_hop()
    outs: list = []
    orig = de.DAXContext._row_expansion

    def spy(self, *args):
        out = orig(self, *args)
        outs.append(out)
        return out
    monkeypatch.setattr(de.DAXContext, "_row_expansion", spy)
    res, err = evaluate_calc_table_expression("ADDCOLUMNS('F', \"x\", [A])", tables, {"A": "SUM('F'[Amt])"}, rels)
    assert err is None
    assert [r[-1] for r in res["rows"]] == [r[1] for r in tables["F"]["rows"]]
    assert len(outs) == 900 and len({id(o) for o in outs}) == 30


def test_threads_running_calculated_tables_leave_no_slots(monkeypatch):
    """A calculated table's iteration runs no measure, so the first draft of
    this change, which keyed the slots per thread in the model's cache and
    dropped them at the end of an outermost measure, never dropped them: 6
    threads left 6 keys and 24 slots pinning 71,940 rows (0.9.146: 4 slots,
    11,987 rows), and a thread that had ended kept its own. They live on the
    context that runs the iteration now, and go with it."""
    from pbix_mcp.dax.calc_tables import evaluate_calc_table_expression
    tables, rels = one_hop(members=60, facts=600)
    hosts: list = []
    monkeypatch.setattr(de.DAXContext, "_row_expansion", _host_spy(hosts))
    amts = [r[1] for r in tables["F"]["rows"]]
    errors: list = []

    def work():
        try:
            for i in range(5):
                res, err = evaluate_calc_table_expression(
                    f"ADDCOLUMNS(FILTER('F', 'F'[Id] >= {i}), \"x\", CALCULATE(SUM('F'[Amt])))", tables, {}, rels)
                assert err is None and [r[-1] for r in res["rows"]] == amts[i:]
        except Exception as e:      # noqa: BLE001
            errors.append(e)
    ths = [threading.Thread(target=work) for _ in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert not errors
    assert not slots_in_cache(tables)
    assert hosts and no_slots_left(hosts)


# --- accounting --------------------------------------------------------------

def test_the_filter_set_weight_is_kept_up_as_contexts_derive(monkeypatch):
    """The weight of a filter-set signature is added up as _filters_sigd
    signs, not walked on each store; it must equal the walk."""
    tables, rels = wide(members=20, cols=5, facts=120)
    bad, heavy = [], []
    orig = de.DAXContext._filters_sig_weight

    def check(self):
        w = orig(self)
        sigd = self._filters_sigd()
        want = 1 + sum(2 + de._part_weight(p) for p in sigd.values())
        if w != want:
            bad.append((w, want))
        if w > 1 + 2 * len(sigd):
            heavy.append(w)
        return w

    monkeypatch.setattr(de.DAXContext, "_filters_sig_weight", check)
    measures = {"A": "SUM('F'[Amt])", "X": "SUMX('F', [A])",
                "Y": "SUMX(VALUES('D'[C1]), CALCULATE([A], ALL('Cal')))",
                "Z": "CALCULATE([X], 'D'[K] IN {1, 2, 3}, KEEPFILTERS('Cal'[Month] <= 6))"}
    for m in (1, 7):
        de.evaluate_measures_batch(list(measures), tables, measures, {"Cal.Month": [m], "D.C2": ["m3_2", "m4_2"]},
                                   "Cal", "Date", rels, group_keys={"Cal.Month"}, selected_filters={})
    assert not bad and heavy            # some signatures weighed more than their filters


def test_eviction_never_changes_an_answer(monkeypatch):
    """Guard: a tiny bound evicts constantly."""
    days, ref_tables, rels = model(90)
    want = [evaluate(ref_tables, rels, RICH, d, [k for k in RICH if k != "amt"]) for d in days]
    small_limit(monkeypatch, 300, members=20)
    _, tables, _ = model(90)
    assert [evaluate(tables, rels, RICH, d, [k for k in RICH if k != "amt"]) for d in days] == want
    assert getattr(de._shared_filter_cache(tables), "evicted", 1) > 0


def test_threads_sharing_one_bounded_cache(monkeypatch):
    """8 threads, constant eviction: no error, the answers of one thread, and
    the bound and its accounting hold (the answers part is a guard)."""
    days, ref_tables, rels = model(40)
    names = ["cmp", "ftab", "keep", "ytd", "rank", "share", "related", "trans", "rowx"]
    want = [evaluate(ref_tables, rels, RICH, d, names) for d in days]
    small_limit(monkeypatch, 2_000, members=50)
    _, tables, _ = model(40)
    errors, got = [], []

    def work():
        try:
            got.append([evaluate(tables, rels, RICH, d, names) for d in days])
        except Exception as e:      # noqa: BLE001
            errors.append(e)
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    try:
        ths = [threading.Thread(target=work) for _ in range(8)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    finally:
        sys.setswitchinterval(old)
    assert not errors and len(got) == 8 and all(g == want for g in got)
    cache = de._shared_filter_cache(tables)
    assert cache.weight == queued_sum(cache) and cache.evicted > 0
    assert droppable_bytes(cache, tables) <= 1_000_000
    assert not slots_in_cache(tables)


# --- the registry ------------------------------------------------------------

class _TakenOnce:
    """A lock that fails, instead of waiting forever, when the thread holding
    it asks for it again."""
    def __init__(self):
        self.held = False

    def acquire(self, blocking=True, timeout=-1):
        assert not self.held, "the registry lock was asked for twice"
        self.held = True
        return True

    def release(self):
        self.held = False

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()


def test_registered_models_share_one_budget_by_need(monkeypatch):
    """The registered caches together weigh at most the total. One working
    model keeps its own limit while the others hold little -- split evenly,
    a second mapping halved it and four held it at the floor -- and past the
    total the heaviest gives back first, never below an even share."""
    for name, v in (("_FILTER_CACHE_TOTAL_WEIGHT", 1_000_000), ("_FILTER_CACHE_MIN_WEIGHT", 800_000),
                    ("_FILTER_CACHE_CELLS_FACTOR", 0), ("_FILTER_CACHE_SHARE_STEP", 10_000)):
        monkeypatch.setattr(de, name, v, raising=False)
    monkeypatch.setattr(de, "_SHARED_FILTER_CACHES", {})
    models = [model(5)[1] for _ in range(de._SHARED_FILTER_CACHE_MAX + 1)]
    caches = [de._shared_filter_cache(t) for t in models[:4]]
    entry = frozenset(range(1_000))             # 1,002 units with its key

    def held(c):
        return sum(1 for k in list(c) if _kind(k) == "probe")
    for i in range(700):
        caches[0][("probe", i)] = entry
    assert held(caches[0]) == 700               # alone at work, its own limit
    for i in range(400):
        caches[1][("probe", i)] = entry
    assert held(caches[0]) + held(caches[1]) <= 1_000 + 11      # the total, one step past
    assert held(caches[1]) == 400                                # the lighter one kept all
    assert 250 <= held(caches[0]) < 700                          # the heavier gave back
    assert sum(c.weight for c in caches) <= 1_000_000 + 10_000 + 1_002
    assert all(c.weight == queued_sum(c) for c in caches)
    # a store made while holding the registry lock never asks for it again
    real, taken = de._CACHE_LOCK, _TakenOnce()
    monkeypatch.setattr(de, "_CACHE_LOCK", taken)
    with taken:
        for i in range(30):
            caches[2][("probe", "held", i)] = entry
    monkeypatch.setattr(de, "_CACHE_LOCK", real)
    assert sum(c.weight for c in caches) <= 1_000_000 + 10_000 + 1_002
    for i in range(2_500):
        caches[3][("probe", "own", i)] = entry
    assert sum(c.weight for c in caches) <= 1_000_000 + 4 * (10_000 + 1_002)
    assert all(c.weight == queued_sum(c) for c in caches)
    # a fifth model pushes the first out of the registry, and of the budget
    caches.append(de._shared_filter_cache(models[4]))
    assert all(c is not caches[0] for c in de._SHARED_FILTER_CACHES.values())
    assert caches[0].check_in == sys.maxsize


def test_a_cache_keeps_looking_at_the_shared_budget(monkeypatch):
    """A registered cache looks at the shared budget each step it stores,
    however its weight moves. Keyed on a weight mark instead (the weight at
    the last look plus a step), a cache trimmed or evicted below its mark
    stopped looking: 8 threads over one model while 30 others stored left
    the registered caches at 49,149 units together against a total of 6,000
    in a stress run of this change's first fix."""
    for name, v in (("_FILTER_CACHE_TOTAL_WEIGHT", 1_000_000), ("_FILTER_CACHE_MIN_WEIGHT", 100_000),
                    ("_FILTER_CACHE_CELLS_FACTOR", 0), ("_FILTER_CACHE_SHARE_STEP", 10_000)):
        monkeypatch.setattr(de, name, v, raising=False)
    monkeypatch.setattr(de, "_SHARED_FILTER_CACHES", {})
    looks = []
    real = getattr(de, "_share_filter_budget", None)

    def counting(grown):
        looks.append(grown)
        return real(grown)
    monkeypatch.setattr(de, "_share_filter_budget", counting, raising=False)
    cache = de._shared_filter_cache(model(5)[1])
    for i in range(1_000):                      # 1M units through a 100k limit
        cache[("probe", i)] = frozenset(range(1_000))
    assert getattr(cache, "evicted", 0) > 0
    assert len(looks) >= 95                     # one per 10,000 units stored


def test_the_limit_follows_the_models_cells():
    """An iteration with a row transition stores a cross filter per row
    keyed by the row's expanded columns, so a wide model needs more room:
    SUMX(F, [A]) by month over 60,000 rows and a 12,000 x 30 dimension
    holds 4.5M units a pass, and at 4M its second pass rebuilt them."""
    def mapping(rows, cols):
        return {"T": {"columns": [f"c{i}" for i in range(cols)], "rows": [None] * rows}}
    small = de._FilterIndexCache(mapping(1_000, 3))
    wide = de._FilterIndexCache(mapping(60_000, 3) | {"D": mapping(12_000, 30)["T"]})
    huge = de._FilterIndexCache(mapping(2_000_000, 20))
    assert small.limit == de._FILTER_CACHE_MIN_WEIGHT
    assert wide.limit == 10 * (60_000 * 3 + 12_000 * 30) > 4_500_000
    assert huge.limit == de._FILTER_CACHE_MAX_WEIGHT
    assert de._FilterIndexCache(None, (5, 10)).limit == de._FILTER_CACHE_MIN_WEIGHT


def test_a_cache_is_served_only_for_its_own_tables():
    """Guard: while a cache is registered nothing else can have its
    mapping's id, so this state is planted (0.9.146 fails it only because
    it trusts that); the registry checks identity anyway."""
    _, a, _ = model(10)
    _, b, _ = model(10)
    ca = de._shared_filter_cache(a)
    fp_b = (id(b), tuple([(k, len(v["rows"]), len(v["columns"])) for k, v in b.items()]))
    de._SHARED_FILTER_CACHES[fp_b] = ca
    try:
        cb = de._shared_filter_cache(b)
        assert cb is not ca and cb["__tables_ref__"] is b and cb.tables is b
    finally:
        de._SHARED_FILTER_CACHES.pop(fp_b, None)


def test_a_transient_mapping_has_a_bounded_cache_of_its_own(monkeypatch):
    small_limit(monkeypatch, 100)
    _, tables, _ = model(5)
    t = de.transient_tables(tables)
    cache = t.filter_cache
    assert type(cache) is de._FilterIndexCache and cache.tables is None
    assert "__tables_ref__" not in cache and de._shared_filter_cache(t) is cache
    for i in range(30):
        cache[("probe", i)] = frozenset(range(10))
    assert ("probe", 0) not in cache and cache.weight <= 100
