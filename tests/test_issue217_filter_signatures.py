"""Issue #217: a filter's memo signature spells each value with its type.

_filter_sig / _filter_sig_exact spelled In-set values with str(), so the
float 1.0 and the text "1.0", and BLANK and the text "None", got one
signature although they select different rows of a text column: 1.0 selects
"1" (its own text spelling) and not "1.0", and "None" selects the text "None"
(Desktop; until #221 the engine had it select BLANK as well). Every memo keyed by a signature then answered the second filter
with the first one's result: the model-wide cross-filter memo, the measure
memo of a query cache (evaluate_measures_batch ``cache=``), the memo of a
filter on column combinations (whose predicate was spelled by
json.dumps(default=str), a date as its ISO text). The measure memo of a
measure that reaches ALLSELECTED keyed a plain list by its values
themselves, where True == 1 == 1.0, so even one batch answered
TREATAS({1}, T[c]) with TREATAS({TRUE()}, T[c])'s result. One evaluation on a
fresh model was hit too: SUMX over a column holding BLANK and the text "None"
keyed the two rows' measure alike and answered the second row with the
first one's result.

Expected values: the engine's own answers on a fresh model (a new tables
mapping has a filter cache of its own). The signatures only key memos, so the
same filters must give the same answers in either order on one model and on
a fresh one; the rows each value selects are _filter_value_keys' (no Desktop
probe: the issue is the memo, not the matching). Spelling a date with its
type made signing a filter on column combinations dearer, and every read of
every table signed it again: it is now signed once per filter set, which
test_a_filter_on_column_combinations_is_signed_once counts."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.formats.vertipaq_decoder import _oa_date_to_python

pytestmark = pytest.mark.unit

SIG = de.DAXContext._filter_sig
XSIG = de.DAXContext._filter_sig_exact
TUPLE_KEY = de._tuple_filter_key([("T", "d"), ("T", "c")])


def _tuple_spec(rows):
    return {"tuple_columns": [["T", "d"], ["T", "c"]], "rows": rows}


# Values str() spells alike. Two filters now share a signature only when they
# hold the same values of the same types; 1.0 / "1.0" and BLANK / "None" select
# different rows of a text column, 1e16 / "1e+16" too ('10000000000000000' is
# 1e16's text), and a date / its ISO text in a filter on column combinations.
APART = [
    ("float-text", [1.0], ["1.0"]),
    ("blank-text", [None], ["None"]),
    ("bool-text", [True], ["True"]),
    ("exponent", [1e16], ["1e+16"]),
    ("date-text", [date(2024, 1, 1)], ["2024-01-01"]),
    ("datetime-text", [datetime(2024, 1, 1)], ["2024-01-01 00:00:00"]),
    ("mixed", [1.0, "a"], ["1.0", "a"]),
    ("scalar-float", 1.0, "1.0"),
    ("scalar-blank", None, "None"),
    ("combinations", _tuple_spec([[date(2024, 1, 1), "a"]]), _tuple_spec([["2024-01-01", "a"]])),
    ("predicate-in", {"in": [date(2024, 1, 1)]}, {"in": ["2024-01-01"]}),
]


@pytest.mark.parametrize("a,b", [(a, b) for _i, a, b in APART], ids=[i for i, _a, _b in APART])
def test_values_printing_alike_get_different_signatures(a, b):
    assert SIG(a) != SIG(b)
    assert XSIG(a) != XSIG(b)


def test_the_same_values_keep_one_signature():
    assert SIG([1.0, "a", None]) == SIG([None, "a", 1.0]) == SIG({None, "a", 1.0})
    assert SIG(de._row_value([2, "x"], None)) == SIG([2, "x"])
    assert XSIG({"op": "<=", "value": date(2024, 1, 1)}) == XSIG({"value": date(2024, 1, 1), "op": "<="})
    assert isinstance(hash(SIG([1, 2.5, None, "t", True, date(2024, 1, 1)])), int)   # a memo key
    # a date-time decoded twice from one serial (two cells of a column)
    one, again = _oa_date_to_python(45000.25), _oa_date_to_python(45000.25)
    assert one is not again
    assert SIG([one]) == SIG([again])
    assert SIG(_tuple_spec([[one, "a"]])) == SIG(_tuple_spec([[again, "a"]]))


def _serials_within_a_microsecond():
    """Two adjacent serials whose decoded date-times print alike: the
    datetime rounds a serial to the microsecond, and a double near 45000
    days steps by about 0.6 of one."""
    s = 45000.25
    for _ in range(20):
        t = math.nextafter(s, math.inf)
        if _oa_date_to_python(s) == _oa_date_to_python(t):
            return s, t
        s = t
    raise AssertionError("no two serials within one microsecond")


def test_a_decoded_date_time_is_signed_by_its_serial():
    # vertipaq_decoder.DAXDateTime keeps the serial its str() rounds
    s, t = _serials_within_a_microsecond()
    a, b = _oa_date_to_python(s), _oa_date_to_python(t)
    assert str(a) == str(b) and (a.oa_serial, b.oa_serial) == (s, t)
    assert SIG([a]) != SIG([b])
    filler = [f"k{i:05d}" for i in range(5000)]
    assert XSIG(filler + [a]) != XSIG(filler + [b])
    assert SIG(_tuple_spec([[a, "a"]])) != SIG(_tuple_spec([[b, "a"]]))


def test_a_large_filter_digests_the_types_and_every_value_apart():
    filler = [f"k{i:05d}" for i in range(5000)]
    assert SIG(filler + [1.0]) is None                     # past 4096 values: the digest
    assert XSIG(filler + [1.0]) != XSIG(filler + ["1.0"])
    assert XSIG(filler + [None]) != XSIG(filler + ["None"])
    # a value holding the old separator cannot make two sets one
    assert XSIG(filler + ["a\x1fb", "c"]) != XSIG(filler + ["a", "b\x1fc"])
    assert XSIG(filler + [1.0]) == XSIG([1.0] + filler[::-1])


# D[Code] holds '1', '1.0', BLANK and 'None'; F reaches D through K
def _code_model():
    d = {"columns": ["Key", "Code"], "rows": [[1, "1"], [2, "1.0"], [3, None], [4, "None"]]}
    f = {"columns": ["K", "Amt"], "rows": [[1, 10.0], [2, 100.0], [3, 1000.0], [4, 10000.0]]}
    return {"D": d, "F": f}


CODE_RELS = [{"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "Key",
              "IsActive": 1, "FromCardinality": 2, "ToCardinality": 1}]
CODE_CASES = [  # filter a, filter b, SUM(F[Amt]) under each
    ([1.0], ["1.0"], 10.0, 100.0),          # 1.0 selects '1' (its text spelling), "1.0" only '1.0'
    ([None], ["None"], 1000.0, 10000.0),    # "None" selects the text 'None' alone (#221)
]


def _total(tables, fc, cache=None):
    return de.evaluate_measures_batch(["T"], tables, {"T": "SUM(F[Amt])"}, {"D.Code": fc},
                                      relationships=CODE_RELS, cache=cache)["T"]


@pytest.mark.parametrize("a,b,want_a,want_b", CODE_CASES)
def test_the_cross_filter_memo_answers_each_filter_in_either_order(a, b, want_a, want_b):
    assert (_total(_code_model(), a), _total(_code_model(), b)) == (want_a, want_b)
    tables = _code_model()
    assert (_total(tables, a), _total(tables, b)) == (want_a, want_b)
    tables = _code_model()
    assert (_total(tables, b), _total(tables, a)) == (want_b, want_a)


@pytest.mark.parametrize("a,b,want_a,want_b", CODE_CASES)
def test_the_measure_memo_of_a_query_cache_answers_each_filter(a, b, want_a, want_b):
    tables, cache = _code_model(), {}
    assert (_total(tables, a, cache), _total(tables, b, cache)) == (want_a, want_b)
    tables, cache = _code_model(), {}
    assert (_total(tables, b, cache), _total(tables, a, cache)) == (want_b, want_a)


def test_a_direct_filter_through_a_query_cache():
    # no relationship: the rows come from the column's own index; the
    # measure memo of the shared query cache keyed both filters alike
    def run(tables, fc, cache):
        return de.evaluate_measures_batch(["S"], tables, {"S": "SUM(T[v])"}, {"T.c": fc},
                                          relationships=[], cache=cache)["S"]

    def model():
        return {"T": {"columns": ["c", "v"], "rows": [["None", 1.0], [None, 2.0], ["x", 4.0]]}}

    tables, cache = model(), {}
    assert (run(tables, [None], cache), run(tables, ["None"], cache)) == (2.0, 1.0)
    tables, cache = model(), {}
    assert (run(tables, ["None"], cache), run(tables, [None], cache)) == (1.0, 2.0)


def test_treatas_blank_and_the_text_none_across_a_relationship():
    def model():
        return {"D": {"columns": ["k", "c"], "rows": [[1, "None"], [2, None], [3, "x"]]},
                "T": {"columns": ["k", "v"], "rows": [[1, 1.0], [2, 2.0], [3, 4.0]]}}
    rels = [{"FromTable": "T", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
             "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
    measures = {"TB": "CALCULATE(SUM(T[v]), TREATAS({BLANK()}, D[c]))",
                "TN": 'CALCULATE(SUM(T[v]), TREATAS({"None"}, D[c]))'}
    want = {"TB": 2.0, "TN": 1.0}       # Desktop: TREATAS({"None"}, ...) is the text alone (#221)
    for order in (("TB", "TN"), ("TN", "TB")):
        tables = model()
        got = {m: de.evaluate_measure(m, tables, measures, relationships=rels) for m in order}
        assert got == want, order


def test_a_measure_reaching_allselected_keys_true_apart_from_one():
    def model():
        return {"T": {"columns": ["c", "x", "v"],
                      "rows": [["true", "p", 1.0], ["1", "p", 10.0], ["x", "q", 100.0]]}}
    measures = {"R": "CALCULATE(SUM(T[v]), ALLSELECTED(T[x]))",
                "MB": "CALCULATE([R], TREATAS({TRUE()}, T[c]))",
                "MI": "CALCULATE([R], TREATAS({1}, T[c]))"}
    fresh = {m: de.evaluate_measures_batch([m], model(), measures, {}, relationships=[])[m]
             for m in ("MB", "MI")}
    assert fresh == {"MB": 1.0, "MI": 10.0}
    for order in (["MB", "MI"], ["MI", "MB"]):
        # one batch shares one measure memo
        assert de.evaluate_measures_batch(order, model(), measures, {}, relationships=[]) == fresh


def test_a_filter_on_column_combinations_keys_a_date_apart_from_its_text():
    def model():
        return {"T": {"columns": ["d", "c", "v"],
                      "rows": [[date(2024, 1, 1), "a", 1.0], ["2024-01-01", "a", 10.0]]}}

    def run(tables, rows):
        return de.evaluate_measure("S", tables, {"S": "SUM(T[v])"}, {TUPLE_KEY: _tuple_spec(rows)})

    a, b = [[date(2024, 1, 1), "a"]], [["2024-01-01", "a"]]
    assert (run(model(), a), run(model(), b)) == (1.0, 10.0)
    tables = model()
    assert (run(tables, a), run(tables, b)) == (1.0, 10.0)
    tables = model()
    assert (run(tables, b), run(tables, a)) == (10.0, 1.0)


def test_one_iteration_keys_blank_apart_from_the_text_none():
    # one evaluation on a fresh model: each row's [S] under its own value
    def model():
        return {"D": {"columns": ["k", "c"], "rows": [[1, None], [2, "None"], [3, "x"]]},
                "F": {"columns": ["k", "v"], "rows": [[1, 1.0], [2, 10.0], [3, 100.0]]}}
    rels = [{"FromTable": "F", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    measures = {"S": "SUM(F[v])", "IT": "SUMX(VALUES(D[c]), [S])",
                "B": "CALCULATE([S], FILTER(VALUES(D[c]), ISBLANK(D[c])))",
                "N": 'CALCULATE([S], FILTER(VALUES(D[c]), D[c] == "None"))',
                "X": 'CALCULATE([S], FILTER(VALUES(D[c]), D[c] == "x"))'}
    rows = {m: de.evaluate_measure(m, model(), measures, relationships=rels) for m in ("B", "N", "X")}
    assert (rows["B"], rows["N"], rows["X"]) == (1.0, 10.0, 100.0)
    # 0.9.145 answered the "None" row with the BLANK row's 1 (102); since #221
    # the "None" row selects its own text alone (Desktop), asserted above.
    it = de.evaluate_measure("IT", model(), measures, relationships=rels)
    assert it == rows["B"] + rows["N"] + rows["X"]


def test_a_filter_on_column_combinations_is_signed_once(monkeypatch):
    # The context signs its filters once per filter set and hands the
    # signatures down to derived contexts (_filters_sigd). Every read of every
    # table signed the spec again, once per iterated date here, and with a
    # date's typed spelling that was most of the time of the iteration.
    def signed(n_days):
        days = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n_days)]
        tables = {"Cal": {"columns": ["Date"], "rows": [[d] for d in days]},
                  "F": {"columns": ["Date", "Cat", "Amt"],
                        "rows": [[d, c, 1.0] for d in days for c in "abc"]}}
        rels = [{"FromTable": "F", "FromColumn": "Date", "ToTable": "Cal", "ToColumn": "Date",
                 "IsActive": 1, "FromCardinality": 2, "ToCardinality": 1}]
        key = de._tuple_filter_key([("F", "Date"), ("F", "Cat")])
        spec = {"tuple_columns": [["F", "Date"], ["F", "Cat"]],
                "rows": [[d, c] for d in days for c in "ab"]}
        orig = de.DAXContext.__dict__["_filter_sig"].__func__
        calls = []

        def counting(allowed):
            if allowed is spec:
                calls.append(1)
            return orig(allowed)

        measures = {"S": "SUM(F[Amt])",
                    "IT": "SUMX(VALUES(Cal[Date]), [S])",
                    "ITX": "SUMX(VALUES(Cal[Date]), IF(ISCROSSFILTERED(F), [S]))"}
        with monkeypatch.context() as m:
            m.setattr(de.DAXContext, "_filter_sig", staticmethod(counting))
            got = de.evaluate_measures_batch(["IT", "ITX"], tables, measures, {key: spec},
                                             relationships=rels)
        # each date keeps its a and b rows
        assert got == {"IT": 2.0 * n_days, "ITX": 2.0 * n_days}
        return len(calls)

    assert signed(40) == signed(10) <= 2
