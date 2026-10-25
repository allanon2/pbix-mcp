"""Issue #232 (part of PR #206 by @allanon2): costs paid again for every
filter context of a query, which dominate once one context costs about a
millisecond.

- (a) _collapse_ws_outside_strings re-scanned each VAR block's text on every
  evaluation and shield pass. It is memoized (8192 entries).
- (b) _as_date_str_strict tried seven strptime formats on a miss, about
  110 us for a value that is no date. An ISO date is now built directly, and
  a value with neither '-' nor '/' answers None without strptime; the loop
  decides the rest.
- (c) _column_is_datetime scanned a relationship column (the whole column
  when it starts with BLANKs) for every outermost measure. The answer is
  kept per model, keyed by the column's position; an absent column is not
  kept, so a column a calculated-column pass appends later is asked afresh.
- (d) pbix_evaluate_dax's route (evaluate_measures_smart without a cache)
  started every call with an empty shield memo and proved the measures'
  shielded tables again: about half of a cheap measure's time. The analysis
  is kept per model while the model's parts compare the same, for the last
  four parts, so the server's top_n ranking (dict(measures), no marked date
  tables) and the main call keep theirs. It must never
  serve an analysis made for other parts: measures edited in place, other
  marked date tables -- among them the outermost measure's, which
  evaluate_measure read from the PREVIOUS evaluation before its memo key. On
  the query route, which kept the analysis across calls already, that gave
  6 where the answer is 2 after a call on another model with the date table
  marked.
- (e) _attach_query_memo serialized the whole model on every call to name
  the query memo: 0.84 s of 731 calls at 1000 measures. It is named once per
  cache. Naming by id() alone, as first proposed, served one model's values
  for another's (60 for 90 after a calculation item changed; a freed
  measures dict's id taken by the next).

Expected values: none of this may change an answer, so answers are compared
with the same evaluation without the memo (each call's own analysis, the
0.9.146 path) or with values worked out by hand; the costs are counted
(strptime calls, column scans, shield analyses, _canonical calls). The
strict date parse is compared with a frozen copy of the 0.9.146 loop.
"""
from __future__ import annotations

import itertools
import threading
from datetime import date, datetime, timedelta

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


# ---- (a) the whitespace collapse ---------------------------------------------
_VAR_TEXTS = [
    "VAR  _x =\n    SUM(T[v])\r\n\tRETURN   _x  ",
    'VAR s = "a   b\n   c" RETURN s & "  "',
    "VAR t = CALCULATE([M], 'My   Table'[c   d] = 1)  RETURN\n t",
    "VAR q = \"say \"\"hi   there\"\"\"   RETURN q",
    "VAR [x] = 1 RETURN [Return   Qty]",
    "",
    "   ",
]


def test_collapse_ws_memo_returns_what_the_function_computes():
    f = de._collapse_ws_outside_strings
    assert f.cache_info().maxsize == 8192
    for t in _VAR_TEXTS:
        assert f(t) == f.__wrapped__(t)
    hits = f.cache_info().hits
    for t in _VAR_TEXTS:
        f(t)
    assert f.cache_info().hits >= hits + len(_VAR_TEXTS)
    assert f('VAR s = "a   b" RETURN   s') == 'VAR s = "a   b" RETURN s'


# ---- (b) the strict date parse -----------------------------------------------
_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
            "%d/%m/%Y", "%m/%d/%Y")


def _loop_0_9_146(s):
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _strict_corpus():
    for y, m, d in itertools.product(["0000", "0001", "2024", "9999"], range(0, 14), range(0, 33)):
        yield f"{y}-{m:02d}-{d:02d}"
        yield f"{d:02d}/{m:02d}/{y}"
    dates = ["2024-01-15", "2024-02-29", "2023-02-29", "2024-04-31", "0000-01-01", "9999-12-31"]
    for d, h, mi, sec, fr, sep in itertools.product(
            dates, ["00", "23", "24"], ["00", "59", "60"], ["59", "60", "61"],
            [None, "5", "123456", "1234567"], ["T", " ", "t"]):
        yield f"{d}{sep}{h}:{mi}:{sec}" + (f".{fr}" if fr else "")
    fullwidth = {ord(c): chr(0xFF10 + i) for i, c in enumerate("0123456789")}
    for s in ("2024-01-15", "2024-01-15T10:20:30", "15/01/2024"):
        yield s.translate(fullwidth)
    yield from ("42475", "42475.0", "-1", "1e5", "Product 7", "", "-", "/", "2024", "2024-01",
                "20240115", "2024/01/15", "1/2/2024", "13/13/2024", " 2024-01-15", "2024-01-15 ",
                "2024-01-15  10:20:30", "2024-01-15\t10:20:30", "2024-01-15T10:20:30Z",
                "2024-01-15T1:2:3", "2024-1-5", "01/01/2017-CLUSTER 1", "100000-A", "N/A")


def test_strict_date_parse_answers_as_the_strptime_loop():
    new = de._as_date_str_strict.__wrapped__
    n = 0
    for s in _strict_corpus():
        want, got = _loop_0_9_146(s), new(s)
        assert got == want and type(got) is type(want), ascii(s)
        n += 1
    assert n > 5000


def test_strict_date_parse_skips_strptime_where_it_cannot_matter(monkeypatch):
    calls = []

    class _Counting(datetime):
        @classmethod
        def strptime(cls, s, fmt):
            calls.append(s)
            return datetime.strptime(s, fmt)

    monkeypatch.setattr(de, "datetime", _Counting)
    new = de._as_date_str_strict.__wrapped__
    assert new("2024-01-15") == date(2024, 1, 15)
    assert new("2024-01-15T10:20:30.5") == date(2024, 1, 15)
    assert new("2024-01-15 23:59:59") == date(2024, 1, 15)
    for s in ("42475", "42475.0", "Product 7", "20240115", ""):
        assert new(s) is None
    assert calls == []
    # the loop still decides what the shortcuts do not cover
    assert new("15/01/2024") == date(2024, 1, 15)
    assert new("2024-02-30") is None
    assert calls


# ---- (c) a column's datetime-ness --------------------------------------------
class _CountingRows(list):
    """Rows that count how often something iterates over all of them."""

    def __init__(self, rows, counter):
        super().__init__(rows)
        self.counter = counter

    def __iter__(self):
        self.counter[0] += 1
        return super().__iter__()


def test_a_columns_datetime_ness_is_scanned_once_per_model():
    n = [0]
    rows = _CountingRows([[None, i] for i in range(20_000)] + [[datetime(2024, 1, 1), 1]], n)
    tables = {"F": {"columns": ["D", "v"], "rows": rows}}
    ctxs = [de.DAXContext(tables, {}, "F", None, None, []) for _ in range(20)]
    n[0] = 0
    for ctx in ctxs:
        assert de.DAXEngine._column_is_datetime(ctx, "F", "D") is True
        assert de.DAXEngine._column_is_datetime(ctx, "F", "v") is False
    assert n[0] == 2


def test_an_absent_column_is_asked_again_once_it_exists():
    tables = {"F": {"columns": ["v"], "rows": [[1], [2]]}}
    ctx = de.DAXContext(tables, {}, "F", None, None, [])
    assert de.DAXEngine._column_is_datetime(ctx, "F", "DateCalc") is False
    assert not any(isinstance(k, tuple) and "is-datetime" in k for k in ctx._filter_idx_cache)
    # as the calculated-column pass appends a column: in place, rows unchanged
    tables["F"]["columns"].append("DateCalc")
    for i, row in enumerate(tables["F"]["rows"]):
        row.append(datetime(2024, 1, 1 + i))
    assert de.DAXEngine._column_is_datetime(ctx, "F", "DateCalc") is True
    ctx2 = de.DAXContext(tables, {}, "F", None, None, [])
    assert de.DAXEngine._column_is_datetime(ctx2, "F", "DateCalc") is True


def test_a_relationships_datetime_column_still_clears_its_table():
    # F[D] -> Cal[D] is a DateTime relationship (#78): a filter on Cal[D]
    # clears Cal's other filters, also when F[D] starts with BLANKs. Week 0
    # is January 1-6; cleared, the 8 days from the 3rd count, not 4.
    days = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(10)]
    tables = {"Cal": {"columns": ["D", "Wk"], "rows": [[d, d.day // 7] for d in days]},
              "F": {"columns": ["D", "v"], "rows": [[None, 100]] * 50 + [[d, 1] for d in days]}}
    rels = [{"FromTable": "F", "FromColumn": "D", "ToTable": "Cal", "ToColumn": "D", "IsActive": 1}]
    ms = {"M": "CALCULATE(SUM(F[v]), Cal[D] >= DATE(2024, 1, 3))", "N": "SUM(F[v])"}
    for _ in range(3):
        got = de.evaluate_measures_smart(["M", "N"], tables, ms, {"Cal.Wk": [0]}, None, None, rels,
                                         simulate_row_context=False)
        assert got == {"M": 8, "N": 6}
    ctx = de.DAXContext(tables, ms, None, None, None, rels)
    assert ctx._filter_idx_cache.get((id(tables["F"]), "is-datetime", 0)) is True


# ---- (d) the shield analysis on the per-filter route -------------------------
def _shield_model():
    tables = {"T": {"columns": ["k"], "rows": [[1], [2], [3]]},
              "F": {"columns": ["k", "v"], "rows": [[1, 10], [2, 20], [3, 30], [1, 5]]}}
    rels = [{"FromTable": "F", "FromColumn": "k", "ToTable": "T", "ToColumn": "k", "IsActive": 1}]
    ms = {"Base": "SUM(F[v])", "All": "CALCULATE(SUM(F[v]), ALL(T))",
          "Outer": "SUMX(VALUES(T[k]), [All])", "Share": "DIVIDE([Base], CALCULATE([Base], ALLSELECTED(T)))",
          "Rows": "SUMX(VALUES(T[k]), [Share])"}
    return tables, rels, ms


def _smart(names, tables, ms, rels, fc=None, **kw):
    return de.evaluate_measures_smart(list(names), tables, ms, fc or {}, None, None, rels,
                                      simulate_row_context=False, **kw)


def _per_call(monkeypatch, fn):
    """``fn()`` with each call's own shield analysis, as 0.9.146 did."""
    with monkeypatch.context() as m:
        m.setattr(de, "_attach_model_shield_memo", lambda ctx: None, raising=False)
        return fn()


def test_per_filter_calls_share_the_models_shield_analysis(monkeypatch):
    tables, rels, ms = _shield_model()
    calls = [0]
    real = de.DAXEngine._shield_noread

    def counting(self, *a):
        calls[0] += 1
        return real(self, *a)

    monkeypatch.setattr(de.DAXEngine, "_shield_noread", counting)
    assert _smart(["Outer"], tables, ms, rels)["Outer"] == 195
    assert _smart(["Outer"], tables, ms, rels, {"T.k": [1]})["Outer"] == 65
    first = calls[0]
    assert first > 0
    for k in (2, 3, 1, 2, 3):
        assert _smart(["Outer"], tables, ms, rels, {"T.k": [k]})["Outer"] == 65
        assert _smart(["Outer"], tables, ms, rels)["Outer"] == 195
    assert calls[0] == first
    assert ("shield-memo",) in de.DAXContext(tables, ms, None, None, None, rels)._filter_idx_cache


def test_a_measure_edited_in_place_is_analysed_again(monkeypatch):
    # 'All' is shielded from T (ALL(T) replaces T's filters), so every row of
    # Outer shares one value: 3 x 65. Edited to SUM(F[v]) it is not, and
    # Outer is the sum of the rows' own values, 65. An analysis kept by the
    # dict's identity alone answered 45 (the first row's 15, three times).
    tables, rels, ms = _shield_model()
    assert _smart(["Outer"], tables, ms, rels)["Outer"] == 195
    ms["All"] = "SUM(F[v])"
    assert _smart(["Outer"], tables, ms, rels)["Outer"] == 65
    ms2 = dict(ms, All="CALCULATE(SUM(F[v]), ALL(T))")
    assert _smart(["Outer"], tables, ms2, rels)["Outer"] == 195
    assert _smart(["Outer"], tables, ms, rels)["Outer"] == 65


def _date_model():
    days = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(90)]
    tables = {"D": {"columns": ["Key", "Date", "Month"], "rows": [[i + 1, d, d.month] for i, d in enumerate(days)]},
              "F": {"columns": ["Key", "v"], "rows": [[i + 1, 1] for i in range(90)]}}
    rels = [{"FromTable": "F", "FromColumn": "Key", "ToTable": "D", "ToColumn": "Key", "IsActive": 1}]
    ms = {"Cum": "CALCULATE(SUM(F[v]), FILTER(ALL(D[Date]), D[Date] <= DATE(2024, 1, 2)))",
          "Outer": "SUMX(VALUES(D[Month]), [Cum])"}
    return tables, rels, ms


def test_other_marked_date_tables_are_analysed_again(monkeypatch):
    # Marked, D[Date]'s filter clears D[Month]: each of the 3 months sees the
    # 2 days, 6. Not marked, the dates meet the month: January's 2.
    tables, rels, ms = _date_model()
    for dts, want in (({"D": "Date"}, 6), (None, 2), ({"D": "Date"}, 6), ({}, 2)):
        assert _smart(["Outer"], tables, ms, rels, date_tables=dts)["Outer"] == want
        assert _per_call(monkeypatch, lambda: _smart(["Outer"], tables, ms, rels, date_tables=dts))["Outer"] == want


def test_the_outermost_measure_reads_its_own_marked_date_tables():
    # A call over a model whose D is marked, then calls over another model
    # with the same names and D not marked: 'Cum' first as the outermost
    # measure of a grouped row. Its analysis read the previous evaluation's
    # date tables, and the per-model memo then served it to 'Outer': 6.
    t1, rels, ms = _date_model()
    assert _smart(["Outer"], t1, dict(ms), rels, date_tables={"D": "Date"})["Outer"] == 6
    t2, _, _ = _date_model()
    assert _smart(["Cum"], t2, ms, rels, {"D.Month": [2]}, group_by={"D.Month"})["Cum"] is None
    assert _smart(["Outer"], t2, ms, rels)["Outer"] == 2


def test_the_query_route_kept_a_stale_analysis_too():
    # Issue #245: the same on the query route (one cache for model 2's calls), where
    # 0.9.146 kept the analysis across calls already and answered 6.
    t1, rels, ms = _date_model()
    de.evaluate_measures_batch(["Outer"], t1, dict(ms), {}, None, None, rels, date_tables={"D": "Date"})
    t2, _, _ = _date_model()
    cache: dict = {}
    de.evaluate_measures_batch(["Cum"], t2, ms, {"D.Month": [2]}, None, None, rels,
                               group_keys={"D.Month"}, cache=cache)
    assert de.evaluate_measures_batch(["Outer"], t2, ms, {}, None, None, rels, cache=cache)["Outer"] == 2


def test_the_slot_follows_the_models_parts(monkeypatch):
    tables, rels, ms = _shield_model()

    def memo(**kw):
        ctx = de.DAXContext(tables, kw.get("measures", ms), None, None, None, kw.get("rels", rels))
        ctx.date_tables = kw.get("date_tables") or {}
        ctx.measure_tables = kw.get("measure_tables") or {}
        de._attach_model_shield_memo(ctx)
        return ctx._shield_cache

    m1 = memo()
    assert memo() is m1                                   # fresh empty parts compare the same
    assert memo(measures=dict(ms)) is m1                  # a copy holds the same measures
    m2 = memo(date_tables={"T": "k"})
    assert m2 is not m1 and memo(date_tables={"T": "k"}) is m2
    assert memo(rels=[dict(rels[0], IsActive=0)]) is not m2
    m3 = memo()
    de.set_calculation_groups(ms, [{"table": "CG", "column": "Name", "precedence": 0,
                                    "items": [{"name": "X", "expression": "SELECTEDMEASURE() * 2"}]}])
    try:
        m4 = memo()
        assert m4 is not m3 and memo() is m4
        de.set_calculation_groups(ms, [{"table": "CG", "column": "Name", "precedence": 0,
                                        "items": [{"name": "X", "expression": "SELECTEDMEASURE() * 3"}]}])
        assert memo() is not m4
    finally:
        de.set_calculation_groups(ms, None)
    # it starts afresh past its bound
    m5 = memo()
    m5.update({("filler", i): i for i in range(10)})
    monkeypatch.setattr(de, "_SHIELD_MEMO_MAX", 5)
    assert memo() is not m5
    # it keeps the last four parts' memos
    for i in range(6):
        memo(date_tables={"T": f"c{i}"})
    entry = de.DAXContext(tables, ms, None, None, None, rels)._filter_idx_cache[("shield-memo",)]
    assert isinstance(entry, tuple) and len(entry) == de._SHIELD_MEMO_SLOTS == 4


def test_two_kinds_of_call_keep_their_analyses(monkeypatch):
    # The server's top_n ranking (_resolve_topn_filters) evaluates over
    # dict(measures) without the marked date tables, the main call over the
    # model's own: with one slot each replaced the other's analysis.
    tables, rels, ms = _date_model()
    calls = [0]
    real = de.DAXEngine._shield_noread

    def counting(self, *a):
        calls[0] += 1
        return real(self, *a)

    def main():
        return _smart(["Outer"], tables, ms, rels, date_tables={"D": "Date"})["Outer"]

    def ranking(month):
        return _smart(["Cum"], tables, dict(ms), rels, {"D.Month": [month]})["Cum"]

    want = (main(), [ranking(m) for m in (1, 2, 3)])
    assert _per_call(monkeypatch, lambda: (main(), [ranking(m) for m in (1, 2, 3)])) == want == (6, [2, None, None])
    monkeypatch.setattr(de.DAXEngine, "_shield_noread", counting)
    assert (main(), [ranking(m) for m in (1, 2, 3)]) == want
    first = calls[0]
    for _ in range(4):
        assert (main(), [ranking(m) for m in (1, 2, 3)]) == want
    assert calls[0] == first


def test_userelationship_answers_as_each_calls_own_analysis(monkeypatch):
    days = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(20)]
    tables = {"D": {"columns": ["Date", "Wk"], "rows": [[d, d.day // 7] for d in days]},
              "F": {"columns": ["Ord", "Ship", "v"],
                    "rows": [[d, d + timedelta(days=3), i] for i, d in enumerate(days)]}}
    rels = [{"FromTable": "F", "FromColumn": "Ord", "ToTable": "D", "ToColumn": "Date", "IsActive": 1},
            {"FromTable": "F", "FromColumn": "Ship", "ToTable": "D", "ToColumn": "Date", "IsActive": 0}]
    ms = {"S": "SUM(F[v])",
          "ByShip": "CALCULATE([S], USERELATIONSHIP(F[Ship], D[Date]))",
          "AllShip": "CALCULATE([ByShip], ALL(D))",
          "Weeks": "SUMX(VALUES(D[Wk]), [AllShip] + [ByShip])"}
    fcs = [{}, {"D.Wk": [0]}, {"D.Wk": [1, 2]}, {"D.Date": [days[4].isoformat()]}]
    want = _per_call(monkeypatch, lambda: [_smart(list(ms), tables, ms, rels, fc) for fc in fcs])
    for _ in range(2):
        assert [_smart(list(ms), tables, ms, rels, fc) for fc in fcs] == want


def test_threads_sharing_the_memo_answer_as_one_thread():
    tables, rels, ms = _shield_model()
    fcs = [{}, {"T.k": [1]}, {"T.k": [2, 3]}, {"T.k": [3]}] * 6
    names = ["Outer", "Rows", "Share", "All"]
    serial = [_smart(names, tables, ms, rels, fc) for fc in fcs]
    # a fresh model each round, so the threads race to build its memo
    for _ in range(3):
        tables, rels, ms = _shield_model()
        out: dict = {}
        errors: list = []
        barrier = threading.Barrier(8)

        def work(t):
            try:
                barrier.wait()
                out[t] = [_smart(names, tables, ms, rels, fc) for fc in fcs]
            except Exception as e:     # noqa: BLE001 -- reported below
                errors.append(e)

        threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert not errors
        assert all(out[t] == serial for t in range(8))


# ---- (e) naming the query memo -----------------------------------------------
def _cg(ms, expr):
    de.set_calculation_groups(ms, [{"table": "CG", "column": "Name", "precedence": 0,
                                    "items": [{"name": "X", "expression": expr, "ordinal": 0}]}])


_T = {"T": {"columns": ["c", "v"], "rows": [["a", 10.0], ["b", 20.0]]}, "CG": {"columns": ["Name"], "rows": [["X"]]}}
_RELS = [{"FromTable": "T", "FromColumn": "c", "ToTable": "T", "ToColumn": "c", "IsActive": 0}]


def test_a_changed_calculation_item_is_not_served_from_the_old_name():
    ms = {"S": "SUM(T[v])"}
    cache: dict = {}
    try:
        got = []
        for expr in ("SELECTEDMEASURE() * 2", "SELECTEDMEASURE() * 3"):
            _cg(ms, expr)
            got.append(de.evaluate_measures_smart(["S"], _T, ms, {"CG.Name": ["X"]}, None, None, _RELS,
                                                  simulate_row_context=False, cache=cache)["S"])
        assert got == [60.0, 90.0]
    finally:
        de.set_calculation_groups(ms, None)


def test_a_freed_measures_dict_never_names_the_next_ones_memo():
    # CPython gives the next dict of the same size the freed one's address
    # (60 times in 60 rounds measured), which an id()-named memo took for
    # the same model
    cache: dict = {}
    for k in range(100):
        m1 = {"M": f"SUM(T[v]) + {k}"}
        assert de.evaluate_measures_batch(["M"], _T, m1, {}, None, None, _RELS, cache=cache)["M"] == 30.0 + k
        del m1
        m2 = {"M": f"SUM(T[v]) * 100 + {k}"}
        assert de.evaluate_measures_batch(["M"], _T, m2, {}, None, None, _RELS, cache=cache)["M"] == 3000.0 + k
        del m2


def test_measures_edited_in_place_rename_the_memo():
    ms = {"M": "SUM(T[v])"}
    cache: dict = {}
    assert de.evaluate_measures_batch(["M"], _T, ms, {}, None, None, _RELS, cache=cache)["M"] == 30.0
    ms["M"] = "SUM(T[v]) * 2"
    assert de.evaluate_measures_batch(["M"], _T, ms, {}, None, None, _RELS, cache=cache)["M"] == 60.0
    ms["N"] = "[M] + 1"
    assert de.evaluate_measures_batch(["N"], _T, ms, {}, None, None, _RELS, cache=cache)["N"] == 61.0


def test_the_query_memo_is_named_once_per_cache(monkeypatch):
    calls = [0]
    real = de._canonical

    def counting(v):
        calls[0] += 1
        return real(v)

    monkeypatch.setattr(de, "_canonical", counting)
    ms = {"M": "SUM(T[v])", **{f"Filler {i}": f"SUM(T[v]) * {i}" for i in range(300)}}
    cache: dict = {}
    for c in ("a", "b") * 15:
        # measure_tables / model_columns / date_tables left out: the context's
        # own fresh empty dicts compare the same; dict(ms) holds the same measures
        got = de.evaluate_measures_batch(["M"], _T, dict(ms), {"T.c": [c]}, None, None, _RELS, cache=cache)
        assert got["M"] == {"a": 10.0, "b": 20.0}[c]
    named = calls[0]
    assert 0 < named <= 7          # one name: seven parts serialized once
    de.evaluate_measures_batch(["M"], _T, dict(ms, M="SUM(T[v]) + 1"), {"T.c": ["a"]}, None, None, _RELS,
                               cache=cache)
    assert calls[0] > named        # other measures: named again
