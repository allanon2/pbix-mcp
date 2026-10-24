"""Issue #226: table and column names are case-insensitive in DAX, and a
reference spelled in another case than the model's was BLANK or ignored.

- The plain aggregates (SUM / MAX / AVERAGE / COUNT / DISTINCTCOUNT ...) read
  the column through an exact lookup of the table name: SUM(f3[A]) was BLANK.
- A reference in a row context matched the row's table exactly, and a row of
  one column (VALUES, DISTINCT, ALL of a column) only under the reference's
  own spelling of the column: FILTER(F3, f3[S] = "a") and
  FILTER(VALUES(F3[S]), F3[s] = "a") kept no row. The #203 rewrite turns
  FILTER(F3, F3[s] = "a") into the second form, so it was BLANK as well.
- A bare table name (COUNTROWS(f3), SUMX(f3, ...), FILTER(f3, ...), ALL(f3),
  VALUES(f3), RELATEDTABLE(f3), CONTAINS(f3, ...)) resolved to no rows.
- A CALCULATE filter was keyed by the name as written, so f3[S] = "a",
  ALL(f3[s]), ALLEXCEPT(f3, d3[R]), DATEADD(cal[date], ...) and
  CROSSFILTER(f3[k], d3[k], ...) filtered, cleared or changed nothing.

Expected values: the first block is Power BI Desktop's own answers over ADOMD
(_desktop_oracle/build_b210.py, model B210, desk.json: the e_* probes). The
other blocks compare a reference spelled in another case with the same
expression in the model's spelling, which is the language rule the Desktop
probes confirm; the values written next to them are the model-spelled
answers, the same on 0.9.145 and now, so a test fails if both spellings
break the same way.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

# build_b210.py's tables D3, F3 (orphan key 9: D3 has a blank row) and TBool
D3_ROWS = [[1, "xa", "North"], [2, "ya", "South"], [3, "za", "North"]]
F3_ROWS = [[1, "a", 10], [2, "a", 20], [1, "b", 5], [9, "c", 7]]
RELS = [{"FromTable": "F3", "FromColumn": "K", "ToTable": "D3", "ToColumn": "K", "IsActive": 1}]


def tables():
    return {"D3": {"columns": ["K", "N", "R"], "rows": [list(r) for r in D3_ROWS]},
            "F3": {"columns": ["K", "S", "A"], "rows": [list(r) for r in F3_ROWS]},
            "TBool": {"columns": ["B", "A"], "rows": [[True, 10], [False, 5]]}}


def evaluate(expr, model_columns=None):
    ctx = de.DAXContext(tables(), {"M": expr}, None, None, None, RELS)
    if model_columns is not None:
        ctx.model_columns = model_columns
    return de.DAXEngine().evaluate_measure("M", ctx)


def _row_by_row(monkeypatch):
    """FILTER and the iterators evaluate their expression for every row (#195
    and the #203 rewrite off), so the row-context reference itself answers."""
    monkeypatch.setattr(de.DAXEngine, "_pushdown_table_filter", lambda self, args, ctx: None)
    monkeypatch.setattr(de.DAXEngine, "_row_only_condition", lambda self, c, t, r: None)
    monkeypatch.setattr(de.DAXEngine, "_row_cell", lambda self, e, r: de._NO_CELL)


# Power BI Desktop over ADOMD, build_b210.py (desk.json); BLANK as None
DESKTOP = {
    'SUM(f3[A])': 42,                                                          # e_sum_case
    'MAX(f3[a])': 20,                                                          # e_max_case
    'CALCULATE(SUM(f3[A]), ALL(F3))': 42,                                      # e_all_case
    'COUNTROWS(FILTER(VALUES(F3[S]), F3[s] = "a"))': 1,                        # e_values_case
    'CALCULATE(SUM(F3[A]), FILTER(F3, f3[S] = "a"))': 30,                      # e_table_case
    'CALCULATE(SUM(F3[A]), FILTER(F3, F3[s] = "a"))': 30,                      # e_col_case
    'CALCULATE(COUNTROWS(F3), FILTER(F3, F3[S] = "a" || F3[s] = "b"))': 3,     # e_col_case_or
    'CALCULATE(SUM(TBool[A]), FILTER(TBool, TBool[b]))': 10,                   # e_bool_case
}


@pytest.mark.parametrize("expr", list(DESKTOP))
def test_desktop_answers(expr):
    assert evaluate(expr) == DESKTOP[expr]


@pytest.mark.parametrize("expr", list(DESKTOP))
def test_desktop_answers_row_by_row(expr, monkeypatch):
    _row_by_row(monkeypatch)
    assert evaluate(expr) == DESKTOP[expr]


# (model's spelling, another case, the model-spelled answer)
SAME_AS_MODEL_SPELLING = [
    # plain aggregates and a bare table name
    ('AVERAGE(F3[A])', 'AVERAGE(f3[a])', 10.5),
    ("MIN(F3[A])", "MIN('f3'[A])", 5),
    ('COUNT(F3[A])', 'COUNT(f3[A])', 4),
    ('COUNTA(F3[S])', 'COUNTA(f3[s])', 4),
    ('DISTINCTCOUNT(F3[S])', 'DISTINCTCOUNT(f3[s])', 3),
    ('DISTINCTCOUNTNOBLANK(F3[S])', 'DISTINCTCOUNTNOBLANK(f3[S])', 3),
    ('COUNTROWS(F3)', 'COUNTROWS(f3)', 4),
    ("COUNTROWS('F3')", "COUNTROWS('f3')", 4),
    ('COUNTROWS(VALUES(F3))', 'COUNTROWS(VALUES(f3))', 4),
    ('SUMX(F3, F3[A])', 'SUMX(f3, f3[A])', 42),
    ('COUNTX(F3, F3[S])', 'COUNTX(f3, f3[s])', 4),
    ('IF(CONTAINS(F3, F3[S], "a"), 1, 0)', 'IF(CONTAINS(f3, f3[s], "a"), 1, 0)', 1),
    # row contexts
    ('SUMX(F3, F3[A] * IF(F3[S] = "a", 1, 0))', 'SUMX(F3, f3[A] * IF(f3[s] = "a", 1, 0))', 30),
    ('COUNTROWS(FILTER(ALL(F3[S]), F3[S] = "a"))', 'COUNTROWS(FILTER(ALL(f3[S]), f3[s] = "a"))', 1),
    ('COUNTROWS(FILTER(F3, RELATED(D3[R]) = "North"))', 'COUNTROWS(FILTER(f3, RELATED(d3[r]) = "North"))', 2),
    ('SUMX(D3, COUNTROWS(RELATEDTABLE(F3)))', 'SUMX(D3, COUNTROWS(RELATEDTABLE(f3)))', 3),
    ('CONCATENATEX(VALUES(F3[S]), F3[S], ",")', 'CONCATENATEX(VALUES(F3[S]), f3[s], ",")', 'a,b,c'),
    ('SUMX(VALUES(D3[R]), COUNTROWS(FILTER(F3, RELATED(D3[R]) = D3[R])))',
     'SUMX(VALUES(D3[R]), COUNTROWS(FILTER(F3, RELATED(D3[R]) = d3[r])))', 4),
    ('COUNTROWS(FIRSTNONBLANK(F3[S], 1))', 'COUNTROWS(FIRSTNONBLANK(f3[s], 1))', 1),
    # CALCULATE's filters, modifiers and table filters
    ('CALCULATE(SUM(F3[A]), F3[S] = "a")', 'CALCULATE(SUM(F3[A]), f3[S] = "a")', 30),
    ('CALCULATE(SUM(F3[A]), F3[S] IN {"a", "b"})', 'CALCULATE(SUM(F3[A]), f3[s] IN {"a", "b"})', 35),
    ('CALCULATE(SUM(F3[A]), NOT F3[S] = "a")', 'CALCULATE(SUM(F3[A]), NOT f3[s] = "a")', 12),
    ('CALCULATE(SUM(F3[A]), D3[R] = "North")', 'CALCULATE(SUM(f3[A]), d3[r] = "North")', 15),
    ('CALCULATE(SUM(F3[A]), KEEPFILTERS(F3[S] = "a"))', 'CALCULATE(SUM(F3[A]), KEEPFILTERS(f3[S] = "a"))', 30),
    # one column's filter in two spellings: the inner one replaces the outer
    # one, and KEEPFILTERS intersects it
    ('CALCULATE(CALCULATE(SUM(F3[A]), F3[S] = "b"), F3[S] = "a")',
     'CALCULATE(CALCULATE(SUM(F3[A]), F3[s] = "b"), F3[S] = "a")', 5),
    ('CALCULATE(CALCULATE(SUM(F3[A]), KEEPFILTERS(F3[S] = "c")), F3[S] IN {"a", "b"})',
     'CALCULATE(CALCULATE(SUM(F3[A]), KEEPFILTERS(f3[s] = "c")), F3[S] IN {"a", "b"})', None),
    ('CALCULATE(CALCULATE(SUM(F3[A]), ALL(F3[S])), F3[S] = "a")',
     'CALCULATE(CALCULATE(SUM(F3[A]), ALL(f3[s])), F3[S] = "a")', 42),
    ('CALCULATE(CALCULATE(SUM(F3[A]), ALLEXCEPT(F3, D3[R])), F3[S] = "a", D3[R] = "North")',
     'CALCULATE(CALCULATE(SUM(F3[A]), ALLEXCEPT(f3, d3[R])), F3[S] = "a", D3[R] = "North")', 15),
    ('CALCULATE(SUM(F3[A]), FILTER(ALL(F3), F3[A] > 6))', 'CALCULATE(SUM(F3[A]), FILTER(ALL(f3), f3[A] > 6))', 37),
    ('CALCULATE(SUM(F3[A]), FILTER(D3, D3[R] = "North"))', 'CALCULATE(SUM(F3[A]), FILTER(d3, d3[r] = "North"))', 15),
    ('CALCULATE(SUM(F3[A]), FILTER(ALLSELECTED(F3[S]), F3[S] = "a"))',
     'CALCULATE(SUM(F3[A]), FILTER(ALLSELECTED(f3[s]), F3[S] = "a"))', 30),
    ('COUNTROWS(CALCULATETABLE(F3, F3[S] = "a"))', 'COUNTROWS(CALCULATETABLE(f3, f3[S] = "a"))', 2),
    ('CALCULATE(COUNTROWS(D3), CROSSFILTER(F3[K], D3[K], BOTH), F3[S] = "a")',
     'CALCULATE(COUNTROWS(D3), CROSSFILTER(f3[k], d3[k], BOTH), F3[S] = "a")', 2),
    ('CALCULATE(SUM(TBool[A]), FILTER(TBool, TBool[B]))', 'CALCULATE(SUM(TBool[A]), FILTER(tbool, tbool[B]))', 10),
]


@pytest.mark.parametrize("model,other,want", SAME_AS_MODEL_SPELLING, ids=[c[1] for c in SAME_AS_MODEL_SPELLING])
def test_another_case_reads_the_models_names(model, other, want):
    assert evaluate(model) == want
    assert evaluate(other) == want


ROW_CASES = [c for c in SAME_AS_MODEL_SPELLING if 'FILTER(' in c[1] or 'X(' in c[1]]


@pytest.mark.parametrize("model,other,want", ROW_CASES, ids=[c[1] for c in ROW_CASES])
def test_another_case_reads_the_models_names_row_by_row(model, other, want, monkeypatch):
    _row_by_row(monkeypatch)
    assert evaluate(other) == want


@pytest.mark.parametrize("expr", [
    'CALCULATE(SUM(F3[A]), f3[Nope] > 1)',
    'SUMX(F3, f3[Nope]) + 1',
    'COUNTROWS(FILTER(F3, f3[nope] = 1)) + 1',
    'SUM(f3[Nope]) + 1',
])
def test_a_column_the_table_lacks_is_refused_in_any_case(expr):
    """f3 is F3, so a column F3 does not have is refused as F3[Nope] is
    (Desktop: "Column 'Nope' in table 'F3' cannot be found"), which surfaces
    as BLANK. Unresolved, the filter was dropped (42) and the reference read
    as BLANK (1)."""
    assert evaluate(expr, model_columns={t: v["columns"] for t, v in tables().items()}) is None


def test_the_models_spelling_never_looks_the_name_up_again(monkeypatch):
    """The case-insensitive lookup runs only once a reference has failed in
    the model's spelling: a 300-row FILTER evaluated row by row never calls
    model_table for F3[A] / F3[S]."""
    _row_by_row(monkeypatch)
    calls = []
    orig = de.DAXContext.model_table
    monkeypatch.setattr(de.DAXContext, "model_table", lambda self, name: calls.append(name) or orig(self, name))
    rows = [[i % 7, "abc"[i % 3], i] for i in range(300)]
    ctx = de.DAXContext({"F3": {"columns": ["K", "S", "A"], "rows": rows}},
                        {"M": 'COUNTROWS(FILTER(F3, F3[A] > 100 && F3[S] = "a"))'}, None, None, None, [])
    assert de.DAXEngine().evaluate_measure("M", ctx) == 66
    assert calls == []


# a marked date table: time intelligence over a column named in another case
DAYS = [datetime(2020, 1, 1), datetime(2020, 1, 15), datetime(2020, 2, 1), datetime(2020, 3, 1),
        datetime(2021, 1, 1), datetime(2021, 2, 1), datetime(2021, 3, 1)]
TI_TABLES = {
    "Cal": {"columns": ["Date", "Year", "Month"], "rows": [[d, d.year, d.month] for d in DAYS]},
    "Sales": {"columns": ["Date", "Amt"], "rows": [[d, 10 * (i + 1)] for i, d in enumerate(DAYS)]},
}
TI_RELS = [{"FromTable": "Sales", "FromColumn": "Date", "ToTable": "Cal", "ToColumn": "Date", "IsActive": 1}]
TI_CASES = [
    ('CALCULATE(SUM(Sales[Amt]), DATESYTD(Cal[Date]))', 'CALCULATE(SUM(sales[amt]), DATESYTD(cal[date]))', 110),
    ('TOTALYTD(SUM(Sales[Amt]), Cal[Date])', 'TOTALYTD(SUM(sales[Amt]), cal[date])', 110),
    ('CALCULATE(SUM(Sales[Amt]), SAMEPERIODLASTYEAR(Cal[Date]))',
     'CALCULATE(SUM(Sales[Amt]), SAMEPERIODLASTYEAR(cal[date]))', 30),
    ('CALCULATE(SUM(Sales[Amt]), DATEADD(Cal[Date], -1, YEAR))',
     'CALCULATE(SUM(Sales[Amt]), DATEADD(cal[Date], -1, YEAR))', 30),
    ('CALCULATE(SUM(Sales[Amt]), Cal[Month] IN {1, 2})', 'CALCULATE(SUM(Sales[Amt]), cal[month] IN {1, 2})', 110),
]


@pytest.mark.parametrize("model,other,want", TI_CASES, ids=[c[1] for c in TI_CASES])
def test_time_intelligence_in_another_case(model, other, want):
    def ev(expr):
        tb = {k: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]} for k, v in TI_TABLES.items()}
        ctx = de.DAXContext(tb, {"M": expr}, "Cal", "Date", {"Cal.Year": [2021], "Cal.Month": [2]}, TI_RELS)
        return de.DAXEngine().evaluate_measure("M", ctx)
    assert ev(model) == want
    assert ev(other) == want
