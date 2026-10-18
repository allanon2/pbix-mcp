"""Issue #195: FILTER(table, <condition on the row's columns>) and an iterator
over a plain column no longer build a row context per row.

The Artificial Intelligence Sample's [Count of Won],
COUNTAX(FILTER(KEEPFILTERS(Opportunities), Opportunities[Status] = "Won"),
Opportunities[OpportunitySeq]), took 2.1-2.7 s over 10 products: FILTER built
a context with its transition for each of 20,000 rows to compare one column
with a constant, and COUNTAX did the same to read one column per row.

- A FILTER condition that reads only columns of the iterated row and
  constants (comparisons, IN { }, AND / OR / NOT, ISBLANK, arithmetic) has
  one value per combination of those columns: it is evaluated once per
  combination, the usual way, and reused.
- SUMX / COUNTX / COUNTAX / AVERAGEX / MAXX over an expression that is just
  a column of the iterated table read the row's cell.

Answers must not move: every case below is compared with the row-by-row
path forced on.
"""
from __future__ import annotations

import datetime as dt

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

STATUS = ["Won", "Lost", "won", None, "Open"]
ROWS = [[i, STATUS[i % 5], float(i % 7) if i % 11 else None, dt.datetime(2024, 1, 1) + dt.timedelta(days=i % 40),
         "P%d" % (i % 3)] for i in range(600)]
TABLES = {
    "Opp": {"columns": ["Seq", "Status", "Value", "Day", "Product"], "rows": ROWS},
    "Prod": {"columns": ["Product", "Line"], "rows": [["P0", "A"], ["P1", "A"], ["P2", "B"]]},
}
RELS = [{"FromTable": "Opp", "FromColumn": "Product", "ToTable": "Prod", "ToColumn": "Product", "IsActive": 1}]
CONDS = [
    'Opp[Status] = "Won"',                         # text compares case-insensitively: "won" too
    'Opp[Status] <> "Won"',
    'ISBLANK(Opp[Status])',
    'NOT ISBLANK(Opp[Value]) && Opp[Value] >= 3',
    'Opp[Value] > 2 || Opp[Status] = "Open"',
    'Opp[Status] IN {"Lost", "Open"}',
    'Opp[Day] < DATE(2024, 1, 10)',
    "'Opp'[Value] * 2 = 6",
    'Opp[Value] = BLANK()',
]
MEASURES = {}
for i, c in enumerate(CONDS):
    MEASURES[f"cnt{i}"] = f"COUNTAX(FILTER(KEEPFILTERS(Opp), {c}), Opp[Seq])"
    MEASURES[f"sum{i}"] = f"SUMX(FILTER(Opp, {c}), Opp[Value])"
    MEASURES[f"calc{i}"] = f"CALCULATE(SUMX(Opp, Opp[Value]), FILTER(Opp, {c}))"
MEASURES.update({
    "avg": "AVERAGEX(Opp, Opp[Value])",
    "max": "MAXX(FILTER(Opp, Opp[Status] = \"Lost\"), Opp[Day])",
    "countx": "COUNTX(Opp, Opp[Value])",
    "rowsMeasure": "COUNTROWS(FILTER(Opp, Opp[Value] > [avg]))",      # not row-only: the old path
})
FILTERS = [{}, {"Prod.Line": ["A"]}, {"Opp.Status": ["Won", "Lost"]}]


def _batch(fc):
    return de.evaluate_measures_batch(list(MEASURES), TABLES, dict(MEASURES), dict(fc), relationships=RELS)


@pytest.mark.parametrize("fc", FILTERS, ids=["none", "line A", "status"])
def test_same_as_row_by_row(fc, monkeypatch):
    fast = _batch(fc)
    monkeypatch.setattr(de.DAXEngine, "_row_only_condition", lambda self, c, t, r: None)
    monkeypatch.setattr(de.DAXEngine, "_row_cell", lambda self, e, r: de._NO_CELL)
    slow = _batch(fc)
    assert fast == slow


def test_status_won_counts_case_insensitively():
    got = de.evaluate_measures_batch(["cnt0"], TABLES, dict(MEASURES), {}, relationships=RELS)
    assert got["cnt0"] == 240        # "Won" and "won": 2 of every 5 rows


def test_condition_evaluated_once_per_combination(monkeypatch):
    calls = {"n": 0}
    real = de.DAXEngine._filter_whole_row

    def counting(self, *a, **kw):
        calls["n"] += 1
        return real(self, *a, **kw)

    monkeypatch.setattr(de.DAXEngine, "_filter_whole_row", counting)
    de.evaluate_measures_batch(["cnt0"], TABLES, dict(MEASURES), {}, relationships=RELS)
    assert calls["n"] == len(set(STATUS))          # one per status, not one per row


def test_plain_column_read_without_row_context(monkeypatch):
    calls = {"n": 0}
    real = de.DAXEngine._make_row_context

    def counting(self, *a, **kw):
        calls["n"] += 1
        return real(self, *a, **kw)

    monkeypatch.setattr(de.DAXEngine, "_make_row_context", counting)
    got = de.evaluate_measures_batch(["countx", "avg"], TABLES, dict(MEASURES), {}, relationships=RELS)
    assert got["countx"] == sum(1 for r in ROWS if r[2] is not None)
    assert calls["n"] == 0


@pytest.mark.parametrize("cond, cols", [
    ('Opp[Status] = "Won"', ["Status"]),
    ("NOT ISBLANK(Opp[Status]) && 'Opp'[Value] > 5", ["Status", "Value"]),
    ('[Status] = "Won"', None),                      # unqualified: could be a measure
    ('Opp[Status] = [M]', None),
    ('SUM(Opp[Value]) > 1', None),
    ('Prod[Line] = "A"', None),
    ('RELATED(Prod[Line]) = "A"', None),
])
def test_row_only_condition(cond, cols):
    row = {"__table__": "Opp", "__row__": True, "Seq": 1, "Status": "Won", "Value": 1.0}
    assert de._engine._row_only_condition(cond, "Opp", row) == cols
