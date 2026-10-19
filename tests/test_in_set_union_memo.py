"""A filter of several values on a column unites those values' row sets; the
union is memoized per model by the values it unites.

A year-to-date measure over a matrix of countries by month asks for the same
dates' rows for every country of a month. On Microsoft's Corporate Spend sample
(a 166,216 row fact table) one matrix query made 1,656 such unions, 37 of them
distinct, each up to 83,000 rows: three quarters of the query's time. The memo
must not change an answer: the union depends only on the values, and the
filters in force are applied after it.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

DAYS = [datetime(2024, 1, d) for d in range(1, 11)]
TABLES = {
    "dim": {"columns": ["D"], "rows": [[d] for d in DAYS]},
    "fact": {"columns": ["D", "Country", "Amt", "Flag"],
             "rows": [[d, c, i * 10 + j, j % 2 == 0]
                      for i, d in enumerate(DAYS) for j, c in enumerate(["A", "B", "C"])]},
}
RELS = [{"FromTable": "fact", "FromColumn": "D", "ToTable": "dim", "ToColumn": "D", "IsActive": 1}]
MEASURES = {"YTD": "TOTALYTD(SUM(fact[Amt]), dim[D])"}


def fresh_tables():
    # A new tables mapping is a new model: its own filter cache (_shared_filter_cache).
    return {k: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]} for k, v in TABLES.items()}


def expected_ytd(day, country):
    return sum(i * 10 + j for i, d in enumerate(DAYS) for j, c in enumerate(["A", "B", "C"])
               if d <= day and c == country)


def test_a_repeated_union_is_the_same_set_and_the_right_rows():
    ctx = de.DAXContext(fresh_tables(), {}, "dim", "D", None, RELS)
    tbl = ctx.tables["fact"]
    # the values come as join spellings (_jstr): text folded to lower case
    first = ctx._indices_for_values(tbl, 1, ["a", "c"])
    assert first == frozenset(i for i, r in enumerate(tbl["rows"]) if r[1] in ("A", "C"))
    assert len(first) == 20
    assert ctx._indices_for_values(tbl, 1, ["c", "a"]) is first   # the memo answers
    # a single value is the index map's own set, as before
    assert ctx._indices_for_values(tbl, 1, ["b"]) == frozenset(range(1, 30, 3))


def test_values_that_spell_alike_are_not_confused():
    # True and 1 are equal in Python but are different keys of the index map
    ctx = de.DAXContext(fresh_tables(), {}, "dim", "D", None, RELS)
    tbl = ctx.tables["fact"]
    assert ctx._indices_for_values(tbl, 3, [True, False]) == frozenset(range(30))
    assert ctx._indices_for_values(tbl, 3, [1, 0]) == frozenset()


def test_year_to_date_by_country_and_day_matches_with_and_without_the_memo(monkeypatch):
    eng = de.DAXEngine()
    tables = fresh_tables()
    got = {}
    for day in DAYS:
        for country in ["A", "B", "C"]:
            ctx = de.DAXContext(tables, MEASURES, "dim", "D",
                                {"dim.D": [day], "fact.Country": [country]}, RELS)
            got[day, country] = eng.evaluate_measure("YTD", ctx)
    assert got == {(d, c): expected_ytd(d, c) for d in DAYS for c in ["A", "B", "C"]}
    memo = de._shared_filter_cache(tables).get("__in_set_unions__")
    assert memo, "the year-to-date unions went through the memo"


def test_the_memo_starts_again_past_its_bound(monkeypatch):
    monkeypatch.setattr(de.DAXContext, "_UNION_MEMO_MAX_ROWS", 25)
    ctx = de.DAXContext(fresh_tables(), {}, "dim", "D", None, RELS)
    tbl = ctx.tables["fact"]
    memo_of = lambda: ctx._filter_idx_cache["__in_set_unions__"]   # noqa: E731
    ab = ctx._indices_for_values(tbl, 1, ["a", "b"])                # 20 rows
    assert memo_of()["__rows__"] == 20
    bc = ctx._indices_for_values(tbl, 1, ["b", "c"])                # 40 > 25: starts again
    assert memo_of()["__rows__"] == 20 and len(memo_of()) == 2
    assert ctx._indices_for_values(tbl, 1, ["a", "b"]) == ab and ab is not bc
    assert len(ab) == len(bc) == 20
