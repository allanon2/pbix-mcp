"""The bucketed per-dimension path keeps the model's filter-index cache.

engine.evaluate_per_dimension aggregates each bucket over a copy of the tables
mapping. _shared_filter_cache keys its caches on the mapping's identity and
holds _SHARED_FILTER_CACHE_MAX (4) of them, first in, first out, so every copy
registered a cache of its own: with the model's already held, the fourth
bucket evicted it, and the next query over the model rebuilt its index maps
from nothing.
"""
from pbix_mcp.dax import engine as dax_engine


def _star(n_regions):
    tables = {
        "Regions": {"columns": ["RegionID", "RegionName"],
                    "rows": [[i, f"R{i:02d}"] for i in range(n_regions)]},
        "Orders": {"columns": ["OrderID", "RegionID", "Revenue"],
                   "rows": [[i, i % n_regions, float(i % 7)] for i in range(500)]},
    }
    measures = {"Total Revenue": "SUM(Orders[Revenue])"}
    rels = [{"FromTable": "Orders", "FromColumn": "RegionID",
             "ToTable": "Regions", "ToColumn": "RegionID", "IsActive": True}]
    return tables, measures, rels


def _per_dimension(tables, measures, rels):
    vals = [r[1] for r in tables["Regions"]["rows"]]
    return dax_engine.evaluate_per_dimension(
        ["Total Revenue"], tables, measures, {}, "Regions.RegionName",
        "Regions", "RegionName", vals, None, None, rels), vals


def test_buckets_do_not_evict_the_models_cache():
    tables, measures, rels = _star(dax_engine._SHARED_FILTER_CACHE_MAX + 6)
    dax_engine.evaluate_measures_batch(["Total Revenue"], tables, measures,
                                       {"Regions.RegionName": ["R01"]}, None, None, rels)
    before = dax_engine._shared_filter_cache(tables)
    assert len(before) > 1          # the query above filled it
    fast, vals = _per_dimension(tables, measures, rels)
    assert len(fast["Total Revenue"]) == len(vals)
    assert dax_engine._shared_filter_cache(tables) is before


def test_buckets_register_no_caches():
    tables, measures, rels = _star(dax_engine._SHARED_FILTER_CACHE_MAX + 6)
    dax_engine._shared_filter_cache(tables)
    held = set(dax_engine._SHARED_FILTER_CACHES)
    _per_dimension(tables, measures, rels)
    assert set(dax_engine._SHARED_FILTER_CACHES) == held


def test_answers_unchanged():
    tables, measures, rels = _star(dax_engine._SHARED_FILTER_CACHE_MAX + 6)
    fast, vals = _per_dimension(tables, measures, rels)
    for v in vals:
        slow = dax_engine.evaluate_measures_batch(
            ["Total Revenue"], tables, measures, {"Regions.RegionName": [v]}, None, None, rels)
        assert fast["Total Revenue"][v] == slow["Total Revenue"]
