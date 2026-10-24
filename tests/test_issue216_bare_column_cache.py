"""Issue #216: an unqualified [Column] resolved against a dead model.

DAXEngine memoized which tables own a bare [X] under (id(tables), name). The
module's engine lives as long as its thread, the memo was never cleared and
did not hold the mapping, so a model built at a freed model's id resolved
[X] against the dead model's tables: on 0.9.145 SUM([X]) answered BLANK
instead of 300 in every trial where CPython reused the id (12 of 200 in our
reproduction, all 12 wrong). The same memo kept a name's owners across a
column appended in place (issue #215's shape change): [Z] asked before
T2[Z] existed stayed unresolved after.

The owners now live in the model's filter-index cache, which holds its
mapping, starts again when the mapping's shape changes, and is dropped with
the model. Expected values are hand-computed from the tables below.
"""
from __future__ import annotations

import gc

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

SUM_X = {"M": "SUM([X])"}


def model_a():
    m = {}
    m["T1"] = {"columns": ["X"], "rows": [[1], [2]]}
    m["T2"] = {"columns": ["Y"], "rows": [[10]]}
    return m


def fill_b(m):
    # [X] now lives in T2 only: 100 + 200
    m["T1"] = {"columns": ["Y"], "rows": [[5]]}
    m["T2"] = {"columns": ["X"], "rows": [[100], [200]]}
    return m


def new_mapping_at(addr, tries=200_000):
    """A new empty dict at ``addr`` (a freed mapping's id), or None. The ones
    that land elsewhere are kept alive, so each try takes another address."""
    keep = []
    for _ in range(tries):
        m: dict = {}
        if id(m) == addr:
            return m
        keep.append(m)
    return None


def test_a_model_at_a_freed_models_id_resolves_its_own_bare_columns():
    reused = 0
    for _ in range(5):
        a = model_a()
        addr = id(a)
        assert de.evaluate_measure("M", a, SUM_X) == 3
        de._SHARED_FILTER_CACHES.clear()        # the registry let the model go
        del a
        gc.collect()
        b = new_mapping_at(addr)
        if b is None:
            continue
        reused += 1
        assert de.evaluate_measure("M", fill_b(b), SUM_X) == 300
        del b
    assert reused, "CPython never reused the freed mapping's id"


def test_a_bare_column_appended_in_place_resolves():
    m = model_a()
    assert de.evaluate_measure("M", m, {"M": "SUM([Z])"}) is None     # no table owns [Z] yet
    m["T2"]["columns"].append("Z")
    for r in m["T2"]["rows"]:
        r.append(7)
    assert de.evaluate_measure("M", m, {"M": "SUM([Z])"}) == 7


def test_two_live_models_resolve_the_same_name_each_to_its_own_table():
    a, b = model_a(), fill_b({})
    for _ in range(2):
        assert de.evaluate_measure("M", a, SUM_X) == 3
        assert de.evaluate_measure("M", b, SUM_X) == 300


def test_a_bare_column_in_a_row_iteration_still_reads_the_rows_table():
    m = model_a()
    m["T3"] = {"columns": ["X"], "rows": [[50]]}        # [X] in two tables: no model-wide owner
    measures = {"M": "SUMX(T1, [X] * 2)", "C": "COUNTROWS(FILTER(T3, [X] > 1))", "S": "SUM([X])"}
    assert de.evaluate_measures_batch(["M", "C", "S"], m, measures) == {"M": 6, "C": 1, "S": None}


def test_a_transient_mapping_keeps_its_owners_in_its_own_cache():
    m = model_a()
    t = de.transient_tables(m)
    assert de.evaluate_measure("M", t, SUM_X) == 3
    assert t.filter_cache["__bare_columns__"]["X"] == ["T1"]
    assert de.evaluate_measure("M", m, SUM_X) == 3
    assert de._shared_filter_cache(m)["__bare_columns__"] is not t.filter_cache["__bare_columns__"]
