"""CALCULATE(<aggregate of T>, FILTER(T, <condition on one column of T>)) is
evaluated as a column filter, KEEPFILTERS(FILTER(VALUES(T[c]), <condition>)):
the same rows of T, taken from the column's index instead of one by one.

Every case here is evaluated both ways, with the rewrite and without it (the
whole-table FILTER path, whose answers the Desktop-checked suites pin), under
several filter contexts: the values must be the same. The rewrite must not be
made where the two differ (a row context, another filter argument, an
expression that reads more than T's rows).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "D": {"columns": ["Key", "Name"], "rows": [[1, "a"], [2, "b"], [3, None]]},
    "F": {"columns": ["Key", "Status", "Value", "Region"],
          "rows": [[1, "Won", 10, "N"], [1, "Lost", 20, "S"], [2, "Won", 30, "N"],
                   [2, None, 40, "S"], [3, "Won", 50, "N"], [9, "Lost", 60, "S"],   # key 9: D's blank row
                   [1, "won", 70, "S"]]},                                               # text compares case-insensitively
}
RELS = [{"FromTable": "F", "FromColumn": "Key", "ToTable": "D", "ToColumn": "Key", "IsActive": 1}]

PUSHED = {
    "sumx": 'CALCULATE(SUMX(F, F[Value]), FILTER(F, F[Status] = "Won"))',
    "sum_in": 'CALCULATE(SUM(F[Value]), FILTER(F, F[Status] IN {"Won", "Lost"}))',
    "count_not_blank": "CALCULATE(COUNTROWS(F), FILTER(F, NOT ISBLANK(F[Status])))",
    "count_blank": "CALCULATE(COUNTROWS(F), FILTER(F, ISBLANK(F[Status])))",
    "avg_quoted": "CALCULATE(AVERAGE('F'[Value]), FILTER('F', 'F'[Value] > 15))",
    "distinct": 'CALCULATE(DISTINCTCOUNT(F[Key]), FILTER(F, F[Status] <> "Won"))',
    "empty": 'CALCULATE(MAX(F[Value]), FILTER(F, F[Status] = "Nope"))',
    "dim_blank_row": "CALCULATE(COUNTROWS(D), FILTER(D, ISBLANK(D[Name])))",
    "dim_name": 'CALCULATE(COUNTROWS(D), FILTER(D, D[Name] = "a"))',
    "minx": 'CALCULATE(MINX(F, F[Value]), FILTER(F, F[Region] = "S"))',
    "two_columns_and": 'CALCULATE(SUM(F[Value]), FILTER(F, F[Status] = "Won" && F[Region] = "N"))',
    "two_columns_or": 'CALCULATE(COUNTROWS(F), FILTER(F, F[Status] = "Lost" || F[Region] = "N"))',
    "two_columns_not": 'CALCULATE(SUM(F[Value]), FILTER(F, NOT (F[Status] = "Won" && F[Region] = "S")))',
    "three_columns": 'CALCULATE(SUMX(F, F[Value]), FILTER(F, F[Key] IN {1, 2} && (ISBLANK(F[Status]) || F[Region] = "N")))',
    "column_vs_column": "CALCULATE(COUNTROWS(F), FILTER(F, F[Key] * 10 < F[Value]))",
    "text_functions": 'CALCULATE(SUM(F[Value]), FILTER(F, UPPER(LEFT(F[Status], 1)) = "W" && LEN(F[Region]) = 1))',
    "number_functions": "CALCULATE(COUNTROWS(F), FILTER(F, MOD(INT(F[Value] / 10), 2) = 1 || ROUND(F[Key] / 3, 0) = 1))",
    "if_and_search": 'CALCULATE(SUM(F[Value]), FILTER(F, IF(ISBLANK(F[Status]), FALSE, SEARCH("o", F[Status], 1, 0) > 0)))',
}
NOT_PUSHED = {
    "row_context": 'SUMX(D, CALCULATE(SUM(F[Value]), FILTER(F, F[Status] = "Won")))',
    "row_context_same_table": 'SUMX(F, CALCULATE(SUM(F[Value]), FILTER(F, F[Status] = "Won")))',
    "second_argument": 'CALCULATE(SUM(F[Value]), FILTER(F, F[Status] = "Won"), D[Name] = "a")',
    "modifier": 'CALCULATE(SUM(F[Value]), FILTER(F, F[Status] = "Won"), ALL(F[Region]))',
    "other_table_aggregate": 'CALCULATE(COUNTROWS(D), FILTER(F, F[Status] = "Won"))',
    "measure_expression": 'CALCULATE([Total], FILTER(F, F[Status] = "Won"))',
    "filter_over_all": 'CALCULATE(SUM(F[Value]), FILTER(ALL(F), F[Status] = "Won"))',
    "condition_with_measure": 'CALCULATE(SUM(F[Value]), FILTER(F, F[Value] > [Total] / 10))',
    "condition_reads_context": 'CALCULATE(SUM(F[Value]), FILTER(F, F[Status] = SELECTEDVALUE(F[Status])))',
    "condition_reads_time": "CALCULATE(SUM(F[Value]), FILTER(F, F[Key] < YEAR(TODAY())))",
    # case-sensitive: the values kept are applied as a filter, which ignores case ("won" would come back)
    "case_sensitive": 'CALCULATE(SUM(F[Value]), FILTER(F, EXACT(F[Status], "Won")))',
    "case_sensitive_find": 'CALCULATE(SUM(F[Value]), FILTER(F, FIND("W", F[Status], 1, 0) = 1))',
    "table_in_another_case": 'CALCULATE(SUM(F[Value]), FILTER(f, F[Status] = "Won"))',
    "quoted_in_another_case": "CALCULATE(SUM(F[Value]), FILTER('f', F[Status] = \"Won\"))",
    "measure_as_a_column": 'CALCULATE(SUMX(F, F[Total]), FILTER(F, F[Status] = "Won"))',
    "unknown_column": 'CALCULATE(SUM(F[Nope]), FILTER(F, F[Status] = "Won"))',
}
CONTEXTS = [
    {},
    {"F.Status": ["Lost"]},
    {"F.Status": ["Won", "Lost"]},
    {"D.Name": ["a"]},
    {"D.Name": ["b"], "F.Region": ["N"]},
    {"F.Region": ["S"]},
    {"F.Key": [9]},
]


def evaluate(expr: str, filters: dict, pushdown: bool, monkeypatch) -> object:
    with monkeypatch.context() as m:
        if not pushdown:
            m.setattr(de.DAXEngine, "_pushdown_table_filter", lambda self, args, ctx: None)
        tables = {k: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]} for k, v in TABLES.items()}
        ctx = de.DAXContext(tables, {"M": expr, "Total": "SUM(F[Value])"}, None, None, dict(filters), RELS)
        return de.DAXEngine().evaluate_measure("M", ctx)


@pytest.mark.parametrize("name", sorted(PUSHED))
def test_pushed_down_values_are_the_whole_table_filters(name, monkeypatch):
    seen = []
    real = de.DAXEngine._pushdown_table_filter

    def spy(self, args, ctx):
        out = real(self, args, ctx)
        seen.append(out is not None)
        return out
    for filters in CONTEXTS:
        slow = evaluate(PUSHED[name], filters, False, monkeypatch)
        with monkeypatch.context() as m:
            m.setattr(de.DAXEngine, "_pushdown_table_filter", spy)
            fast = evaluate(PUSHED[name], filters, True, monkeypatch)
        assert fast == slow, (name, filters)
    assert seen and all(seen), "the rewrite was made"


@pytest.mark.parametrize("name", sorted(NOT_PUSHED))
def test_no_rewrite_where_the_two_could_differ(name, monkeypatch):
    seen = []
    real = de.DAXEngine._pushdown_table_filter

    def spy(self, args, ctx):
        out = real(self, args, ctx)
        seen.append(out)
        return out
    for filters in CONTEXTS:
        slow = evaluate(NOT_PUSHED[name], filters, False, monkeypatch)
        with monkeypatch.context() as m:
            m.setattr(de.DAXEngine, "_pushdown_table_filter", spy)
            same = evaluate(NOT_PUSHED[name], filters, True, monkeypatch)
        assert same == slow, (name, filters)
    assert seen and not any(seen), f"no rewrite: {[s for s in seen if s]}"


def test_a_condition_that_fails_on_a_hidden_value(monkeypatch):
    # "abc" * 1 fails; the whole-table FILTER never sees it when Code is filtered to the numbers, so neither may the
    # pushed-down filter (over ALL(F[Code]) it did, and the measure went BLANK)
    tables = {"F": {"columns": ["Code", "V", "R"], "rows": [["12", 1, "N"], ["abc", 2, "S"], ["30", 4, "N"]]}}
    for expr, filters in [(e, f) for e in ("CALCULATE(SUM(F[V]), FILTER(F, F[Code] * 1 > 5))",
                                           'CALCULATE(SUM(F[V]), FILTER(F, F[Code] * 1 > 5 && F[R] = "N"))',
                                           "CALCULATE(SUM(F[V]), FILTER(F, VALUE(F[Code]) > 5))",
                                           'CALCULATE(SUM(F[V]), FILTER(F, VALUE(LEFT(F[Code], 1)) >= 1 && F[R] = "N"))')
                          for f in ({"F.Code": ["12", "30"]}, {"F.R": ["N"]}, {"F.Code": ["30"]}, {})]:
        got = []
        for pushdown in (False, True):
            with monkeypatch.context() as m:
                if not pushdown:
                    m.setattr(de.DAXEngine, "_pushdown_table_filter", lambda self, args, ctx: None)
                ctx = de.DAXContext({k: dict(v) for k, v in tables.items()}, {"M": expr}, None, None, dict(filters), [])
                got.append(de.DAXEngine().evaluate_measure("M", ctx))
        assert got[0] == got[1], (expr, filters, got)
    for expr in ("CALCULATE(SUM(F[V]), FILTER(F, F[Code] * 1 > 5))", 'CALCULATE(SUM(F[V]), FILTER(F, F[Code] * 1 > 5 && F[R] = "N"))'):
        ctx = de.DAXContext(tables, {"M": expr}, None, None, {"F.R": ["N"]}, [])
        assert de.DAXEngine().evaluate_measure("M", ctx) == 5


def test_known_values():
    # by hand: Won (case-insensitive) is 10 + 30 + 50 + 70; Won under Region S is the "won" row's 70
    def ev(expr, filters):
        ctx = de.DAXContext(TABLES, {"M": expr}, None, None, dict(filters), RELS)
        return de.DAXEngine().evaluate_measure("M", ctx)
    assert ev(PUSHED["sumx"], {}) == 160
    assert ev(PUSHED["sumx"], {"F.Region": ["S"]}) == 70
    assert ev(PUSHED["sumx"], {"F.Status": ["Lost"]}) is None
    assert ev(PUSHED["count_blank"], {}) == 1
    assert ev(PUSHED["empty"], {}) is None
