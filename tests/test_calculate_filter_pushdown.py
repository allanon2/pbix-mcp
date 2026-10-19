"""CALCULATE(<aggregate of T>, FILTER(T, <condition on T's columns>)) is
evaluated as the column filter KEEPFILTERS(FILTER(VALUES(T[c]), <condition>))
where the two keep the same rows of T and nothing else is read (issue #203;
PR #200, by @allanon2): the Artificial Intelligence Sample's [Revenue Won]
applied a 20,000-row table filter row by row (2.0 s for three products on
0.9.143, 0.08 s now).

Desktop's answers are from _desktop_oracle/build_b200.py (756 probes, all
matched with the rewrite and without it): text differing in case only, blank
text and numbers, an orphan and a blank key giving the dimension its blank
row, aggregates of the fact and of the dimension, under outer filters on the
dimension, on the filtered column itself and on another fact column.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

D_ROWS = [[1, "Alpha", "North"], [2, "beta", "South"], [3, "Gamma", "North"], [4, None, "South"]]
# as the model stores them: one spelling of a text that differs in case only
# (Desktop keeps the first, "Won", for "won" and "WON")
F_ROWS = [[1, "Won", 100, 1, 3], [1, "Won", 50, 2, 4], [2, "Lost", 70, 3, 5], [2, "Won", 30, None, 6],
          [3, None, 20, 4, 7], [3, "Open", None, 5, 8], [9, "Won", 11, 6, 9], [4, "Won", 7, 7, 10],
          [None, "Lost", 5, 8, 11]]
RELS = [{"FromTable": "F", "FromColumn": "Key", "ToTable": "D", "ToColumn": "Key", "IsActive": 1}]


def tables():
    return {"D": {"columns": ["Key", "Name", "Region"], "rows": [list(r) for r in D_ROWS]},
            "F": {"columns": ["Key", "Status", "Amt", "Qty", "Day"], "rows": [list(r) for r in F_ROWS]}}


def evaluate(expr, filters=None, pushdown=True, monkeypatch=None):
    if not pushdown:
        monkeypatch.setattr(de.DAXEngine, "_pushdown_table_filter", lambda self, args, ctx: None)
    ctx = de.DAXContext(tables(), {"M": expr, "Total": "SUM(F[Amt])"}, None, None, filters, RELS)
    return de.DAXEngine().evaluate_measure("M", ctx)


# Desktop's answers (build_b200.py); BLANK as None
DESKTOP = {
    'CALCULATE(SUM(F[Amt]), FILTER(F, F[Status] = "Won"))': 198,
    'CALCULATE(SUM(F[Amt]), FILTER(F, F[Status] = "won"))': 198,
    'CALCULATE(CALCULATE(SUM(F[Amt]), FILTER(F, F[Status] = "Won")), D[Region] = "North")': 150,
    'CALCULATE(CALCULATE(SUM(F[Amt]), FILTER(F, F[Status] = "Won")), F[Status] = "won")': 198,
    'CALCULATE(CALCULATE(SUM(F[Amt]), FILTER(F, F[Status] = "Won")), D[Key] = BLANK())': 11,
    'CALCULATE(COUNTROWS(F), FILTER(F, ISBLANK(F[Status])))': 1,
    'CALCULATE(COUNTROWS(F), FILTER(F, F[Status] <> "Won"))': 4,
    'CALCULATE(CALCULATE(COUNTROWS(F), FILTER(F, F[Status] <> "Won")), F[Status] IN {"Lost", "Open"})': 3,
    'CALCULATE(CALCULATE(AVERAGE(F[Amt]), FILTER(F, F[Amt] > 25)), F[Day] <= 6)': 62.5,
    'CALCULATE(MIN(F[Qty]), FILTER(F, F[Amt] + F[Qty] > 40))': 1,
    'CALCULATE(COUNT(F[Amt]), FILTER(F, F[Amt] = 0))': None,
    'CALCULATE(COUNTA(F[Status]), FILTER(F, F[Status] = BLANK()))': None,
    'CALCULATE(DISTINCTCOUNT(F[Status]), FILTER(F, LEFT(F[Status], 1) = "W"))': 1,
    'CALCULATE(CALCULATE(DISTINCTCOUNT(F[Status]), FILTER(F, UPPER(F[Status]) = "WON")), D[Name] = "beta")': 1,
    'CALCULATE(CALCULATE(SUMX(F, F[Qty]), FILTER(F, F[Status] IN {"Won", "Lost"})), D[Key] = BLANK())': 14,
    'CALCULATE(CALCULATE(COUNTROWS(F), FILTER(F, F[Status] = BLANK())), D[Key] = BLANK())': None,
    'CALCULATE(COUNTROWS(D), FILTER(D, ISBLANK(D[Name])))': 1,
    'CALCULATE(COUNTROWS(D), FILTER(D, D[Name] <> "Alpha"))': 3,
    'CALCULATE(CALCULATE(COUNTROWS(D), FILTER(D, D[Name] <> "Alpha")), D[Key] = BLANK())': None,
    'CALCULATE(CALCULATE(COUNTROWS(D), FILTER(D, D[Key] > 1)), F[Status] = "won")': 3,
    'CALCULATE(DISTINCTCOUNT(D[Name]), FILTER(D, D[Name] <> "Alpha"))': 3,
    'CALCULATE(COUNTA(D[Name]), FILTER(D, ISBLANK(D[Name])))': None,
    'CALCULATE(CALCULATE(COUNTROWS(D), FILTER(D, D[Region] = "North")), F[Day] <= 6)': 2,
}


@pytest.mark.parametrize("expr", list(DESKTOP))
def test_desktop_answers_with_the_rewrite_and_without_it(expr, monkeypatch):
    assert evaluate(expr) == DESKTOP[expr]
    assert evaluate(expr, pushdown=False, monkeypatch=monkeypatch) == DESKTOP[expr]


PUSHED = [
    ('SUM(F[Amt])', 'FILTER(F, F[Status] = "Won")', 'KEEPFILTERS(FILTER(VALUES(\'F\'[Status]), F[Status] = "Won"))'),
    ('COUNTROWS(F)', "FILTER('F', F[Amt] > 25 && F[Qty] < 5)",
     "KEEPFILTERS(FILTER(SUMMARIZE('F', 'F'[Amt], 'F'[Qty]), F[Amt] > 25 && F[Qty] < 5))"),
    ('SUMX(F, F[Qty])', 'FILTER(F, LEFT(F[Status], 1) = "W")', None),
    ('DISTINCTCOUNT(F[Status])', 'FILTER (F, NOT(ISBLANK(F[Amt])))', None),
    ('COUNTROWS(D)', 'FILTER(D, D[Region] = "North")', None),
]


@pytest.mark.parametrize("agg,flt,want", PUSHED)
def test_the_rewrite(agg, flt, want):
    ctx = de.DAXContext(tables(), {}, None, None, None, RELS)
    got = de.DAXEngine()._pushdown_table_filter([agg, flt], ctx)
    assert got is not None and got[0] == agg
    if want is not None:
        assert got[1] == want


NOT_PUSHED = {
    "two filter arguments": ['SUM(F[Amt])', 'FILTER(F, F[Amt] > 25)', 'D[Region] = "North"'],
    "a measure": ['[Total]', 'FILTER(F, F[Amt] > 25)'],
    "an expression over the aggregate": ['SUM(F[Amt]) + 0', 'FILTER(F, F[Amt] > 25)'],
    "another table's aggregate": ['COUNTROWS(D)', 'FILTER(F, F[Amt] > 25)'],
    "another table's column": ['SUM(F[Amt])', 'FILTER(F, RELATED(D[Region]) = "North")'],
    "a measure in the condition": ['SUM(F[Amt])', 'FILTER(F, [Total] > 25)'],
    "a case-sensitive function": ['SUM(F[Amt])', 'FILTER(F, EXACT(F[Status], "Won"))'],
    "FIND is case-sensitive": ['SUM(F[Amt])', 'FILTER(F, FIND("W", F[Status], 1, 0) = 1)'],
    "a filter-reading function": ['SUM(F[Amt])', 'FILTER(F, F[Amt] > CALCULATE(SUM(F[Amt])) / 4)'],
    "not the table itself": ['SUM(F[Amt])', 'FILTER(ALL(F), F[Amt] > 25)'],
    "a table spelled otherwise": ['SUM(f[Amt])', 'FILTER(f, f[Amt] > 25)'],
    "an unknown column": ['SUM(F[Amt])', 'FILTER(F, F[Nope] > 25)'],
    "not FILTER": ['SUM(F[Amt])', 'KEEPFILTERS(FILTER(F, F[Amt] > 25))'],
    "a constant condition": ['SUM(F[Amt])', 'FILTER(F, TRUE())'],
}


@pytest.mark.parametrize("name", list(NOT_PUSHED))
def test_no_rewrite_where_the_two_could_differ(name):
    ctx = de.DAXContext(tables(), {"Total": "SUM(F[Amt])"}, None, None, None, RELS)
    assert de.DAXEngine()._pushdown_table_filter(NOT_PUSHED[name], ctx) is None


# Desktop: in a row context the table filter replaces the row's transition on
# F's expanded table (every D row sees all of F's "Won" rows), where the column
# filter would keep it (r5: 187).
ROW_CONTEXT = {
    'SUMX(D, CALCULATE(SUM(F[Amt]), FILTER(F, F[Status] = "Won")))': 792,
    'SUMX(VALUES(D[Region]), CALCULATE(COUNTROWS(F), FILTER(F, F[Amt] > 25)))': 12,
    'SUMX(F, CALCULATE(SUM(F[Amt]), FILTER(F, F[Amt] > 25)))': 2250,
    'SUMX(D, CALCULATE(SUM(F[Amt]), KEEPFILTERS(FILTER(VALUES(F[Status]), F[Status] = "Won"))))': 187,
    'SUMX(D, CALCULATE(COUNTROWS(D), FILTER(D, D[Key] > 1)))': 12,
    'SUMX(VALUES(D[Region]), CALCULATE(SUM(F[Amt]), FILTER(F, F[Status] = "Won")))': 594,
}


@pytest.mark.parametrize("expr", list(ROW_CONTEXT))
def test_no_rewrite_in_a_row_context(expr, monkeypatch):
    seen = []
    real = de.DAXEngine._pushdown_table_filter

    def spy(self, args, ctx):
        got = real(self, args, ctx)
        seen.append(got is not None)
        return got
    monkeypatch.setattr(de.DAXEngine, "_pushdown_table_filter", spy)
    assert evaluate(expr) == ROW_CONTEXT[expr]
    assert not any(seen)


CONTEXTS = [None, {"D.Region": ["North"]}, {"F.Status": ["won"]}, {"F.Status": ["Lost", "Open"]},
            {"F.Day": [3, 4, 5, 6]}, {"D.Key": [9]}]
SHAPES = ['CALCULATE(SUM(F[Amt]), FILTER(F, F[Status] = "won"))',
          'CALCULATE(COUNTROWS(F), FILTER(F, F[Status] <> "Won"))',
          'CALCULATE(AVERAGE(F[Amt]), FILTER(F, F[Amt] + F[Qty] > 40))',
          'CALCULATE(MAXX(F, F[Qty]), FILTER(F, F[Amt] >= 0 || ISBLANK(F[Amt])))',
          'CALCULATE(COUNTA(F[Status]), FILTER(F, LEN(F[Status]) = 3))',
          'CALCULATE(COUNTROWS(D), FILTER(D, NOT(D[Key] = 2)))']


@pytest.mark.parametrize("expr", SHAPES)
def test_the_rewrite_and_the_table_filter_agree_in_each_context(expr, monkeypatch):
    pushed = [evaluate(expr, f) for f in CONTEXTS]
    monkeypatch.setattr(de.DAXEngine, "_pushdown_table_filter", lambda self, args, ctx: None)
    assert pushed == [evaluate(expr, f) for f in CONTEXTS]
