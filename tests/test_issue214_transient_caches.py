"""Issue #214 (reported in PR #207, by @allanon2): a tables mapping built for
one evaluation took a slot of the model-wide filter-index cache registry and
pushed the open models' caches out.

_shared_filter_cache keys a registry of four caches on the identity of the
tables mapping. evaluate_per_dimension evaluated each bucket over a fresh
``dict(tables)``, so every bucket registered a cache of its own: from the
fourth bucket on the model's cache was evicted, and the per-value query the
server runs right after the bucketed one rebuilt every index (measured on
0.9.145 at 1M fact rows: 2.93 s instead of 0.026 s). Within one call the
second fact table's probes no longer found the cache the first one filled.
Regenerating a calculated table (server._evaluate_calc_table_partition) did
the same with its throwaway mapping.

Answers never changed -- cache entries are pure functions of their keys --
so the expected values here are the engine's own answers on the per-value
path; what is checked is that the model keeps its cache and the registry
holds no mapping but the model's.
"""
from __future__ import annotations

import gc
import threading
import weakref

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

MANY = de._SHARED_FILTER_CACHE_MAX + 3


def star(n_regions, two_facts=False):
    tables = {
        "Regions": {"columns": ["RegionID", "RegionName"],
                    "rows": [[i, f"R{i:02d}"] for i in range(n_regions)]},
        "Orders": {"columns": ["RegionID", "Revenue"],
                   "rows": [[i % n_regions, float(i % 7)] for i in range(700)]},
    }
    measures = {"Revenue": "SUM(Orders[Revenue])", "Lines": "COUNTROWS(Orders)"}
    rels = [{"FromTable": "Orders", "FromColumn": "RegionID", "ToTable": "Regions",
             "ToColumn": "RegionID", "IsActive": True}]
    if two_facts:
        tables["Returns"] = {"columns": ["RegionID", "Qty"],
                             "rows": [[i % n_regions, i % 5] for i in range(300)]}
        measures["Qty"] = "SUM(Returns[Qty])"
        rels.append({"FromTable": "Returns", "FromColumn": "RegionID", "ToTable": "Regions",
                     "ToColumn": "RegionID", "IsActive": True})
    return tables, measures, rels


def per_dimension(tables, measures, rels, names):
    vals = [r[1] for r in tables["Regions"]["rows"]]
    return de.evaluate_per_dimension(names, tables, measures, {}, "Regions.RegionName",
                                     "Regions", "RegionName", vals, None, None, rels), vals


def test_the_models_filter_cache_outlives_a_per_dimension_query():
    tables, measures, rels = star(MANY)
    de.evaluate_measures_batch(["Revenue"], tables, measures, {"Regions.RegionName": ["R01"]},
                               None, None, rels)
    cache = de._shared_filter_cache(tables)
    filled = dict(cache)
    assert len(filled) > 1
    fast, vals = per_dimension(tables, measures, rels, ["Revenue", "Lines"])
    assert set(fast) == {"Revenue", "Lines"} and len(fast["Revenue"]) == len(vals)
    assert de._shared_filter_cache(tables) is cache
    assert all(cache.get(k) is v for k, v in filled.items())


def test_a_per_dimension_query_registers_no_mapping_but_the_model():
    tables, measures, rels = star(MANY)
    de._shared_filter_cache(tables)
    held = dict(de._SHARED_FILTER_CACHES)
    per_dimension(tables, measures, rels, ["Revenue"])
    assert de._SHARED_FILTER_CACHES.keys() == held.keys()
    assert all(de._SHARED_FILTER_CACHES[k] is v for k, v in held.items())


def test_the_second_fact_table_finds_the_cache_the_first_one_used(monkeypatch):
    """Within ONE call the buckets of Orders evicted the model's cache before
    the base context and probes of Returns ran."""
    tables, measures, rels = star(MANY, two_facts=True)
    handed = []
    inner = de._shared_filter_cache

    def spy(t):
        c = inner(t)
        if t is tables:
            handed.append(c)
        return c

    monkeypatch.setattr(de, "_shared_filter_cache", spy)
    fast, _vals = per_dimension(tables, measures, rels, ["Revenue", "Qty"])
    assert set(fast) == {"Revenue", "Qty"}
    assert len(handed) > 2 and len({id(c) for c in handed}) == 1


def test_bucketed_answers_equal_the_per_value_path_including_an_empty_bucket():
    tables, measures, rels = star(MANY)
    tables["Regions"]["rows"].append([999, "Empty"])      # a member no order reaches
    fast, vals = per_dimension(tables, measures, rels, ["Revenue", "Lines"])
    for v in vals:
        slow = de.evaluate_measures_batch(["Revenue", "Lines"], tables, measures,
                                          {"Regions.RegionName": [v]}, None, None, rels)
        assert fast["Revenue"][v] == slow["Revenue"] and fast["Lines"][v] == slow["Lines"]
    assert fast["Lines"]["Empty"] is None


def test_concurrent_per_dimension_queries_share_one_model_cache():
    tables, measures, rels = star(MANY)
    serial, _ = per_dimension(tables, measures, rels, ["Revenue", "Lines"])
    de._SHARED_FILTER_CACHES.clear()
    cache = de._shared_filter_cache(tables)
    bad, errors = [], []

    def work():
        try:
            for _ in range(3):
                got, _ = per_dimension(tables, measures, rels, ["Revenue", "Lines"])
                if got != serial:
                    bad.append(got)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ths = [threading.Thread(target=work) for _ in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert not errors and not bad
    assert de._shared_filter_cache(tables) is cache
    assert all(c["__tables_ref__"] is tables for c in de._SHARED_FILTER_CACHES.values())


def test_regenerating_calculated_tables_keeps_the_models_cache(monkeypatch):
    """server._evaluate_calc_table_partition reads the tables an expression
    names into a mapping of their own, once per generated calculated table."""
    from pbix_mcp import server
    from pbix_mcp.formats import vertipaq_decoder

    tables, measures, rels = star(3)
    de.evaluate_measures_batch(["Revenue"], tables, measures, {"Regions.RegionName": ["R01"]},
                               None, None, rels)
    cache = de._shared_filter_cache(tables)
    held = dict(de._SHARED_FILTER_CACHES)
    monkeypatch.setattr(vertipaq_decoder, "read_table_from_abf",
                        lambda abf, name, meta: {"columns": ["V"], "rows": [[i] for i in range(10)]})
    for _ in range(de._SHARED_FILTER_CACHE_MAX + 1):
        cols, rows, why = server._evaluate_calc_table_partition("FILTER('Src', 'Src'[V] > 2)", None, None)
        assert cols and len(rows) == 7, why
    assert de._shared_filter_cache(tables) is cache
    assert de._SHARED_FILTER_CACHES.keys() == held.keys()


def test_a_transient_mapping_carries_its_own_cache():
    tables, measures, rels = star(3)
    held = dict(de._SHARED_FILTER_CACHES)
    t = de.transient_tables(tables)
    assert t == tables and t is not tables
    assert de._shared_filter_cache(t) is t.filter_cache
    assert de._shared_filter_cache(de.transient_tables(tables)) is not t.filter_cache
    assert de._SHARED_FILTER_CACHES == held
    # the same cache on any thread: it travels with the mapping
    seen = []
    th = threading.Thread(target=lambda: seen.append(de._shared_filter_cache(t)))
    th.start()
    th.join()
    assert seen == [t.filter_cache] and seen[0] is t.filter_cache
    # every context over it uses it, and the answers are the model's
    r = de.evaluate_measures_batch(["Revenue", "Lines"], t, measures, {"Regions.RegionName": ["R01"]},
                                   None, None, rels)
    assert r == de.evaluate_measures_batch(["Revenue", "Lines"], tables, measures,
                                           {"Regions.RegionName": ["R01"]}, None, None, rels)
    assert len(t.filter_cache) > 0 and "__tables_ref__" not in t.filter_cache
    assert de.transient_tables() == {}


def test_a_transient_mapping_is_freed_with_its_cache_by_reference_counting():
    """The cache holds no reference back to its mapping: no cycle, so the
    two go as soon as the bucket is done, without waiting for the collector."""
    class Probe:
        pass

    tables, measures, rels = star(3)
    t = de.transient_tables(tables)
    de.evaluate_measures_batch(["Revenue"], t, measures, {"Regions.RegionName": ["R01"]},
                               None, None, rels)
    probe = Probe()
    t.filter_cache["probe"] = probe
    gone = weakref.ref(probe)
    del probe
    was = gc.isenabled()
    gc.disable()
    try:
        del t
        assert gone() is None
    finally:
        if was:
            gc.enable()
