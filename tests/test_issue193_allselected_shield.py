"""Issue #193 (OpenBI's doc 66): a measure that reaches a table only through
CALCULATE(..., FILTER(ALLSELECTED(T), ...)) -- or ALL(T) / ALLSELECTED(T) as
the filter -- has one value whatever T's filters are, so the memo keys it on
the query's selection and the iteration's rows instead of each row's or
group's own filter.

Awesome Chocolates' trend line, [Selection Sales Calculation] grouped by
week: [Selection min date] / [Selection max date] filter ALLSELECTED('dim-Date')
by [Max Week Not Blank], which scans ALLSELECTED('dim-Date') again; they were
evaluated for each of the 106 weeks (15-26 s per measure, 1 s now). The
memo shield proves it: the CALCULATE's whole-row filter replaces every filter
on the table, and SELECTEDVALUE of a calculation group (or of a table the
date filters never reach) reads none of them.

Expected values: Power BI Desktop 2.152 over ADOMD (the Desktop oracle
toolkit's build_b193.py: the same shape over 13 weeks, per week, under
explicit Date filters ALLSELECTED keeps, and with a disconnected selector)."""
from __future__ import annotations

import datetime as dt

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

DAYS = [dt.datetime(2024, 1, 1) + dt.timedelta(days=i) for i in range(91)]
SALE_DAYS = [d for d in DAYS if d.weekday() in (1, 3) and not (dt.datetime(2024, 2, 5) <= d < dt.datetime(2024, 2, 19))
             and d < dt.datetime(2024, 3, 20)]
TABLES = {
    "Date": {"columns": ["Date", "Month", "Week", "WeekStart"],
             "rows": [[d, d.month, (d - DAYS[0]).days // 7 + 1, d - dt.timedelta(days=d.weekday())] for d in DAYS]},
    "Sales": {"columns": ["SDate", "Amount"], "rows": [[d, d.day] for d in SALE_DAYS for _ in (0, 1)]},
    "Mode": {"columns": ["Mode"], "rows": [["One"], ["Two"]]},
}
RELS = [{"FromTable": "Sales", "FromColumn": "SDate", "ToTable": "Date", "ToColumn": "Date", "IsActive": 1}]
DESKTOP = [
    ('MaxWeekNotBlank', "MAXX(FILTER(ALLSELECTED('Date'), NOT ISBLANK([Total])), 'Date'[Week])", '12'),
    ('SelMax', "CALCULATE(MAX('Date'[WeekStart]), FILTER(ALLSELECTED('Date'), NOT ISBLANK([Total]) && 'Date'[Week] = [MaxWeekNotBlank]))", '2024-03-18T00:00:00'),
    ('SelMin', 'SWITCH(SELECTEDVALUE(Mode[Mode]), "Two", CALCULATE(MIN(\'Date\'[WeekStart]), FILTER(ALLSELECTED(\'Date\'), NOT ISBLANK([Total]) && \'Date\'[Week] >= [MaxWeekNotBlank] - 1)), CALCULATE(MIN(\'Date\'[WeekStart]), FILTER(ALLSELECTED(\'Date\'), NOT ISBLANK([Total]) && \'Date\'[Week] = [MaxWeekNotBlank])))', '2024-03-18T00:00:00'),
    ('Trend', "IF(AND(SELECTEDVALUE('Date'[WeekStart]) >= [SelMin], SELECTEDVALUE('Date'[WeekStart]) <= [SelMax]), [Total])", None),
    ('AllMaxRows', "CALCULATE(COUNTROWS('Date'), ALL('Date')) + 0", '91'),
    ('ModeSel', 'SELECTEDVALUE(Mode[Mode], "none")', 'none'),
    ('WeeksSelMax', 'CONCATENATEX(VALUES(\'Date\'[WeekStart]), FORMAT([SelMax], "yyyymmdd"), ",", \'Date\'[WeekStart])', '20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318'),
    ('WeeksSelMin', 'CONCATENATEX(VALUES(\'Date\'[WeekStart]), FORMAT([SelMin], "yyyymmdd"), ",", \'Date\'[WeekStart])', '20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318,20240318'),
    ('WeeksTrend', 'CONCATENATEX(VALUES(\'Date\'[WeekStart]), [Trend] & "", ",", \'Date\'[WeekStart])', ',,,,,,,,,,,38,'),
    ('WeeksMWNB', 'CONCATENATEX(VALUES(\'Date\'[WeekStart]), [MaxWeekNotBlank] & "", ",", \'Date\'[WeekStart])', '12,12,12,12,12,12,12,12,12,12,12,12,12'),
    ('SumTrend', "SUMX(VALUES('Date'[WeekStart]), [Trend])", '38'),
    ('Feb_SelMax', 'CALCULATE(FORMAT([SelMax], "yyyymmdd"), \'Date\'[Month] = 2)', '20240226'),
    ('Feb_WeeksSelMax', "CALCULATE([WeeksSelMax], 'Date'[Month] = 2)", '20240226,20240226,20240226,20240226,20240226'),
    ('Feb_WeeksTrend', "CALCULATE([WeeksTrend], 'Date'[Month] = 2)", ',,,,112'),
    ('JanFeb_SumTrend', "CALCULATE([SumTrend], 'Date'[Month] <= 2)", '112'),
    ('Two_WeeksSelMin', 'CALCULATE([WeeksSelMin], Mode[Mode] = "Two")', '20240311,20240311,20240311,20240311,20240311,20240311,20240311,20240311,20240311,20240311,20240311,20240311,20240311'),
    ('Two_SumTrend', 'CALCULATE([SumTrend], Mode[Mode] = "Two")', '90'),
    ('Two_Feb_WeeksTrend', 'CALCULATE([WeeksTrend], Mode[Mode] = "Two", \'Date\'[Month] = 2)', ',,,84,112'),
    ('RowsSelMaxDistinct', 'COUNTROWS(DISTINCT(SELECTCOLUMNS(ALLSELECTED(\'Date\'), "m", [SelMax])))', '1'),
    ('RowsMWNBDistinct', 'COUNTROWS(DISTINCT(SELECTCOLUMNS(ALLSELECTED(\'Date\'), "m", [MaxWeekNotBlank])))', '1'),
    ('OuterDep', 'CONCATENATEX(VALUES(\'Date\'[WeekStart]), CALCULATE(COUNTROWS(FILTER(\'Date\', NOT ISBLANK([Total]))) + 0) & "", ",", \'Date\'[WeekStart])', '2,2,2,2,2,0,0,2,2,2,2,1,0'),
]
MEASURES = {"Total": "SUM(Sales[Amount])", **{k: dax for k, dax, _w in DESKTOP}}


def _norm(v):
    v = de._scalarize(v)
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return None if v is None else str(v)


@pytest.fixture(scope="module")
def answers():
    got = de.evaluate_measures_batch(list(MEASURES), TABLES, dict(MEASURES), filter_context={},
                                     date_table="Date", date_column="Date", relationships=RELS)
    return {k: _norm(v) for k, v in got.items()}


@pytest.mark.parametrize("name, dax, want", DESKTOP, ids=[d[0] for d in DESKTOP])
def test_matches_desktop(answers, name, dax, want):
    assert answers[name] == want, (name, dax)


def _ctx(measures):
    return de.DAXContext(TABLES, measures, "Date", "Date", {}, RELS)


@pytest.mark.parametrize("name", ["SelMax", "SelMin"])
def test_shielded(name):
    assert "Date" in de._engine._shielded_tables(name, _ctx(dict(MEASURES)))


def test_outer_filter_reader_not_shielded():
    measures = dict(MEASURES, Dep="CALCULATE(MAX('Date'[WeekStart]), FILTER('Date', NOT ISBLANK([Total])))")
    assert "Date" not in de._engine._shielded_tables("Dep", _ctx(measures))


def test_grouped_rows_share_the_selection_dates(monkeypatch):
    """One week per call, as a grouped visual queries: the selection's dates
    are computed once for the 13 weeks, not once per week."""
    body = MEASURES["SelMax"]
    calls = {"n": 0}
    real = de._engine._eval_expr

    def counting(expr, ctx, *a, **kw):
        if isinstance(expr, str) and expr.strip() == body:
            calls["n"] += 1
        return real(expr, ctx, *a, **kw)

    monkeypatch.setattr(de._engine, "_eval_expr", counting)
    weeks = sorted({r[3] for r in TABLES["Date"]["rows"]})
    cache: dict = {}
    vals = []
    for w in weeks:
        got = de.evaluate_measures_batch(["SelMax"], TABLES, dict(MEASURES), {"Date.WeekStart": [w]},
                                         "Date", "Date", RELS, group_keys={"Date.WeekStart"},
                                         selected_filters={}, cache=cache)
        vals.append(_norm(got["SelMax"]))
    assert set(vals) == {"2024-03-18T00:00:00"}      # Desktop, every week
    assert calls["n"] <= 2
