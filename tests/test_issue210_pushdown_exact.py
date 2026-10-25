"""Issue #210: the #203 rewrite of CALCULATE(<aggregate of T>, FILTER(T,
<condition>)) into KEEPFILTERS(FILTER(VALUES(T[c]), <condition>)) changed
answers. A differential fuzz (the rewrite on against off) found three
classes, and in each the table filter was right:

- a column spelled in another case: a row of VALUES(T[c]) answers only its own
  spelling, so F3[s] read nothing (BLANK where Desktop has 30) and a bare
  TBool[b] stayed true for every value (15 against 10);
- a table with a blank row: VALUES hands the blank row's BLANK to the
  condition, which FILTER(T, ...) never iterates, so SEARCH without its
  not-found value failed the whole filter (BLANK against 3);
- values an In-set aliases: the kept values are applied as an In-set, which
  reads the text "None" as BLANK too, date-like text as its moment and a
  number as its float (3 against 1, 59 against 49).

The rewrite is now made only where every reference is spelled as the model
spells it, the condition is no error for the table's blank row (when it has
one), and no two cells of a column filtered share such a reading. A condition
TRUE or FALSE for the blank row keeps the rewrite: the BLANK it keeps selects
only rows FILTER keeps too, and the blank row, which none of the aggregates
counts. Refusing TRUE as well (the first version of this fix) sent a 1:1
side's conditions to the table filter, which drops the unmatched row (issue
#227): FILTER(FC, FC[A] >= 0) went from Desktop's 30 to 10.

Expected values: Power BI Desktop 2.152 over ADOMD, battery
_desktop_oracle/build_b210.py (57 probes); the tables below are that
battery's. Where Desktop and the table filter agree, both paths are asserted.
On a 1:1 relationship the table filter drops the unmatched row (issue #227),
so there the Desktop value is asserted with the rewrite only -- for a FILTER
that keeps the rows of a measured probe (c11_rows 2, c11_filter_true 30,
c11_ec_rows 2) -- and the differential fuzz below leaves 1:1 out. Shapes no
battery measured (integers past 2**53, a column mixing text and numbers) are
asserted against the table filter, which compares the cells themselves.
"""
from __future__ import annotations

import random
from datetime import date, datetime

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit


def tables():
    return {
        "TA": {"columns": ["S", "A"], "rows": [["None", 1], [None, 2], ["x", 4]]},
        "TB": {"columns": ["S", "A"], "rows": [["2020-01-01", 1], ["2020-01-01T00:00:00", 2], ["x", 4],
                                               ["1/2/2020", 8], ["01/02/2020", 16], ["2020-02-01", 32]]},
        "TC": {"columns": ["TS", "A"], "rows": [["2020-01-01T10:00:00+01:00", 1],
                                                ["2020-01-01T10:00:00+02:00", 2], ["x", 4]]},
        "FC": {"columns": ["K", "N", "A"], "rows": [[1, "xa", 10], [2, "ya", 20]]},
        "EC": {"columns": ["K", "Z"], "rows": [[1, "p"], [3, "q"]]},
        # D3's blank row: the orphan fact key 9
        "D3": {"columns": ["K", "N", "R"], "rows": [[1, "xa", "North"], [2, "ya", "South"], [3, "za", "North"]]},
        "F3": {"columns": ["K", "S", "A"], "rows": [[1, "a", 10], [2, "a", 20], [1, "b", 5], [9, "c", 7]]},
        "TBool": {"columns": ["B", "A"], "rows": [[True, 10], [False, 5]]},
        # the Artificial Intelligence Sample's shape: a fact filtered on its status
        "Opp": {"columns": ["Seq", "Status", "Rev"], "rows": [[1, "Won", 10], [2, "Lost", 5], [3, "Won", 7]]},
        # numeric text: '01' and '1' stay two values in an In-set
        "TK": {"columns": ["code", "v"], "rows": [["01", 10], ["1", 5], ["7", 1]]},
        # readings an In-set gives a cell that meet no other cell, and ones that do
        "TN": {"columns": ["S", "A"], "rows": [["None", 1], ["x", 4]]},
        "TD": {"columns": ["S", "A"], "rows": [["2020-01-01", 1], ["2020-01-02", 2], ["x", 4], [None, 8]]},
        "TI": {"columns": ["S", "A"], "rows": [["20200101", 1], ["2020-01-01", 2], ["x", 4]]},
        "TBig": {"columns": ["K", "A", "B"], "rows": [[2 ** 53, 1, 1], [2 ** 53 + 1, 1, 2], [5, 4, 4]]},
        "TMix": {"columns": ["S", "A"], "rows": [["5", 1], [5, 2], ["x", 4]]},
        "TDt": {"columns": ["D", "A"], "rows": [[date(2020, 1, 1), 1], [datetime(2020, 1, 1), 2],
                                                [date(2020, 1, 3), 4]]},
    }


RELS = [{"FromTable": "FC", "FromColumn": "K", "ToTable": "EC", "ToColumn": "K", "IsActive": 1,
         "FromCardinality": 1, "ToCardinality": 1, "CrossFilteringBehavior": 2},
        {"FromTable": "F3", "FromColumn": "K", "ToTable": "D3", "ToColumn": "K", "IsActive": 1}]


def context(expr="BLANK()"):
    return de.DAXContext(tables(), {"M": expr}, None, None, None, RELS)


def evaluate(expr, pushdown=True, monkeypatch=None):
    if not pushdown:
        monkeypatch.setattr(de.DAXEngine, "_pushdown_table_filter", lambda self, args, ctx: None)
    return de.DAXEngine().evaluate_measure("M", context(expr))


def rewrite(agg, flt):
    return de.DAXEngine()._pushdown_table_filter([agg, flt], context())


# Desktop's answers (build_b210.py), which the table filter gives too
DESKTOP = {
    'CALCULATE(SUM(F3[A]), FILTER(F3, F3[s] = "a"))': 30,                       # e_col_case
    'CALCULATE(COUNTROWS(F3), FILTER(F3, F3[S] = "a" || F3[s] = "b"))': 3,      # e_col_case_or
    'CALCULATE(SUM(TBool[A]), FILTER(TBool, TBool[b]))': 10,                    # e_bool_case
    'CALCULATE(COUNTROWS(D3), FILTER(D3, SEARCH("a", D3[N]) > 0))': 3,          # d_filter_search
    'CALCULATE(SUM(TA[A]), FILTER(TA, TA[S] = "None"))': 1,                     # a_filter_none
    'CALCULATE(SUM(TA[A]), FILTER(TA, NOT ISBLANK(TA[S])))': 5,                 # a_notblank
    'CALCULATE(SUM(TB[A]), FILTER(TB, LEN(TB[S]) = 10))': 49,                   # b_len10
    'CALCULATE(SUM(TB[A]), FILTER(TB, LEN(TB[S]) = 8))': 8,                     # b_len8
    'CALCULATE(SUM(TC[A]), FILTER(TC, RIGHT(TC[TS], 6) = "+01:00"))': 1,        # c_offset
    'CALCULATE(COUNTROWS(D3), KEEPFILTERS(FILTER(DISTINCT(D3[N]), SEARCH("a", D3[N]) > 0)))': 3,
}


@pytest.mark.parametrize("expr", list(DESKTOP))
def test_desktop_answers_with_the_rewrite_and_without_it(expr, monkeypatch):
    assert evaluate(expr) == DESKTOP[expr]
    assert evaluate(expr, pushdown=False, monkeypatch=monkeypatch) == DESKTOP[expr]


def test_values_hands_the_blank_rows_blank_to_the_condition():
    # Desktop: an error (SEARCH's text is not found) -- the BLANK the rewrite
    # would have given D3's blank row, where FILTER(D3, ...) keeps 3 rows
    expr = 'CALCULATE(COUNTROWS(D3), KEEPFILTERS(FILTER(VALUES(D3[N]), SEARCH("a", D3[N]) > 0)))'
    with pytest.raises(DAXEvaluationError):
        de.DAXEngine()._eval_expr(expr, context())


# A 1:1 side: both sides have a blank row, and the table filter drops the
# side's unmatched row (issue #227), so the rewrite must stay. Each FILTER
# keeps the rows of a measured probe: FC's two (c11_rows 2, c11_filter_true
# 30), FC's unmatched row alone (one of the two c11_rows counts), EC's two
# (c11_ec_rows 2).
ONE_TO_ONE = {
    'CALCULATE(COUNTROWS(FC), FILTER(FC, FC[A] > 0))': 2,       # c11_rows, FALSE for BLANK
    'CALCULATE(COUNTROWS(FC), FILTER(FC, FC[A] >= 0))': 2,      # TRUE for BLANK
    'CALCULATE(SUM(FC[A]), FILTER(FC, FC[A] >= 0))': 30,
    'CALCULATE(SUM(FC[A]), FILTER(FC, FC[A] < 100))': 30,
    'CALCULATE(SUM(FC[A]), FILTER(FC, FC[N] <> "zz"))': 30,
    'CALCULATE(SUM(FC[A]), FILTER(FC, NOT (FC[N] = "zz")))': 30,
    'CALCULATE(SUM(FC[A]), FILTER(FC, FC[N] <> "xa"))': 20,
    'CALCULATE(COUNTROWS(EC), FILTER(EC, EC[Z] <> "zz"))': 2,
}


@pytest.mark.parametrize("expr", list(ONE_TO_ONE))
def test_a_one_to_one_side_keeps_the_rewrite_true_or_false_for_blank(expr, monkeypatch):
    real = de.DAXEngine._pushdown_table_filter
    fired = []

    def spy(self, args, ctx):
        fired.append(real(self, args, ctx))
        return fired[-1]

    monkeypatch.setattr(de.DAXEngine, "_pushdown_table_filter", spy)
    assert evaluate(expr) == ONE_TO_ONE[expr]
    assert fired and fired[0] is not None


PUSHED = [
    # the AI Sample's [Revenue Won]: a fact without a blank row, a status text
    ('SUM(Opp[Rev])', 'FILTER(Opp, Opp[Status] = "Won")',
     'KEEPFILTERS(FILTER(VALUES(\'Opp\'[Status]), Opp[Status] = "Won"))'),
    # a dimension with a blank row, a condition FALSE for BLANK
    ('COUNTROWS(D3)', 'FILTER(D3, D3[R] = "North")',
     'KEEPFILTERS(FILTER(VALUES(\'D3\'[R]), D3[R] = "North"))'),
    ('COUNTROWS(D3)', 'FILTER(D3, NOT ISERROR(SEARCH("a", D3[N])))', None),
    # ... or TRUE for it: the kept BLANK selects D3's BLANK cells, which
    # FILTER keeps too, and the blank row, which COUNTROWS(D3) does not count
    ('COUNTROWS(D3)', 'FILTER(D3, ISBLANK(D3[R]))', None),
    ('COUNTROWS(D3)', 'FILTER(D3, D3[R] <> "North")', None),
    ('COUNTROWS(D3)', 'FILTER(D3, ISBLANK(D3[R]) || D3[K] > 2)', None),
    ('SUM(FC[A])', 'FILTER(FC, FC[A] >= 0)', None),
    # combinations are matched exactly: "None" and date-like text are fine there
    ('SUM(TA[A])', 'FILTER(TA, TA[S] = "None" && TA[A] > 0)',
     'KEEPFILTERS(FILTER(SUMMARIZE(\'TA\', \'TA\'[S], \'TA\'[A]), TA[S] = "None" && TA[A] > 0))'),
    ('SUM(TB[A])', 'FILTER(TB, LEN(TB[S]) = 10 && TB[A] > 0)', None),
    # numbers and numeric text are not aliased
    ('SUM(F3[A])', 'FILTER(F3, F3[A] > 6)', None),
    ('SUM(F3[A])', 'FILTER(F3, F3[S] = "a")', None),
    ('SUM(TK[v])', 'FILTER(TK, TK[code] = "01")', None),
    # a reading that meets no other cell: "None" without a BLANK cell, dates
    # spelled one way
    ('SUM(TN[A])', 'FILTER(TN, TN[S] = "None")', None),
    ('SUM(TN[A])', 'FILTER(TN, ISBLANK(TN[S]))', None),
    ('SUM(TD[A])', 'FILTER(TD, LEN(TD[S]) = 10)', None),
    # the ISO basic format is no date on any Python (issue #242): "20200101"
    # meets no other cell
    ('SUM(TI[A])', 'FILTER(TI, LEN(TI[S]) = 8)', None),
]


@pytest.mark.parametrize("agg,flt,want", PUSHED)
def test_the_rewrite_is_kept(agg, flt, want):
    got = rewrite(agg, flt)
    assert got is not None and got[0] == agg
    if want is not None:
        assert got[1] == want


NOT_PUSHED = {
    "a column in another case": ['SUM(F3[A])', 'FILTER(F3, F3[s] = "a")'],
    "one of two references in another case": ['COUNTROWS(F3)', 'FILTER(F3, F3[S] = "a" || F3[s] = "b")'],
    "a bare boolean in another case": ['SUM(TBool[A])', 'FILTER(TBool, TBool[b])'],
    "the table in another case in the condition": ['SUM(F3[A])', 'FILTER(F3, f3[S] = "a")'],
    "the aggregate's column in another case": ['SUM(F3[a])', 'FILTER(F3, F3[S] = "a")'],
    "the aggregate's table in another case": ['SUM(f3[A])', 'FILTER(F3, F3[S] = "a")'],
    "COUNTROWS of the table in another case": ['COUNTROWS(f3)', 'FILTER(F3, F3[S] = "a")'],
    "several columns, one in another case": ['SUM(F3[A])', 'FILTER(F3, F3[s] = "a" && F3[A] > 0)'],
    "an error on the blank row's BLANK": ['COUNTROWS(D3)', 'FILTER(D3, SEARCH("a", D3[N]) > 0)'],
    "MID from the blank row's BLANK": ['COUNTROWS(D3)', 'FILTER(D3, MID("abc", D3[K], 1) = "a")'],
    "several columns, an error on the blank row": ['COUNTROWS(D3)', 'FILTER(D3, SEARCH("a", D3[N]) > D3[K])'],
    "a 1:1 side, an error on BLANK": ['SUM(FC[A])', 'FILTER(FC, SEARCH("a", FC[N]) > 0)'],
    "the text None next to BLANK": ['SUM(TA[A])', 'FILTER(TA, TA[S] = "None")'],
    "the text None next to BLANK, any condition": ['SUM(TA[A])', 'FILTER(TA, NOT ISBLANK(TA[S]))'],
    "ISO and d/m/Y text": ['SUM(TB[A])', 'FILTER(TB, LEN(TB[S]) = 10)'],
    "two offsets of one time": ['SUM(TC[A])', 'FILTER(TC, RIGHT(TC[TS], 6) = "+01:00")'],
    "integers past 2**53": ['SUM(TBig[B])', 'FILTER(TBig, TBig[K] = 9007199254740993)'],
    "integers past 2**53, a combination": ['SUM(TBig[B])',
                                           'FILTER(TBig, TBig[K] = 9007199254740993 && TBig[A] = 1)'],
    "text and numbers in one column": ['SUM(TMix[A])', 'FILTER(TMix, TMix[S] = "5")'],
    "a date and a date-time of one moment": ['SUM(TDt[A])', 'FILTER(TDt, DAY(TDt[D]) = 1)'],
}


# Python 3.10's datetime.fromisoformat does not read the ISO basic format
# ("20200101"), so there the In-set reads it as text and the rewrite is exact
# (the answer, REFUSED_VALUES below, is 1 on every version); the parse is to be
# made the same on every version (#242).
_READS_ISO_BASIC = de._as_moment_str("20200101") is not None


@pytest.mark.parametrize("name", [
    n if n != "ISO basic and extended" else pytest.param(
        n, marks=pytest.mark.skipif(not _READS_ISO_BASIC, reason="this Python does not read ISO basic (#242)"))
    for n in NOT_PUSHED])
def test_no_rewrite_where_the_kept_values_are_not_filters_rows(name):
    assert rewrite(*NOT_PUSHED[name]) is None


# Where a reading meets another cell the rewrite is refused, and the answer
# is FILTER's, which compares the cells themselves: the key 2**53 + 1 is not
# 2**53 (2, alone and in the combination), the text "5" is not the number 5
# (1). 0.9.145 rewrote these and answered 3.
REFUSED_VALUES = {
    'CALCULATE(SUM(TBig[B]), FILTER(TBig, TBig[K] = 9007199254740993))': 2,
    'CALCULATE(SUM(TBig[B]), FILTER(TBig, TBig[K] = 9007199254740993 && TBig[A] = 1))': 2,
    'CALCULATE(SUM(TMix[A]), FILTER(TMix, TMix[S] = "5"))': 1,
}
# ... and where a reading meets none, the rewrite answers as FILTER does
KEPT_VALUES = {
    'CALCULATE(SUM(TN[A]), FILTER(TN, TN[S] = "None"))': 1,
    'CALCULATE(SUM(TN[A]), FILTER(TN, ISBLANK(TN[S])))': None,
    'CALCULATE(SUM(TD[A]), FILTER(TD, LEN(TD[S]) = 10))': 3,
    'CALCULATE(SUM(TD[A]), FILTER(TD, TD[S] = "2020-01-01"))': 1,
    # "20200101" is not "2020-01-01" (LEN 8 keeps 1). On Python 3.11+, where
    # fromisoformat read the basic format as a date, the In-set took one for
    # the other: 0.9.145 rewrote and answered 3, 0.9.146 refused the rewrite
    # there (and the test asking for that failed on 3.10; issue #242)
    'CALCULATE(SUM(TI[A]), FILTER(TI, LEN(TI[S]) = 8))': 1,
}


@pytest.mark.parametrize("expr", list(REFUSED_VALUES) + list(KEPT_VALUES))
def test_readings_an_in_set_gives_answer_as_the_table_filter(expr, monkeypatch):
    want = {**REFUSED_VALUES, **KEPT_VALUES}[expr]
    assert evaluate(expr) == want
    assert evaluate(expr, pushdown=False, monkeypatch=monkeypatch) == want


def test_numeric_text_keeps_its_own_values(monkeypatch):
    # '1' is not selected by '01': Desktop's CALCULATE(SUM(TK[v]), TK[code] =
    # "01") is 10 (build_b210.py k_fact_01), and FILTER compares text as text
    expr = 'CALCULATE(SUM(TK[v]), FILTER(TK, TK[code] = "01"))'
    assert evaluate(expr) == 10
    assert evaluate(expr, pushdown=False, monkeypatch=monkeypatch) == 10


def test_a_column_is_scanned_once(monkeypatch):
    # the pushdown asks on every CALCULATE it rewrites: the column's texts are
    # read once per model, whatever the condition
    seen = []
    real = de._as_moment_str
    monkeypatch.setattr(de, "_as_moment_str", lambda s: seen.append(s) or real(s))
    ctx, engine = context(), de.DAXEngine()
    assert engine._pushdown_table_filter(['SUM(TK[v])', 'FILTER(TK, TK[code] = "01")'], ctx) is not None
    assert sorted(seen) == ["01", "1", "7"]
    assert engine._pushdown_table_filter(['COUNTROWS(TK)', 'FILTER(TK, LEN(TK[code]) = 1)'], ctx) is not None
    assert len(seen) == 3


def test_a_leading_space_still_reads_as_a_date():
    # _as_moment_str reads the stripped text, and so does the In-set
    t = {"T": {"columns": ["S", "A"], "rows": [[" 2020-01-01", 1], ["2020-01-01", 2], ["x", 4]]}}
    ctx = de.DAXContext(t, {}, None, None, None, [])
    assert de.DAXEngine()._pushdown_table_filter(['SUM(T[A])', 'FILTER(T, LEFT(T[S], 1) = " ")'], ctx) is None


# ---- a seeded differential: the rewrite on against off ---------------------
# Star models with orphan and blank keys (the dimension's blank row), text an
# In-set reads another way ("None", ISO basic and extended, d/m/Y, offsets)
# next to text that is not, integers past 2**53, references in another case,
# conditions TRUE, FALSE and an error (SEARCH / MID) for BLANK, inactive
# relationships under USERELATIONSHIP and outer filters. No 1:1: there the
# table filter itself is wrong (issue #227).
ALIASING = ["None", "2020-01-01", "2020-01-01T00:00:00", "1/2/2020", "01/02/2020", "2020-02-01",
            "2020-01-01T10:00:00+01:00", "2020-01-01T10:00:00+02:00", " 2020-01-01", "20200101"]
BIG = [2 ** 53, 2 ** 53 + 1]
PLAIN = ["a", "A", "ab", "xa", "Won", "won", "Lost", "", None, "01", "1", "x y"]


def _model(rng):
    dkeys = list(range(1, rng.randint(2, 4) + 1))
    pool = PLAIN + (ALIASING if rng.random() < 0.4 else [])
    orphan = rng.random() < 0.5
    nums = [0, 1, 2, 5, -1, 2.5, None] + (BIG if rng.random() < 0.2 else [])
    f_rows = [[rng.choice(dkeys + ([9, None] if orphan else [])), rng.choice(dkeys), rng.choice(pool),
               rng.choice(nums)] for _ in range(rng.randint(3, 7))]
    d_rows = [[k, rng.choice(pool), rng.choice(["North", "South", None])] for k in dkeys]
    kind = rng.choice(["m1", "bidi", "inactive"])
    rel = {"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "K", "IsActive": 1}
    if kind == "bidi":
        rel["CrossFilteringBehavior"] = 2
    rels = [rel]
    if kind == "inactive":
        rels.append({"FromTable": "F", "FromColumn": "K2", "ToTable": "D", "ToColumn": "K", "IsActive": 0})
    return ({"F": {"columns": ["K", "K2", "S", "A"], "rows": f_rows},
             "D": {"columns": ["K", "N", "R"], "rows": d_rows}}, rels, kind)


def _condition(rng, t):
    col, kind = rng.choice({"F": [("S", "text"), ("A", "num")], "D": [("N", "text"), ("R", "text"),
                                                                      ("K", "num")]}[t])
    ref = f"{t}[{col.lower() if rng.random() < 0.15 else col}]"
    word = rng.choice(PLAIN[:7] + ALIASING[:3])
    if kind == "num":
        return rng.choice([f"{ref} > {rng.choice([0, 1, 2])}", f"{ref} <> 2", f"ISBLANK({ref})",
                           f'MID("abc", {ref}, 1) = "a"', f"{ref} IN {{1, 5}}", f"{ref} >= 0",
                           f"{ref} = {2 ** 53 + 1}"])
    return rng.choice([f'{ref} = "{word}"', f'{ref} <> "{word}"', f"NOT ISBLANK({ref})", f"LEN({ref}) = 10",
                       f'SEARCH("a", {ref}) > 0', f'SEARCH("a", {ref}, 1, 0) > 0', f'LEFT({ref}, 1) = "2"',
                       f'{ref} IN {{"{word}", "a"}}', f'{ref} = "{word}" && {t}[K] > 1'])


def _measure(rng, t, kind):
    agg = rng.choice(["SUM({t}[{n}])", "COUNTROWS({t})", "MAX({t}[{n}])", "DISTINCTCOUNT({t}[{c}])",
                      "COUNTA({t}[{c}])", "AVERAGEX({t}, {t}[{n}])"])
    agg = agg.format(t=t, n={"F": "A", "D": "K"}[t], c={"F": "S", "D": "N"}[t])
    expr = f"CALCULATE({agg}, FILTER({t}, {_condition(rng, t)}))"
    r = rng.random()
    if r < 0.2:
        expr = f'CALCULATE({expr}, D[R] = "North")'
    elif r < 0.35 and kind == "inactive":
        expr = f"CALCULATE({expr}, USERELATIONSHIP(F[K2], D[K]))"
    return expr


def test_the_rewrite_and_the_table_filter_agree_on_random_models(monkeypatch):
    real = de.DAXEngine._pushdown_table_filter
    fired = []

    def spy(self, args, ctx):
        got = real(self, args, ctx)
        fired.append(got is not None)
        return got

    def run(tbls, rels, expr, fc, push):
        monkeypatch.setattr(de.DAXEngine, "_pushdown_table_filter", spy if push else (lambda s, a, c: None))
        ctx = de.DAXContext({k: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]}
                             for k, v in tbls.items()}, {"M": expr}, None, None, fc, rels)
        try:
            return de.DAXEngine().evaluate_measure("M", ctx)
        except DAXEvaluationError as exc:
            return f"error {exc}"

    differ = []
    n = 0
    for seed in range(60):
        rng = random.Random(seed)
        tbls, rels, kind = _model(rng)
        for _ in range(5):
            t = rng.choice(["F", "D"])
            expr = _measure(rng, t, kind)
            fc = {"F.S": [rng.choice(PLAIN)]} if rng.random() < 0.2 else None
            pushed = run(tbls, rels, expr, fc, True)
            plain = run(tbls, rels, expr, fc, False)
            n += 1
            if pushed != plain:
                differ.append((seed, expr, fc, pushed, plain))
    assert differ == []
    # and the rewrite was exercised, not refused everywhere
    assert sum(fired) >= n // 4
