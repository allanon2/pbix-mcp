"""Issue #186: a measure memo shared by the calls of one query.

A grouped visual evaluated one row per evaluate_measures_batch call built a
new DAXContext, and with it an empty memo, for every row: each row
recomputed the per-item values of a RANKX over ALL(...), which do not depend
on the row. Agents Performance's 293-row employee table took 3.2 s a row
(~950 s); with one ``cache`` dict passed to every call it takes 32 s.

The cache must never change an answer: it is named by the model, and a
measure that reaches ALLSELECTED is keyed by the query's selection.
"""

import random

import pytest

from pbix_mcp.dax import engine as dax

pytestmark = pytest.mark.unit

_N = 40


def _model(seed=3, n=_N, scale=1.0):
    rnd = random.Random(seed)
    emps = list(range(1, n + 1))
    fact = [[rnd.choice(emps), round(rnd.uniform(10, 500) * scale, 2)] for _ in range(2000)]
    tables = {"Emp": {"columns": ["k"], "rows": [[e] for e in emps]},
              "Sales": {"columns": ["k", "Amount"], "rows": fact}}
    rels = [{"FromTable": "Sales", "FromColumn": "k", "ToTable": "Emp", "ToColumn": "k", "IsActive": 1}]
    return tables, rels


_MEASURES = {
    "Total": "SUM(Sales[Amount])",
    "Rank": "RANKX(FILTER(ALL(Emp[k]), NOT ISBLANK([Total])), [Total], , DESC)",
    "Share": "DIVIDE([Total], CALCULATE([Total], ALLSELECTED(Emp)))",
}


def _rows(tables, rels, measures, cache, names=("Rank", "Share"), selected=None, group=True, keys=None):
    out = {}
    for e in keys or range(1, _N + 1):
        kw = {"group_keys": {"Emp.k"}, "selected_filters": selected or {}} if group else {}
        r = dax.evaluate_measures_batch(list(names), tables, measures, {"Emp.k": [e]},
                                        relationships=rels, cache=cache, **kw)
        out[e] = tuple(None if r[n] is None else round(r[n], 9) for n in names)
    return out


@pytest.fixture
def total_count(monkeypatch):
    """How many times [Total]'s expression is evaluated (not served by a memo)."""
    calls = {"n": 0}
    real = dax._engine._eval_expr

    def counting(expr, ctx, *a, **kw):
        if isinstance(expr, str) and expr.strip() == "SUM(Sales[Amount])":
            calls["n"] += 1
        return real(expr, ctx, *a, **kw)

    monkeypatch.setattr(dax._engine, "_eval_expr", counting)
    return calls


class TestSharedMemo:
    def test_same_answers(self):
        tables, rels = _model()
        assert _rows(tables, rels, dict(_MEASURES), {}) == _rows(tables, rels, dict(_MEASURES), None)

    def test_rank_items_computed_once_per_query(self, total_count):
        tables, rels = _model()
        _rows(tables, rels, dict(_MEASURES), None, names=("Rank",))
        per_row = total_count["n"]
        total_count["n"] = 0
        _rows(tables, rels, dict(_MEASURES), {}, names=("Rank",))
        shared = total_count["n"]
        # one [Total] per employee (plus the rows' own), not one per employee per row
        assert per_row >= _N * _N
        assert shared <= 2 * _N

    def test_measures_dict_copied_per_call(self, total_count):
        # The issue's repro passes dict(measures) to every call.
        tables, rels = _model()
        cache: dict = {}
        for e in range(1, _N + 1):
            dax.evaluate_measures_batch(["Rank"], tables, dict(_MEASURES), {"Emp.k": [e]},
                                        relationships=rels, cache=cache)
        assert total_count["n"] <= 2 * _N


class TestNothingLeaks:
    def test_allselected_keyed_by_the_selection(self):
        # One cache across two queries whose selections differ: the grouped
        # rows of the whole table, then the same rows sliced to employees 1-5.
        tables, rels = _model()
        shared: dict = {}
        whole = _rows(tables, rels, dict(_MEASURES), shared)
        sliced = _rows(tables, rels, dict(_MEASURES), shared, selected={"Emp.k": [1, 2, 3, 4, 5]},
                       keys=range(1, 6))
        assert whole == _rows(tables, rels, dict(_MEASURES), None)
        assert sliced == _rows(tables, rels, dict(_MEASURES), None, selected={"Emp.k": [1, 2, 3, 4, 5]},
                               keys=range(1, 6))
        assert sliced[1][1] != whole[1][1]

    def test_ungrouped_rows_allselected(self):
        # Without group keys each call's filter is its own selection: Share is 1.
        tables, rels = _model()
        shared: dict = {}
        got = _rows(tables, rels, dict(_MEASURES), shared, names=("Share",), group=False)
        assert set(got.values()) == {(1.0,)}
        assert got == _rows(tables, rels, dict(_MEASURES), None, names=("Share",), group=False)

    def test_two_models_one_cache(self):
        t1, rels = _model(seed=3)
        t2, _ = _model(seed=3, scale=2.0)
        shared: dict = {}
        a = dax.evaluate_measures_batch(["Total"], t1, dict(_MEASURES), {"Emp.k": [1]},
                                        relationships=rels, cache=shared)
        b = dax.evaluate_measures_batch(["Total"], t2, dict(_MEASURES), {"Emp.k": [1]},
                                        relationships=rels, cache=shared)
        assert b["Total"] == pytest.approx(2 * a["Total"])

    def test_changed_measure_one_cache(self):
        tables, rels = _model()
        shared: dict = {}
        a = dax.evaluate_measures_batch(["Total"], tables, dict(_MEASURES), {"Emp.k": [1]},
                                        relationships=rels, cache=shared)
        m2 = dict(_MEASURES, Total="SUM(Sales[Amount]) * 3")
        b = dax.evaluate_measures_batch(["Total"], tables, m2, {"Emp.k": [1]},
                                        relationships=rels, cache=shared)
        assert b["Total"] == pytest.approx(3 * a["Total"])

    def test_smart_takes_the_cache(self):
        tables, rels = _model()
        shared: dict = {}
        got = [dax.evaluate_measures_smart(["Rank"], tables, dict(_MEASURES), {"Emp.k": [e]},
                                           relationships=rels, group_by={"Emp.k"}, cache=shared)["Rank"]
               for e in range(1, 11)]
        want = [dax.evaluate_measures_smart(["Rank"], tables, dict(_MEASURES), {"Emp.k": [e]},
                                            relationships=rels, group_by={"Emp.k"})["Rank"]
                for e in range(1, 11)]
        assert got == want
