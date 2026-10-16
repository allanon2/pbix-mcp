"""Issue #192 (OpenBI's doc 65): DATESBETWEEN with a BLANK start or end.

A BLANK StartDate is the first date of the dates column and a BLANK EndDate
its last -- of the whole column, whatever filters are in force: the
documented life-to-date CALCULATE(..., DATESBETWEEN('Date'[Date], BLANK(),
MAX('Date'[Date]))) under March starts on the column's first day. The engine
returned no dates for either.

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW per
measure (the Desktop oracle toolkit's build_b192.py: Date runs December
2023 - May 2024, Sales holds two rows on days 5, 15 and 25 of each month but
February, Amount the day)."""
from __future__ import annotations

import datetime as dt

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

DAYS = [dt.datetime(2023, 12, 1) + dt.timedelta(days=i) for i in range(183)]
SALE_DAYS = [d for d in DAYS if d.day in (5, 15, 25) and not (d.year == 2024 and d.month == 2)]
TABLES = {
    "Date": {"columns": ["Date", "Year", "Month"], "rows": [[d, d.year, d.month] for d in DAYS]},
    "Sales": {"columns": ["SDate", "Amount"], "rows": [[d, d.day] for d in SALE_DAYS for _ in (0, 1)]},
}
RELS = [{"FromTable": "Sales", "FromColumn": "SDate", "ToTable": "Date", "ToColumn": "Date", "IsActive": 1}]
DESKTOP = [
    ('Both', "CALCULATE([Total], DATESBETWEEN('Date'[Date], DATE(2024,1,2), DATE(2024,1,20)))", '40'),
    ('StartBlank', "CALCULATE([Total], DATESBETWEEN('Date'[Date], BLANK(), DATE(2024,1,10)))", '100'),
    ('EndBlank', "CALCULATE([Total], DATESBETWEEN('Date'[Date], DATE(2024,4,10), BLANK()))", '170'),
    ('BothBlank', "CALCULATE([Total], DATESBETWEEN('Date'[Date], BLANK(), BLANK()))", '450'),
    ('RowsStartBlank', "COUNTROWS(DATESBETWEEN('Date'[Date], BLANK(), DATE(2024,1,10)))", '41'),
    ('RowsEndBlank', "COUNTROWS(DATESBETWEEN('Date'[Date], DATE(2024,4,10), BLANK()))", '52'),
    ('RowsBothBlank', "COUNTROWS(DATESBETWEEN('Date'[Date], BLANK(), BLANK()))", '183'),
    ('FirstStartBlank', "FIRSTDATE(DATESBETWEEN('Date'[Date], BLANK(), DATE(2024,1,10)))", '2023-12-01T00:00:00'),
    ('LastEndBlank', "LASTDATE(DATESBETWEEN('Date'[Date], DATE(2024,4,10), BLANK()))", '2024-05-31T00:00:00'),
    ('LTD_Mar', "CALCULATE(CALCULATE([Total], DATESBETWEEN('Date'[Date], BLANK(), MAX('Date'[Date]))), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '270'),
    ('LTDrows_Mar', "CALCULATE(COUNTROWS(DATESBETWEEN('Date'[Date], BLANK(), MAX('Date'[Date]))), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '122'),
    ('LTDfirst_Mar', "CALCULATE(FIRSTDATE(DATESBETWEEN('Date'[Date], BLANK(), MAX('Date'[Date]))), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '2023-12-01T00:00:00'),
    ('ToEndRows_Mar', "CALCULATE(COUNTROWS(DATESBETWEEN('Date'[Date], MIN('Date'[Date]), BLANK())), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '92'),
    ('ToEndLast_Mar', "CALCULATE(LASTDATE(DATESBETWEEN('Date'[Date], MIN('Date'[Date]), BLANK())), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '2024-05-31T00:00:00'),
    ('BothBlankRows_Mar', "CALCULATE(COUNTROWS(DATESBETWEEN('Date'[Date], BLANK(), BLANK())), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '183'),
    ('EmptyMaxStart', "COUNTROWS(DATESBETWEEN('Date'[Date], CALCULATE(MAX('Date'[Date]), 'Date'[Date] > DATE(2030,1,1)), DATE(2024,1,10)))", '41'),
    ('VarBlankEnd', "VAR e = BLANK() RETURN COUNTROWS(DATESBETWEEN('Date'[Date], DATE(2024,5,20), e))", '12'),
    ('FactStartBlankFirst', 'FIRSTDATE(DATESBETWEEN(Sales[SDate], BLANK(), DATE(2024,1,10)))', '2023-12-05T00:00:00'),
    ('FactEndBlankLast', 'LASTDATE(DATESBETWEEN(Sales[SDate], DATE(2024,1,10), BLANK()))', '2024-05-25T00:00:00'),
    ('StartBlankBeforeFirst', "COUNTROWS(DATESBETWEEN('Date'[Date], BLANK(), DATE(2023,6,1))) + 0", '0'),
    ('EndBlankAfterLast', "COUNTROWS(DATESBETWEEN('Date'[Date], DATE(2025,1,1), BLANK())) + 0", '0'),
    ('Reversed', "COUNTROWS(DATESBETWEEN('Date'[Date], DATE(2024,3,1), DATE(2024,2,1))) + 0", '0'),
    ('StartBlank_Year2024', "CALCULATE(COUNTROWS(DATESBETWEEN('Date'[Date], BLANK(), DATE(2024,1,10))), 'Date'[Year] = 2024)", '41'),
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
