"""Issue #78: a date filter clears the date table's other filters only where
Power BI does.

Power BI adds ALL(<date table>) to a filter on a date column only when the
column is a MARKED date table's date column, or a DateTime column used in a
relationship — active or inactive. On any other table the dates intersect
with the table's Year / Month / Day filters (the classic "YTD equals the
month until you mark the date table").

The engine had the rule in pieces, each half wrong:
  * CALCULATE with a time-intelligence filter (DATESYTD, ...) cleared ALWAYS;
  * TOTALxTD, STARTOF* / ENDOF* and the balances never cleared (PRs #68 / #72
    made them clear ALWAYS);
  * #73's direct-filter rule covered marked tables only.

Every expected value below is Power BI Desktop's (2.152, queried over ADOMD)
for the same shapes and data: daily dates, v = day of month, grouped by
Year x Month (and Year x Month x Day for ENDOFMONTH).
"""
import json
from datetime import date, datetime, timedelta

import pytest

from pbix_mcp import server as S
from pbix_mcp.builder import PBIXBuilder

pytestmark = pytest.mark.unit

DAYS = [date(2024, 1, 1) + timedelta(days=i) for i in range(91)]   # Jan..Mar 2024
DATE_COLS = [{"name": "Date", "data_type": "DateTime"},
             {"name": "Year", "data_type": "Int64"},
             {"name": "Month", "data_type": "Int64"},
             {"name": "Day", "data_type": "Int64"},
             {"name": "Key", "data_type": "Int64"}]


def _row(d, **extra):
    return {"Date": datetime(d.year, d.month, d.day), "Year": d.year, "Month": d.month,
            "Day": d.day, "Key": d.year * 10000 + d.month * 100 + d.day, **extra}


# table -> (fact table, fact value column); DU measures over itself
TABLES = {"DM": ("DM", "v"), "DU": ("DU", "v"), "DR": ("FR", "amt"),
          "DI": ("FI", "amt"), "DK": ("FK", "amt")}
SHAPES = {
    "ytd": "TOTALYTD(SUM({f}[{v}]), {t}[Date])",
    "cytd": "CALCULATE(SUM({f}[{v}]), DATESYTD({t}[Date]))",
    "le": "CALCULATE(SUM({f}[{v}]), {t}[Date] <= DATE(2024, 2, 15))",
    "run": "CALCULATE(SUM({f}[{v}]), FILTER(ALL({t}[Date]), {t}[Date] <= MAX({t}[Date])))",
    "eom": "CALCULATE(SUM({f}[{v}]), ENDOFMONTH({t}[Date]))",
}


def build_date_model(path, extra=None):
    """DM marked; DU unmarked, unrelated; DR related on its DateTime column;
    DI reached by an ACTIVE integer key plus an INACTIVE DateTime relationship;
    DK related by an integer key only. `extra(builder)` may add more."""
    b = PBIXBuilder("issue78")
    vcol = [{"name": "v", "data_type": "Int64"}]
    b.add_table("DM", DATE_COLS + vcol, rows=[_row(d, v=d.day) for d in DAYS])
    b.add_table("DU", DATE_COLS + vcol, rows=[_row(d, v=d.day) for d in DAYS])
    for dim, fact in (("DR", "FR"), ("DI", "FI"), ("DK", "FK")):
        b.add_table(dim, DATE_COLS, rows=[_row(d) for d in DAYS])
        b.add_table(fact, [{"name": "Date", "data_type": "DateTime"},
                           {"name": "Key", "data_type": "Int64"},
                           {"name": "amt", "data_type": "Int64"}],
                    rows=[{"Date": datetime(d.year, d.month, d.day),
                           "Key": _row(d)["Key"], "amt": d.day} for d in DAYS])
    b.add_relationship("FR", "Date", "DR", "Date")
    b.add_relationship("FI", "Key", "DI", "Key")
    b.add_relationship("FI", "Date", "DI", "Date", is_active=False)
    b.add_relationship("FK", "Key", "DK", "Key")
    for t, (f, v) in TABLES.items():
        for name, shape in SHAPES.items():
            b.add_measure(f, f"{t}_{name}", shape.format(t=t, f=f, v=v))
    if extra:
        extra(b)
    b.add_page("Page 1")
    b.save(path)
    alias = "i78_" + str(abs(hash(path)))[:8]
    assert json.loads(S.pbix_open(path, alias))["success"]
    # Mark DM as a date table, the way Desktop stores it: DataCategory 'Time',
    # IsKey on the date column and NOT on the RowNumber column.
    tid = "(SELECT ID FROM [Table] WHERE Name = 'DM')"
    for sql in ("UPDATE [Table] SET DataCategory = 'Time' WHERE Name = 'DM'",
                f"UPDATE [Column] SET IsKey = 0 WHERE Type = 3 AND TableID = {tid}",
                f"UPDATE [Column] SET IsKey = 1 WHERE ExplicitName = 'Date' AND TableID = {tid}"):
        assert json.loads(S.pbix_datamodel_modify_metadata(alias, sql))["success"]
    return alias


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("i78") / "dates.pbix")
    alias = build_date_model(path)
    yield alias
    S._open_files.pop(alias, None)
    S._dax_cache.pop(alias, None)


def _cell(alias, measure, table, **coords):
    fc = {f"{table}.{k}": [v] for k, v in coords.items()}
    r = json.loads(S.pbix_evaluate_dax(alias, measure, json.dumps(fc),
                                       apply_default_filters=False,
                                       group_by=json.dumps(sorted(fc))))
    assert r["success"], r
    (res,) = r["results"]
    return None if res.get("status") == "blank" else res.get("value")


# Desktop's values: (table, shape, month) -> value; None = BLANK
DESKTOP = {
    # marked, DateTime relationship (active or inactive): the dates CLEAR Month
    ("DM", "ytd", 2): 931, ("DR", "ytd", 2): 931, ("DI", "ytd", 2): 931,
    ("DM", "cytd", 2): 931, ("DR", "cytd", 2): 931, ("DI", "cytd", 2): 931,
    ("DM", "le", 1): 616, ("DR", "le", 1): 616, ("DI", "le", 1): 616,
    ("DM", "le", 3): 616, ("DR", "le", 3): 616, ("DI", "le", 3): 616,
    ("DM", "run", 2): 931, ("DR", "run", 2): 931, ("DI", "run", 2): 931,
    # unmarked and unrelated, or related by an integer key only: they INTERSECT
    ("DU", "ytd", 2): 435, ("DK", "ytd", 2): 435,
    ("DU", "cytd", 2): 435, ("DK", "cytd", 2): 435,
    ("DU", "le", 1): 496, ("DK", "le", 1): 496,
    ("DU", "le", 2): 120, ("DK", "le", 2): 120,
    ("DU", "le", 3): None, ("DK", "le", 3): None,
    ("DU", "run", 2): 435, ("DK", "run", 2): 435,
}


@pytest.mark.parametrize("table,shape,month", sorted(DESKTOP))
def test_year_month_cells_match_desktop(model, table, shape, month):
    got = _cell(model, f"{table}_{shape}", table, Year=2024, Month=month)
    assert got == DESKTOP[(table, shape, month)], (table, shape, month, got)


@pytest.mark.parametrize("table,expected", [
    ("DM", 31), ("DR", 31), ("DI", 31),   # ENDOFMONTH clears the Day filter
    ("DU", None), ("DK", None),           # Jan 31 intersected with Day = 1
])
def test_endofmonth_at_day_grain_matches_desktop(model, table, expected):
    assert _cell(model, f"{table}_eom", table, Year=2024, Month=1, Day=1) == expected


def test_running_total_at_day_grain(model):
    # Feb 10: cumulative where the rule applies (Jan 496 + Feb 1..10 = 551),
    # that day alone where it does not
    for table, expected in (("DM", 551), ("DR", 551), ("DI", 551), ("DU", 10), ("DK", 10)):
        assert _cell(model, f"{table}_run", table, Year=2024, Month=2, Day=10) == expected, table
