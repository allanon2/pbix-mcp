"""Issue #230: filter propagation per filter context (reported in PR #206, by
@allanon2).

A running total by date under a year filter on a table two hops from the
fact (Y -> Cal -> F, 4,017 dates, 60,000 fact rows, 912 dates asked) took
127 s on 0.9.146 (10 s now). The memo of the cross filters was keyed by the
WHOLE filter set, which changes on every row of the axis, so for every date
the engine spelled the year filter's unchanged join keys again, walked the
two hops again and turned the keys back into row indices -- and kept each
missed entry for good. The date itself, Cal[Date] <= MAX(Cal[Date]), was a
scan of the calendar by make_value_matcher per date.

Three exact rewrites, behind one switch (engine._FAST_PROPAGATION), so that
comparing it on against off exercises every part, the memo included:

- each source table's cross filter memoized by that source's own filters
  (their typed signatures, #217), the relationships, the DatePartOnly joins
  and the date table with its date column, its target rows resolved once;
  the parts put together once per context (blank_row_visible asks again on
  one row context for every row of an iterator), and whether a source
  reaches a table at all known per relationship set;
- a direct one -> many hop as the union of per-row buckets of target rows,
  filled only for the rows a filter keeps, a date's bucket the value map's
  own set rather than a copy of its two spellings' union;
- an inequality on a number or date column bisected in the column's sorted
  values, its BLANK, NaN and other cells answered as _compare answers them,
  behind a bounded memo of results looked up first.

Expected values: the engine's own key path -- the switch off, each filter
context on a FRESH model so that no memo carries between contexts -- in the
differential fuzz (one shared model, contexts shuffled, two passes, with the
switch on); plain Python sums for the running totals; make_value_matcher's
scan for the inequality index; _hop_keys plus _indices_for_values (the
0.9.146 path) for the buckets. No Desktop probe is needed: nothing here may
change an answer. Planted bugs (a memo key without the date table, without
the filters or without the relationships; a grouping that ignores ALL()'s
snapshot; a bucket hop without the blank row or without key ownership; <=
read as <; the BLANK class dropped) must each make the fuzz fail. The fuzz
runs 20 seeds here; PBIX_PROPAGATION_FUZZ_SEEDS asks for more (0 differences
in 300 seeds when this was written).
"""
from __future__ import annotations

import decimal
import functools
import itertools
import math
import os
import random
from datetime import date, datetime, timedelta

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

DC = de.DAXContext


# ---- a running total by date, a year filter two hops away -------------------
def _running_model(n_dates=400, n_fact=4000, seed=1):
    r = random.Random(seed)
    start = datetime(2023, 3, 1)
    days = [start + timedelta(days=i) for i in range(n_dates)]
    tables = {
        "Cal": {"columns": ["Date", "Year"], "rows": [[d, d.year] for d in days]},
        "Y": {"columns": ["Date", "Year"], "rows": [[d, d.year] for d in days]},
        "F": {"columns": ["When", "Amt"],
              "rows": [[r.choice(days), round(r.uniform(-50, 200), 2)] for _ in range(n_fact)]},
    }
    rels = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date",
             "IsActive": 1, "FromCardinality": 2, "ToCardinality": 1},
            {"FromTable": "Cal", "FromColumn": "Date", "ToTable": "Y", "ToColumn": "Date",
             "IsActive": 1, "FromCardinality": 2, "ToCardinality": 1}]
    measures = {"Total": "SUM(F[Amt])",
                "Running": "CALCULATE([Total], Cal[Date] <= MAX(Cal[Date]))"}
    return tables, rels, measures, days


def _python_running(tables, days, year):
    sums: dict = {}
    for w, a in tables["F"]["rows"]:
        if w.year == year:
            sums[w] = sums.get(w, 0.0) + a
    out, acc = [], 0.0
    for d in days:
        if d.year == year:
            acc += sums.get(d, 0.0)
            out.append(acc)
    return out


def _run_dates(tables, rels, measures, days, year, limit=None):
    base = {"Y.Year": [year]}
    cache: dict = {}
    out = []
    for d in [d for d in days if d.year == year][:limit]:
        fc = dict(base, **{"Cal.Date": [d]})
        out.append(de.evaluate_measures_batch(["Running"], tables, measures, fc, "Cal", "Date", rels,
                                              group_keys={"Cal.Date"}, selected_filters=base,
                                              cache=cache)["Running"])
    return out


def _counting(monkeypatch, name):
    calls = {"n": 0}
    orig = DC.__dict__.get(name)
    if orig is None:                # not in this version: never called
        return calls

    def wrapper(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)
    monkeypatch.setattr(DC, name, wrapper)
    return calls


def test_a_running_total_walks_the_unchanged_year_filter_once(monkeypatch):
    # the propagation alone: #231's running-total route claims its owners hop
    # by hop over the dimension once its scans have paid for it
    monkeypatch.setattr(de, "_RANGE_BUILD_FACTOR", float("inf"), raising=False)
    tables, rels, measures, days = _running_model()
    paths = _counting(monkeypatch, "_propagate_filter_path")
    hops = _counting(monkeypatch, "_hop_keys")
    got = _run_dates(tables, rels, measures, days, 2023)
    want = _python_running(tables, days, 2023)
    assert len(got) == len(want) == 306
    assert all(abs(g - w) < 1e-6 for g, w in zip(got, want))
    # Y -> Cal -> F once (0.9.146: once per date, 306); the date's own hop
    # Cal -> F and Y -> Cal (for MAX(Cal[Date])) from their buckets
    assert paths["n"] <= 2, paths
    assert hops["n"] <= 4, hops


def test_each_date_leaves_one_entry_in_a_bounded_memo(monkeypatch):
    monkeypatch.setattr(DC, "_XTF_ROWS_MEMO_MAX", 5000)
    tables, rels, measures, days = _running_model(n_dates=300, n_fact=3000, seed=2)
    want = _python_running(tables, days, 2023)
    got = _run_dates(tables, rels, measures, days, 2023)
    assert all(abs(g - w) < 1e-6 for g, w in zip(got, want))
    cache = de._shared_filter_cache(tables)
    memo = cache[("xtf-rows",)]
    biggest = max(sum(len(s) for s in v) for v in memo.values())
    assert memo.weight <= 5000 + biggest + 10
    # the memo of the whole filter set is not kept per date any more
    assert not [k for k in cache if isinstance(k, tuple) and len(k) > 1 and k[1] == "xtf"]


def test_the_switch_off_is_the_key_path(monkeypatch):
    monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    tables, rels, measures, days = _running_model(n_dates=120, n_fact=1500, seed=3)
    got = _run_dates(tables, rels, measures, days, 2023)
    assert all(abs(g - w) < 1e-6 for g, w in zip(got, _python_running(tables, days, 2023)))
    cache = de._shared_filter_cache(tables)
    assert ("xtf-rows",) not in cache and ("ineq",) not in cache
    assert not [k for k in cache if isinstance(k, tuple) and len(k) > 1 and k[1] == "hop-buckets"]


# ---- asking again -----------------------------------------------------------
def _snowflake(n_orders=3000, seed=1):
    r = random.Random(seed)
    tables = {
        "Region": {"columns": ["RK", "Zone"], "rows": [[i, "NS"[i % 2]] for i in range(10)]},
        "Country": {"columns": ["CK", "RK"], "rows": [[i, i % 10] for i in range(50)]},
        "Customer": {"columns": ["UK", "CK", "Seg"], "rows": [[i, i % 50, "abc"[i % 3]] for i in range(400)]},
        "Product": {"columns": ["PK", "Cat"], "rows": [[i, "xyz"[i % 3]] for i in range(60)]},
        "Cal": {"columns": ["Date", "Year"], "rows": [[i, 2020 + i // 365] for i in range(1500)]},
        # keys past the customers: Customer has a blank row
        "Orders": {"columns": ["UK", "PK", "D", "Amt"],
                   "rows": [[r.randrange(420), r.randrange(60), r.randrange(1500), r.random()]
                            for _ in range(n_orders)]},
    }

    def rel(ft, fc, tt, tc):
        return {"FromTable": ft, "FromColumn": fc, "ToTable": tt, "ToColumn": tc, "IsActive": 1,
                "FromCardinality": 2, "ToCardinality": 1}
    rels = [rel("Country", "RK", "Region", "RK"), rel("Customer", "CK", "Country", "CK"),
            rel("Orders", "UK", "Customer", "UK"), rel("Orders", "PK", "Product", "PK"),
            rel("Orders", "D", "Cal", "Date")]
    return tables, rels


SNOWFLAKE_FILTERS = {"Region.Zone": ["N"], "Product.Cat": ["x"], "Cal.Year": [2021],
                     "Customer.Seg": ["a", "b"]}


def test_a_context_asking_again_puts_its_cross_filters_together_once(monkeypatch):
    # the memo of the whole filter set answered a context asking again in
    # 1.4 us; a memo key per source and a search of the relationship graph for
    # the sources that are no neighbours took 10 us
    tables, rels = _snowflake()
    ctx = DC(tables, {}, "Cal", "Date", dict(SNOWFLAKE_FILTERS), rels)
    keys = _counting(monkeypatch, "_source_memo_key")
    paths = _counting(monkeypatch, "_find_rel_path")
    first = ctx._get_cross_table_filters("Orders")
    assert len(first) == 4 and keys["n"] == 4, (first, keys)
    asked = (keys["n"], paths["n"])
    for _ in range(50):
        assert ctx._get_cross_table_filters("Orders") == first
        ctx.get_filtered_rows("Orders")
    ctx.get_filtered_rows("Orders")
    asked_rows = keys["n"]
    for _ in range(50):
        ctx.get_filtered_rows("Orders")
    assert keys["n"] == asked_rows == asked[0] + 4
    # fresh contexts over the same model: whether Region reaches Orders (three
    # hops) is known, and their parts come from the per-source memo
    for _ in range(20):
        assert ctx.with_filters({})._get_cross_table_filters("Orders") == first
    assert paths["n"] == asked[1]
    assert ctx._filter_idx_cache[("reaches", ctx._rels_sig, "Region", "Orders")] is True


def test_a_row_iterator_asks_the_blank_row_once_per_context(monkeypatch):
    # HASONEVALUE inside SUMX asks blank_row_visible on the row context's root
    # for every row: 11% slower than 0.9.146 while each ask made its memo keys
    tables, rels = _snowflake()
    # every segment stays visible: no row has one value, each counts 1. No
    # filter on Customer itself, which would hide its blank row before the
    # cross filters are asked
    measures = {"M": "SUMX(Orders, IF(HASONEVALUE(Customer[Seg]), 0, 1))"}
    fc = {"Region.Zone": ["N"], "Product.Cat": ["x"], "Cal.Year": [2020, 2021],
          "Country.CK": list(range(40))}
    want = DC(tables, {}, "Cal", "Date", fc, rels).get_filtered_rows("Orders")
    asks = _counting(monkeypatch, "blank_row_visible")
    keys = _counting(monkeypatch, "_source_memo_key")
    got = de.evaluate_measures_batch(["M"], tables, measures, fc, "Cal", "Date", rels)["M"]
    assert got == len(want) > 150
    assert asks["n"] >= len(want)
    assert 0 < keys["n"] <= 20, keys


# ---- buckets ----------------------------------------------------------------
def test_a_one_shot_query_fills_only_the_buckets_it_selects():
    r = random.Random(4)
    n = 5000
    dim = {"columns": ["Key", "Name", "Seg"], "rows": [[100000 + i, f"name{i}", r.choice("abcde")] for i in range(n)]}
    fact = {"columns": ["K", "Amt"], "rows": [[100000 + r.randrange(n), r.randint(1, 9)] for _ in range(20000)]}
    tables = {"D": dim, "F": fact}
    rels = [{"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    measures = {"Total": "SUM(F[Amt])"}
    got = de.evaluate_measures_batch(["Total"], tables, measures, {"D.Name": ["name17"]}, None, None, rels)
    assert got["Total"] == sum(a for k, a in fact["rows"] if k == 100017)
    slots = de._shared_filter_cache(tables)[(id(dim), "hop-buckets", 0, id(fact), 0)]
    assert len(slots) == n and sum(s is not None for s in slots) == 1
    got = de.evaluate_measures_batch(["Total"], tables, measures, {"D.Seg": ["a"]}, None, None, rels)
    keys = {row[0] for row in dim["rows"] if row[2] == "a"}
    assert got["Total"] == sum(a for k, a in fact["rows"] if k in keys)
    assert sum(s is not None for s in slots) == len(keys | {100017})


def _hop_case(dim_rows, fact_rows, **rel):
    tables = {"D": {"columns": ["Key", "Name"], "rows": dim_rows},
              "F": {"columns": ["K", "Amt"], "rows": fact_rows}}
    r = {"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
         "FromCardinality": 2, "ToCardinality": 1}
    r.update(rel)
    return tables, [r]


HOP_CASES = {
    # a key the one side holds twice joins its last row (#162)
    "duplicate-keys": _hop_case([[1, "a"], [2, "b"], [2, "c"], [3, "d"]],
                                [[1, 1], [2, 10], [2, 100], [3, 1000], [9, 5]]),
    # many-to-many: every row holding the key
    "many-to-many": _hop_case([[1, "a"], [2, "b"], [2, "c"]], [[1, 1], [2, 10], [None, 7]],
                              FromCardinality=2, ToCardinality=2),
    # a BLANK one-side key, BLANK and unmatched fact keys: the blank row (#82)
    "blank-keys": _hop_case([[1, "a"], [None, "b"], [3, "c"]],
                            [[1, 1], [None, 10], [3, 100], [99, 1000], [None, 5]]),
    # text keys match with their ASCII case folded (#163)
    "text-case": _hop_case([["K1", "a"], ["k2", "b"], ["É", "c"]],
                           [["k1", 1], ["K2", 10], ["é", 100], ["K1", 1000]]),
    # a key reaching a text column has no numeric spellings (#222)
    "text-numeric": _hop_case([["01", "a"], ["1", "b"], ["7", "c"]],
                              [["01", 1], ["1", 10], ["1.0", 100], ["7", 1000], [" 1", 5]]),
    # one key in several types: a date and its serial, int and float (#39)
    "aliases": _hop_case([[datetime(2024, 1, 1), "a"], [2, "b"], [3.0, "c"], [datetime(2024, 1, 2, 12), "d"]],
                         [[45292, 1], [45292.0, 10], [2.0, 100], [3, 1000], ["2024-01-01", 5],
                          [45293.5, 50], [datetime(2024, 1, 2, 12), 500]]),
    # a NaN key on the one side is spelled as itself (#219)
    "nan-key": _hop_case([[1.0, "a"], [float("nan"), "b"], [2.0, "c"]],
                         [[1.0, 1], [2, 10], [3.0, 100], [None, 1000]]),
    # a date column's value map holds a midnight date under its str() and its
    # ISO date, the second set a superset when the column also spells dates
    # as text: the bucket is their union
    "date-spellings": _hop_case([[datetime(2024, 1, 1), "a"], [datetime(2024, 1, 2), "b"],
                                 [date(2024, 1, 3), "c"], [datetime(2024, 1, 4, 6), "d"]],
                                [[datetime(2024, 1, 1), 1], ["2024-01-01", 10], [date(2024, 1, 1), 100],
                                 [datetime(2024, 1, 2), 1000], [datetime(2024, 1, 2, 9), 5],
                                 ["2024-01-03", 50], [datetime(2024, 1, 3), 500],
                                 [datetime(2024, 1, 4, 6), 7], ["2024-01-04", 70]]),
}


@pytest.mark.parametrize("case", sorted(HOP_CASES))
def test_the_buckets_select_what_the_key_path_selects(case):
    tables, rels = HOP_CASES[case]
    ctx = DC(tables, {}, None, None, {}, rels)
    dim, fact = tables["D"], tables["F"]
    names = [row[1] for row in dim["rows"]]
    choices = [[n] for n in names] + [names, [], names[:2], names[1:]]
    # a filter that keeps the blank row too (BLANK is not "zz")
    choices_blank = [{"not_in": ["zz"]}, {"not_in": [names[0]]}]
    for values in choices + choices_blank:
        col_filters = [("Name", values)]
        got = ctx._direct_hop_rows("D", dim, col_filters, 0, "F", fact, 0)
        rows, applied = ctx._rows_where(dim, col_filters)
        blank = "D" in ctx.blank_row_tables() and ctx._filters_admit_blank(applied, dim)
        want = ctx._indices_for_values(fact, 0, ctx._hop_keys("D", rows, blank, 0, "F", 0))
        assert got == want, (case, values)


def test_a_date_bucket_is_the_value_maps_own_set():
    # a midnight date's two spellings in the fact column's value map hold the
    # same rows: the bucket is one of those sets, never a copy of their union
    # (a one-shot Cal[Year] filter over 1M fact rows was 9-20% slower than the
    # key path, and kept twice its memory)
    r = random.Random(6)
    days = [datetime(2020, 1, 1) + timedelta(days=i) for i in range(90)]
    cal = {"columns": ["Date", "Year", "Month"], "rows": [[d, d.year, d.month] for d in days]}
    fact = {"columns": ["When", "Amt"], "rows": [[r.choice(days), r.randint(1, 9)] for _ in range(3000)]}
    tables = {"Cal": cal, "F": fact}
    rels = [{"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    got = de.evaluate_measures_batch(["Total"], tables, {"Total": "SUM(F[Amt])"}, {"Cal.Month": [2]},
                                     "Cal", "Date", rels)["Total"]
    assert got == sum(a for w, a in fact["rows"] if w.month == 2)
    cache = de._shared_filter_cache(tables)
    slots = cache[(id(cal), "hop-buckets", 0, id(fact), 0)]
    own = {id(s) for s in cache[(id(fact), "vmap", 0)].values()}
    filled = [s for s in slots if s is not None]
    assert len(filled) == 29
    assert all(id(s) in own for s in filled)


def test_date_part_and_one_to_one_joins_keep_the_key_path():
    tables, rels = _hop_case([[date(2024, 1, 1), "a"]], [[datetime(2024, 1, 1, 9), 1]], JoinOnDateBehavior=2)
    ctx = DC(tables, {}, None, None, {}, rels)
    assert ctx._direct_hop_rows("D", tables["D"], [("Name", ["a"])], 0, "F", tables["F"], 0) is None
    tables, rels = _hop_case([[1, "a"], [3, "b"]], [[1, 10], [2, 20]], FromCardinality=1, ToCardinality=1)
    ctx = DC(tables, {}, None, None, {}, rels)
    assert ctx._direct_hop_rows("D", tables["D"], [("Name", ["a"])], 0, "F", tables["F"], 0) is None
    assert de.evaluate_measure("S", tables, {"S": "SUM(F[Amt])"}, {"D.Name": ["a"]},
                               relationships=rels) == 10


def test_a_key_that_cannot_be_spelled_turns_the_hop_over_to_the_key_path(monkeypatch):
    tables, rels = _hop_case([[1, "a"], ["boom", "b"], [3, "c"]], [[1, 1], [3, 100], ["boom", 7]])
    real = de._join_key_aliases
    armed = {"on": False}

    def aliases(v):
        if v == "boom" and armed["on"]:
            raise ValueError("unspellable key")
        return real(v)
    monkeypatch.setattr(de, "_join_key_aliases", aliases)
    ctx = DC(tables, {}, None, None, {}, rels)
    dim, fact = tables["D"], tables["F"]
    ctx.blank_row_tables()   # spells every key once, before the key goes bad
    armed["on"] = True
    assert ctx._direct_hop_rows("D", dim, [("Name", ["a"])], 0, "F", fact, 0) == frozenset({0})
    assert ctx._direct_hop_rows("D", dim, [("Name", ["b"])], 0, "F", fact, 0) is None
    assert ctx._filter_idx_cache[(id(dim), "hop-buckets", 0, id(fact), 0)] is de._UNINDEXED
    armed["on"] = False      # the key path from now on, even once the key spells
    assert ctx._direct_hop_rows("D", dim, [("Name", ["a"])], 0, "F", fact, 0) is None
    assert de.evaluate_measure("S", tables, {"S": "SUM(F[Amt])"}, {"D.Name": ["b", "c"]},
                               relationships=rels) == 107


# ---- the inequality index ---------------------------------------------------
OPS = ["<", "<=", "=<", ">", ">=", "=>"]
CELLS = [None, 0, 1, -1, 2.5, 10, 45301, 45301.5, float("nan"), float("inf"), float("-inf"), -0.0,
         True, False, "12", "abc", "2024-01-10", "", " 5 ", decimal.Decimal("10.5"), 2 ** 60, 2 ** 60 + 1,
         date(2024, 1, 10), datetime(2024, 1, 10), datetime(2024, 1, 10, 12, 30), date(1899, 12, 30),
         datetime(1899, 12, 29, 12)]
TARGETS = [None, 0, 5, -5, 45301, 45301.0, 45301.5, "45301", "2024-01-10", "2024-01-10 12:00:00",
           "10/01/2024", "abc", "-5", "0", "nan", float("nan"), float("inf"), True, decimal.Decimal("5"),
           date(2024, 1, 10), datetime(2024, 1, 10, 6), 2 ** 60, " 5 ", "1e3", "inf", -0.0]


def _scan(cells, spec):
    m = de.make_value_matcher(spec)
    return frozenset(i for i, c in enumerate(cells) if m(c))


def test_the_inequality_index_answers_as_the_matcher_does():
    numeric = [c for c in CELLS if type(c) is not bool and isinstance(c, (int, float, date))]
    columns = [[c] for c in CELLS] + [list(CELLS), numeric, numeric + [None, float("nan")]]
    answered = fell_back = 0
    for col in columns:
        tables = {"T": {"columns": ["V"], "rows": [[c] for c in col]}}
        ctx = DC(tables, {}, None, None, {}, [])
        tbl = tables["T"]
        specs = [{"op": op, "value": t} for op, t in itertools.product(OPS, TARGETS)]
        specs += [{"between": [lo, hi]} for lo, hi in [(0, 10), (None, 5), (-5, None), (date(2024, 1, 1), 45302)]]
        specs += [{"all": [{"op": ">", "value": -1}, {"op": "<=", "value": t}]} for t in (None, 2.5, 45301)]
        for spec in specs:
            got = ctx._inequality_rows(tbl, 0, spec)
            if got is None:
                fell_back += 1
                continue
            answered += 1
            assert got == _scan(col, spec), (col if len(col) == 1 else len(col), spec)
    # every target with a number is answered from the index
    assert answered > 3 * fell_back, (answered, fell_back)


def test_other_predicates_are_left_to_the_matcher():
    tables = {"T": {"columns": ["V"], "rows": [[1], [2], [None]]}}
    ctx = DC(tables, {}, None, None, {}, [])
    for spec in ({"op": "=", "value": 1}, {"op": "<>", "value": 1}, {"in": [1]}, {"value": 1},
                 {"op": ">", "value": 1, "contains": "1"}, {"between": [1]}, {"all": []},
                 {"all": [{"op": ">", "value": 0}, [1]]}, {"is_blank": True}, {}, [1, 2]):
        assert ctx._inequality_rows(tables["T"], 0, spec) is None, spec


def test_a_mostly_text_column_is_scanned():
    rows = [[f"t{i}"] for i in range(100)] + [[i] for i in range(100)]
    tables = {"T": {"columns": ["V"], "rows": rows}}
    ctx = DC(tables, {}, None, None, {}, [])
    assert ctx._inequality_rows(tables["T"], 0, {"op": ">", "value": 5}) is None
    assert de.evaluate_measure("N", tables, {"N": "COUNTROWS(T)"}, {"T.V": {"op": ">", "value": 5}}) == \
        len(_scan([r[0] for r in rows], {"op": ">", "value": 5}))


def test_a_repeated_inequality_is_answered_from_the_memo(monkeypatch):
    r = random.Random(5)
    prod = {"columns": ["Prod"], "rows": [[i] for i in range(1, 31)]}
    fact = {"columns": ["Prod", "Amt"],
            "rows": [[r.randint(1, 30), round(r.uniform(-50, 200), 2)] for _ in range(3000)]}
    tables = {"P": prod, "F": fact}
    rels = [{"FromTable": "F", "FromColumn": "Prod", "ToTable": "P", "ToColumn": "Prod", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    measures = {"Big": "CALCULATE(SUM(F[Amt]), F[Amt] > 50)"}
    compares = {"n": 0}
    real_compare = de._compare

    def counting_compare(*a):
        compares["n"] += 1
        return real_compare(*a)
    monkeypatch.setattr(de, "_compare", counting_compare)
    bisects = _counting(monkeypatch, "_inequality_order")
    cache: dict = {}
    for p in range(1, 31):
        got = de.evaluate_measures_batch(["Big"], tables, measures, {"P.Prod": [p]}, None, None, rels,
                                         group_keys={"P.Prod"}, cache=cache)["Big"]
        want = sum(a for q, a in fact["rows"] if q == p and a > 50)
        assert abs(got - want) < 1e-6
    # the column holds numbers only: no cell is compared on its own (0.9.146
    # scanned all 3,000), and the sorted values are read once, the memo after
    assert compares["n"] == 0
    assert bisects["n"] == 1
    fcache = de._shared_filter_cache(tables)
    assert len(fcache[("ineq",)]) == 1
    # the rows are never kept in the predicate scans' memo, which no bound holds
    assert not [k for k in fcache if isinstance(k, tuple) and len(k) > 2 and k[0] == id(fact) and k[1] == "d"]


def test_the_inequality_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(DC, "_INEQ_MEMO_MAX", 300)
    tables = {"T": {"columns": ["V"], "rows": [[i] for i in range(200)]}}
    ctx = DC(tables, {}, None, None, {}, [])
    for bound in range(0, 200, 7):
        assert ctx._inequality_rows(tables["T"], 0, {"op": "<", "value": bound}) == frozenset(range(bound))
        assert ctx._filter_idx_cache[("ineq",)].weight <= 300 + 200


# ---- differential fuzz: the switch on against off -------------------------------
FUZZ_MEASURES = {
    "Total": "SUM(F[Amt])",
    "Rows": "COUNTROWS(F)",
    "DRows": "COUNTROWS(D)",
    "GRows": "COUNTROWS(G)",
    "Running": "CALCULATE([Total], Cal[Date] <= MAX(Cal[Date]))",
    "NotA": 'CALCULATE([Total], D[Cat] <> "a")',
    "Big": "CALCULATE([Total], F[Amt] > 50)",
    "Between": "CALCULATE([Rows], F[Amt] >= -20, F[Amt] <= 60)",
    "AllF": "CALCULATE([Total], ALL(F))",
    "AllD": "CALCULATE([Total], ALL(D))",
    # ALL() of a table the date table reaches with no relationship: the
    # date filters go into its snapshot (_no_prop_keys)
    "AllG": "CALCULATE([GRows], ALL(G))",
    "AllGNested": "CALCULATE(CALCULATE([GRows], Cal[Year] = 2024), ALL(G))",
    "Keep": "CALCULATE([Total], KEEPFILTERS(D[Num] >= 2))",
    "Sel": "CALCULATE([Total], ALLSELECTED(Cal[Date]))",
    "UseRel": "CALCULATE([Total], USERELATIONSHIP(F[Ship], Cal[Date]))",
    "XF": "CALCULATE([DRows], CROSSFILTER(F[DKey], D[Key], BOTH))",
    "Treat": 'CALCULATE([Total], TREATAS({("a", 2)}, D[Cat], D[Num]))',
    "Iter": "SUMX(VALUES(D[Cat]), [Total])",
    "IterF": "SUMX(FILTER(D, D[Num] > 1.5), [Rows])",
    # a fact row's transition: its dimensions' filters back on F are skipped
    "IterFD": "SUMX(F, CALCULATE(COUNTROWS(D)))",
    "IterFF": "SUMX(F, CALCULATE(SUM(F[Amt])))",
    "KeysVis": "COUNTROWS(VALUES(D[Key]))",
    "Window": "CALCULATE([Rows], Cal[Date] >= MIN(Cal[Date]) - 3, Cal[Date] <= MAX(Cal[Date]))",
    "Year24": "CALCULATE([Total], Y[Year] = 2024)",
    "NumLT": "CALCULATE([Total], D[Num] < 2)",
    "NumGTBlank": "CALCULATE([Rows], D[Num] > BLANK())",
    "Least": "MIN(F[Amt])",
    "LastDate": "MAX(Cal[Date])",
    "IterCal": "SUMX(VALUES(Cal[Date]), [Rows])",
    "TKv": "SUM(TK[v])",
    "TKRows": "COUNTROWS(DK)",
    "TKIter": "SUMX(VALUES(DK[code]), [TKv])",
}

# (date table, date column) a context names: None auto-detects (Cal here)
DATE_TABLES = [("Cal", "Date"), ("Y", "Date"), (None, None), ("Cal", "Shift")]


def _fuzz_model(seed):
    r = random.Random(seed)
    days = [datetime(2023, 12, 20) + timedelta(days=i) for i in range(r.randint(15, 45))]
    keys: list = list(range(1, r.randint(4, 9)))
    if r.random() < 0.3:
        keys.append(keys[0])                       # a key the one side holds twice (#162)
    if r.random() < 0.2:
        keys.append(None)                          # a BLANK key on the one side
    if r.random() < 0.15:
        keys.append(float("nan"))                  # a NaN key (#219)
    if r.random() < 0.15:
        keys.append(2.0)                           # 2 and 2.0: one key
    numpool: list = [1.5, 2, 3]
    if r.random() < 0.4:
        numpool += [None, "x", float("nan"), True, "2", 2.0]
    d_rows = [[k, r.choice("abc"), r.choice(numpool), r.choice(["1", "1.0", "None", None, "x"])] for k in keys]
    when = list(days)
    if r.random() < 0.4:
        when.append(datetime(2024, 1, 3, 12, 30))  # a time of day
    extra = [99, None] if r.random() < 0.5 else []
    f_rows = []
    for _ in range(r.randint(30, 120)):
        amt = r.choice([round(r.uniform(-100, 100), 2), r.randint(0, 80)])
        f_rows.append([r.choice([k for k in keys if k is not None] + extra),
                       r.choice(when) if r.random() > 0.04 else None, r.choice(days),
                       None if r.random() < 0.06 else amt])
    if r.random() < 0.15:
        f_rows.append([keys[0], days[0], days[1], math.nan])
    codes = ["01", "1", "7", "A"] + (["a"] if r.random() < 0.5 else [])
    tables = {
        "D": {"columns": ["Key", "Cat", "Num", "Code"], "rows": d_rows},
        "Cal": {"columns": ["Date", "Year", "Shift"],
                "rows": [[d, d.year if d.day != 5 else None, d + timedelta(days=2)] for d in days]},
        "Y": {"columns": ["Date", "Year", "Week"], "rows": [[d, d.year, d.isocalendar()[1]] for d in days]},
        "F": {"columns": ["DKey", "When", "Ship", "Amt"], "rows": f_rows},
        "G": {"columns": ["Date", "N"], "rows": [[r.choice(days), i] for i in range(r.randint(5, 20))]},
        "DK": {"columns": ["code", "x"], "rows": [[c, i + 1] for i, c in enumerate(codes)]},
        "TK": {"columns": ["code", "v"],
               "rows": [[r.choice(codes + ["9", None, "1.0"]), r.choice([1, 5, 10, 100])]
                        for _ in range(r.randint(8, 25))]},
    }
    r1 = {"FromTable": "F", "FromColumn": "DKey", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
          "FromCardinality": 2, "ToCardinality": 1}
    kind = r.random()
    if kind < 0.2:
        r1["CrossFilteringBehavior"] = 2           # both directions
    elif kind < 0.3:
        r1["FromCardinality"] = r1["ToCardinality"] = 2   # many-to-many
    r2 = {"FromTable": "F", "FromColumn": "When", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1,
          "FromCardinality": 2, "ToCardinality": 1}
    if r.random() < 0.25:
        r2["JoinOnDateBehavior"] = 2               # DatePartOnly (#126)
    r3 = {"FromTable": "F", "FromColumn": "Ship", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 0,
          "FromCardinality": 2, "ToCardinality": 1}
    r4 = {"FromTable": "Cal", "FromColumn": "Date", "ToTable": "Y", "ToColumn": "Date", "IsActive": 1,
          "FromCardinality": 2, "ToCardinality": 1}
    if r.random() < 0.15:
        r4["CrossFilteringBehavior"] = 2
    r5 = {"FromTable": "TK", "FromColumn": "code", "ToTable": "DK", "ToColumn": "code", "IsActive": 1,
          "FromCardinality": 2, "ToCardinality": 1}
    return tables, [r1, r2, r3, r4, r5], days


def _fuzz_contexts(seed, days, n=10):
    r = random.Random(seed * 7919 + 1)
    out = []
    for _ in range(n):
        fc: dict = {}
        group = False
        x = r.random()
        if x < 0.4:
            fc["Cal.Date"] = [r.choice(days)]
            group = r.random() < 0.6
        elif x < 0.7:
            fc["Cal.Date"] = {"op": r.choice(OPS),
                              "value": r.choice([r.choice(days), None, 45300.0, "2024-01-10", "abc",
                                                 r.choice(days).date(), float("nan")])}
        elif x < 0.8:
            fc["Cal.Date"] = {"between": [days[2], days[-3]]}
        if r.random() < 0.35:
            fc["D.Cat"] = r.sample("abc", r.randint(1, 2))
        if r.random() < 0.3:
            fc["D.Num"] = {"op": r.choice(["<", ">=", ">", "<="]), "value": r.choice([2, None, "2", True, 1.5])}
        if r.random() < 0.25:
            fc["F.Amt"] = {"all": [{"op": ">", "value": -20}, {"op": "<=", "value": r.choice([10, 60, None])}]}
        if r.random() < 0.25:
            fc["Y.Week"] = {"not_in": [r.choice([1, 2, 3])]}
        if r.random() < 0.25:
            fc["Y.Year"] = [2024] if r.random() < 0.7 else [2023]
        if r.random() < 0.25:
            fc["D.Key"] = [r.choice([1, 2, 99, None, "None", 1.0, "1", "2.0", float("nan")])]
        if r.random() < 0.3:
            fc["D.Code"] = [r.choice([1.0, "1.0", None, "None", 1, "1"])]
        if r.random() < 0.15:
            fc["Cal.Year"] = [None] if r.random() < 0.5 else ["None"]
        if r.random() < 0.3:
            fc["DK.code"] = [r.choice(["01", "1", 1, "a", "A", None, "7"])]
        if r.random() < 0.2:
            fc["DK.x"] = {"op": r.choice(["<", ">="]), "value": r.choice([2, 3, None])}
        dt = r.choice(DATE_TABLES)
        out.append((fc, group, dt))
        if r.random() < 0.35:                      # the same filters under another date table
            out.append((fc, group, r.choice([d for d in DATE_TABLES if d != dt])))
    return out


def _copy(tables):
    return {k: {"columns": list(v["columns"]), "rows": [list(x) for x in v["rows"]]} for k, v in tables.items()}


def _evaluate(tables, rels, fc, group, dt):
    kw = {}
    if group:
        base = {k: v for k, v in fc.items() if k != "Cal.Date"}
        kw = {"group_keys": {"Cal.Date"}, "selected_filters": base}
    out = {}
    for name in FUZZ_MEASURES:
        try:
            out[name] = de.evaluate_measures_batch([name], tables, FUZZ_MEASURES, dict(fc), dt[0], dt[1],
                                                   rels, **kw)[name]
        except Exception as e:      # compared by type
            out[name] = ("error", type(e).__name__)
    return out


def _same(a, b):
    if isinstance(a, float) and isinstance(b, float) and (math.isnan(a) or math.isnan(b)):
        return math.isnan(a) and math.isnan(b)
    return type(a) is type(b) and a == b


@functools.lru_cache(maxsize=None)
def _reference(seed):
    """Each context with the switch off on a fresh model: no memo carries."""
    base, rels, days = _fuzz_model(seed)
    fcs = _fuzz_contexts(seed, days)
    saved = de._FAST_PROPAGATION
    de._FAST_PROPAGATION = False
    try:
        return base, rels, fcs, [_evaluate(_copy(base), rels, fc, g, dt) for fc, g, dt in fcs]
    finally:
        de._FAST_PROPAGATION = saved


def _differences(seed):
    """The switch on over ONE model, the contexts shuffled, twice over."""
    base, rels, fcs, ref = _reference(seed)
    assert de._FAST_PROPAGATION
    shared = _copy(base)
    order = list(range(len(fcs)))
    random.Random(seed).shuffle(order)
    diffs = []
    for _pass in range(2):
        for i in order:
            fc, g, dt = fcs[i]
            got = _evaluate(shared, rels, fc, g, dt)
            bad = [(m, ref[i][m], got[m]) for m in FUZZ_MEASURES if not _same(ref[i][m], got[m])]
            if bad:
                diffs.append((seed, fc, dt, bad))
    return diffs


FUZZ_SEEDS = int(os.environ.get("PBIX_PROPAGATION_FUZZ_SEEDS", "20"))


def test_propagation_per_context_answers_as_the_key_path(monkeypatch):
    seen = {"suppressed": 0, "expansion": 0, "buckets": 0, "inequality": 0, "memo hits": 0, "memo misses": 0}
    groups, hop, ineq = DC.__dict__["_cross_groups"], DC.__dict__["_direct_hop_rows"], DC.__dict__["_inequality_rows"]
    rows_fn = DC.__dict__["_source_cross_rows"]

    def cross_groups(self, table_name):
        # a filter from another table the grouping actually leaves out:
        # ALL()'s snapshot of it, or a row transition's back on its own table
        fc = self.filter_context
        snap = self._no_prop_keys.get(table_name) or {}
        for fk, v in fc.items():
            if fk.partition(".")[0] == table_name:
                continue
            if fk in snap and snap[fk] == self._filter_sig_exact(v):
                seen["suppressed"] += 1
            elif (type(v) is de._ExpansionValues and v.origin == table_name
                  and self._filter_sig(fc.get(v.anchor_key)) == v.anchor_sig):
                seen["expansion"] += 1
        return groups(self, table_name)

    def direct_hop(self, *a):
        out = hop(self, *a)
        seen["buckets"] += out is not None
        return out

    def inequality(self, *a):
        out = ineq(self, *a)
        seen["inequality"] += out is not None
        return out

    def rows(self, table_name, tbl, src_table, src_tbl, col_filters, mkey):
        memo = self._filter_idx_cache.get(("xtf-rows",))
        seen["memo hits" if memo is not None and mkey in memo else "memo misses"] += 1
        return rows_fn(self, table_name, tbl, src_table, src_tbl, col_filters, mkey)
    monkeypatch.setattr(DC, "_cross_groups", cross_groups)
    monkeypatch.setattr(DC, "_direct_hop_rows", direct_hop)
    monkeypatch.setattr(DC, "_inequality_rows", inequality)
    monkeypatch.setattr(DC, "_source_cross_rows", rows)
    diffs = []
    for seed in range(FUZZ_SEEDS):
        diffs += _differences(seed)
    assert not diffs, diffs[:5]
    # every part ran, the grouping left out a filter by ALL()'s snapshot and
    # one by a row's expansion, and the memo answered most per-source asks
    assert min(seen.values()) > 0, seen
    assert seen["memo hits"] > seen["memo misses"], seen


def _plant_memo_key(monkeypatch, cut):
    orig = DC.__dict__["_source_memo_key"]
    monkeypatch.setattr(DC, "_source_memo_key", lambda self, *a: (lambda k: None if k is None else cut(k))(
        orig(self, *a)))


def _plant_no_suppression(monkeypatch):
    orig = DC.__dict__["_cross_groups"]

    def cross_groups(self, table_name):
        saved = self._no_prop_keys
        self._no_prop_keys = {}
        try:
            return orig(self, table_name)
        finally:
            self._no_prop_keys = saved
    monkeypatch.setattr(DC, "_cross_groups", cross_groups)


def _plant_in_hop(monkeypatch, attr, value):
    orig = DC.__dict__["_direct_hop_rows"]

    def direct_hop(self, *a):
        setattr(self, attr, value)
        try:
            return orig(self, *a)
        finally:
            delattr(self, attr)
    monkeypatch.setattr(DC, "_direct_hop_rows", direct_hop)


def _plant_le_as_lt(monkeypatch):
    orig = DC.__dict__["_one_inequality"].__func__
    monkeypatch.setattr(DC, "_one_inequality", staticmethod(
        lambda tbl, ci, order, op, t: orig(tbl, ci, order, "<" if op in ("<=", "=<") else op, t)))


def _plant_no_blank_class(monkeypatch):
    orig = DC.__dict__["_inequality_order"]

    def order(self, tbl, ci):
        o = orig(self, tbl, ci)
        return o if o is de._UNINDEXED else (o[0], o[1], frozenset(), o[3], o[4])
    monkeypatch.setattr(DC, "_inequality_order", order)


PLANTS = {
    "memo key without the date table": lambda mp: _plant_memo_key(mp, lambda k: k[:7]),
    "memo key without the filters": lambda mp: _plant_memo_key(mp, lambda k: k[:4] + (frozenset(),) + k[5:]),
    "memo key without the relationships": lambda mp: _plant_memo_key(mp, lambda k: k[:5] + (0,) + k[6:]),
    "grouping ignores ALL()'s snapshot": _plant_no_suppression,
    "bucket hop drops the blank row": lambda mp: _plant_in_hop(mp, "blank_row_tables", lambda: frozenset()),
    "bucket hop ignores key ownership": lambda mp: _plant_in_hop(mp, "_non_owner_rows", lambda *a: frozenset()),
    "<= read as <": _plant_le_as_lt,
    "BLANK class dropped": _plant_no_blank_class,
}


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_the_fuzz_catches_a_planted_bug(monkeypatch, plant):
    seeds = (4, 15)
    for seed in seeds:
        _reference(seed)            # computed before the plant goes in
    PLANTS[plant](monkeypatch)
    assert any(_differences(seed) for seed in seeds), plant
