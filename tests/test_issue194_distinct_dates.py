"""Issue #194: a time-intelligence function over a column whose dates repeat
returns each date once.

Over a fact table's date column (two sales a day), DATESBETWEEN,
DATESINPERIOD, DATESYTD, DATESMTD, DATESQTD, PARALLELPERIOD and NEXTMONTH
returned a row per fact row; Desktop returns the distinct dates.

Expected values: Power BI Desktop 2.152 over ADOMD (build_b192.py, the
Fact*Rows probes)."""
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
    ('FactStartBlankRows', 'COUNTROWS(DATESBETWEEN(Sales[SDate], BLANK(), DATE(2024,1,10)))', '4'),
    ('FactBothBlankRows', 'COUNTROWS(DATESBETWEEN(Sales[SDate], BLANK(), BLANK()))', '15'),
    ('FactBothRows', 'COUNTROWS(DATESBETWEEN(Sales[SDate], DATE(2023,12,1), DATE(2024,1,10)))', '4'),
    ('FactInPeriodRows', 'COUNTROWS(DATESINPERIOD(Sales[SDate], DATE(2024,1,10), -2, MONTH))', '4'),
    ('FactYTDRows_Mar', "CALCULATE(COUNTROWS(DATESYTD(Sales[SDate])), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '6'),
    ('FactPMRows_Mar', "CALCULATE(COUNTROWS(PREVIOUSMONTH(Sales[SDate])), 'Date'[Month] = 3, 'Date'[Year] = 2024)", None),
    ('FactDateAddRows_Mar', "CALCULATE(COUNTROWS(DATEADD(Sales[SDate], -2, MONTH)), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '3'),
    ('FactSPLYRows_Mar', "CALCULATE(COUNTROWS(SAMEPERIODLASTYEAR(Sales[SDate])), 'Date'[Month] = 3, 'Date'[Year] = 2024)", None),
    ('FactPPRows_Mar', "CALCULATE(COUNTROWS(PARALLELPERIOD(Sales[SDate], -2, MONTH)), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '3'),
    ('FactMTDRows_Mar', "CALCULATE(COUNTROWS(DATESMTD(Sales[SDate])), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '3'),
    ('FactQTDRows_Mar', "CALCULATE(COUNTROWS(DATESQTD(Sales[SDate])), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '6'),
    ('FactNextMonthRows_Mar', "CALCULATE(COUNTROWS(NEXTMONTH(Sales[SDate])), 'Date'[Month] = 3, 'Date'[Year] = 2024)", '3'),
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
