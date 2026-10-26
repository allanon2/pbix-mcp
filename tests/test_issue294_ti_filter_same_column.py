"""#294: a time-intelligence filter and another filter argument on the same date column intersect.

What was wrong: CALCULATE's filter arguments are combined with AND (Microsoft's CALCULATE
reference), and _fn_calculate keeps a record (applied_here) of what its own arguments wrote so
that two of them on one column intersect while one on a column the OUTER context filtered
replaces it. The time-intelligence tables (DATESYTD, PREVIOUSDAY, ENDOFMONTH, ...: the
_apply_date_rows path), the dates TOTALYTD / TOTALMTD / TOTALQTD and OPENING/CLOSINGBALANCE*
apply beside their filter arguments (#282, the same path), the DATEADD /
SAMEPERIODLASTYEAR shift (_rebase_filter_delta) and a single-column table filter
(FILTER(ALL(Cal[Date]), ...), VALUES(Cal[Date])) never wrote that record, nor read it. So:

- a later argument on the date column REPLACED the dates: TOTALYTD(SUM(F[v]), Cal[Date],
  Cal[Date] <= DATE(2024, 1, 3)) under January 3 2024 was 11775, every order up to January 3 of
  either year and the blank row's, after #282 (0.9.147 dropped the filter and
  happened to answer 8696); CALCULATE(SUM(F[v]), DATESYTD(Cal[Date]), Cal[Date] <= DATE(2024, 1,
  3)) was 11775 on 0.9.147 too;
- the dates replaced an EARLIER argument's filter on the date column, and cleared what it wrote
  on the table's other columns: CALCULATE(SUM(F[v]), Cal[Year] = 2023, DATESYTD(Cal[Date])) under
  January 3 2024 was 8696 (a 2023 year and 2024 dates meet nowhere: BLANK);
- two FILTER(ALL(Cal[Date]), ...) arguments: the second replaced the first;
- and an intersection of two arguments on a date column whose filter clears the rest of the
  date table (#78: a DateTime relationship column, or a marked date table's) did not count as a
  filter on it: CALCULATE(SUM(F[v]), Cal[Date] >= DATE(2024, 1, 2), Cal[Date] <= DATE(2024, 1,
  3)) under Cal[Year] = 2023 kept the 2023 year: BLANK. Desktop adds ALL(Cal) to a CALCULATE that
  filters Cal[Date] (#78, measured on single filter arguments); two arguments on it are such a
  filter, so the window's 8672.

Power BI Desktop 2.152 confirms the reading (battery B256c, build_b256c.py d_*): d_ytd_le_j3
8696, d_ytd_le_j2 120, d_ytd_filter_bit 8672, d_cbm_le_j2 BLANK, d_calc_dytd_ge_j2 and
d_calc_ge_j2_dytd 8672, d_calc_year_dytd BLANK, d_dateadd_lt_j2 BLANK, d_two_filters 8672,
d_two_preds_year 8672 and d_two_filters_year 8672.

Where the expected values come from: exact arithmetic over the model of
tests/test_issue250_userelationship_date_part.py (Power BI Desktop 2.152's battery B256,
build_b256.py). Each F row's v is a distinct power of two; F[Order] -> Cal[Date] joins by value,
so the orders put 3 on Dec 30 2023, 4 on Dec 31, 24 on Jan 1 2024, 96 on Jan 2, 8576 on Jan 3,
512 on Jan 4, 4096 on Jan 5 and 3072 on Cal's blank row (row 10's order carries a time of day,
row 11's is before the calendar); 8696 is January 1..3 2024. Activated, the DatePartOnly
F[Ship] puts 6 on Jan 1 and 24 on Jan 2 (B256 a_ship_jan1 / a_ship_jan2). ENDOFMONTH under
January 3 is January 5, the month's last Cal date (B256b t_cbm_ur_j3 1536 is January 5's
ships). Desktop answers the intersection for another column of the date table
(build_b256b.py t_ytd_year_j3: TOTALYTD(SUM(F[v]), Cal[Date], Cal[Year] = 2023) under January 3
2024 is BLANK).
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES, same

pytestmark = pytest.mark.unit

UR = "USERELATIONSHIP(F[Ship], Cal[Date])"
J3 = "Cal[Date] = DATE(2024, 1, 3)"


def at(expr, outer=J3):
    return f"CALCULATE({expr}, {outer})"


PROBES = {
    # TOTALxTD's filter argument on the date column (#282's path)
    "ytd_le_jan3": (at("TOTALYTD(SUM(F[v]), Cal[Date], Cal[Date] <= DATE(2024, 1, 3))"), 8696),
    "ytd_le_jan2": (at("TOTALYTD(SUM(F[v]), Cal[Date], Cal[Date] <= DATE(2024, 1, 2))"), 24 + 96),
    # the bound as an aggregate: a CALCULATE in a True/False filter is refused
    # (Power BI Desktop 2.152, build_b255c.py k24; Learn's TOTALYTD: its filter
    # "has restrictions described in the topic, CALCULATE"; #271)
    "ytd_le_maxdata": (at("TOTALYTD(SUM(F[v]), Cal[Date], Cal[Date] <= MAXX(ALL(F), F[Order]))"),
                       8696),
    "mtd_le_jan3": (at("TOTALMTD(SUM(F[v]), Cal[Date], Cal[Date] <= DATE(2024, 1, 3))"), 8696),
    "ytd_ge_jan2": (at("TOTALYTD(SUM(F[v]), Cal[Date], Cal[Date] >= DATE(2024, 1, 2))"), 96 + 8576),
    "ytd_filter_all_date": (at("TOTALYTD(SUM(F[v]), Cal[Date], FILTER(ALL(Cal[Date]), "
                               "Cal[Date] <= DATE(2024, 1, 3)))"), 8696),
    "ytd_filter_all_cal": (at("TOTALYTD(SUM(F[v]), Cal[Date], FILTER(ALL(Cal), Cal[Year] = 2024))"), 8696),
    "ytd_in": (at("TOTALYTD(SUM(F[v]), Cal[Date], Cal[Date] IN {DATE(2024, 1, 2), DATE(2024, 1, 5)})"), 96),
    "ytd_treatas": (at("TOTALYTD(SUM(F[v]), Cal[Date], TREATAS({DATE(2024, 1, 2), DATE(2024, 1, 5)}, "
                       "Cal[Date]))"), 96),
    "ytd_values": (at("TOTALYTD(SUM(F[v]), Cal[Date], VALUES(Cal[Date]))"), 8576),
    "ytd_ge_under_bit": (at("TOTALYTD(SUM(F[v]), Cal[Date], Cal[Date] >= DATE(2024, 1, 2))", "Cal[Bit] = 16"),
                         96 + 8576),
    "ytd_filter_under_bit": (at("TOTALYTD(SUM(F[v]), Cal[Date], FILTER(ALL(Cal[Date]), "
                                "Cal[Date] >= DATE(2024, 1, 2)))", "Cal[Bit] = 16"), 96 + 8576),
    "ytd_le_under_year": (at("TOTALYTD(SUM(F[v]), Cal[Date], Cal[Date] <= DATE(2024, 1, 2))", "Cal[Year] = 2024"),
                          24 + 96),
    "qtd_ur_le_jan2": (at(f"TOTALQTD(SUM(F[v]), Cal[Date], {UR}, Cal[Date] <= DATE(2024, 1, 2))"), 6 + 24),
    "cbm_le_jan2": (at("CLOSINGBALANCEMONTH(SUM(F[v]), Cal[Date], Cal[Date] <= DATE(2024, 1, 2))"), None),
    # a time-intelligence table as CALCULATE's own filter argument, in either order
    "calc_dytd_le_jan3": (at("CALCULATE(SUM(F[v]), DATESYTD(Cal[Date]), Cal[Date] <= DATE(2024, 1, 3))"), 8696),
    "calc_dytd_ge_jan2": (at("CALCULATE(SUM(F[v]), DATESYTD(Cal[Date]), Cal[Date] >= DATE(2024, 1, 2))"),
                          96 + 8576),
    "calc_ge_jan2_dytd": (at("CALCULATE(SUM(F[v]), Cal[Date] >= DATE(2024, 1, 2), DATESYTD(Cal[Date]))"),
                          96 + 8576),
    "calc_le_jan2_dytd": (at("CALCULATE(SUM(F[v]), Cal[Date] <= DATE(2024, 1, 2), DATESYTD(Cal[Date]))"), 24 + 96),
    "calc_year_dytd": (at("CALCULATE(SUM(F[v]), Cal[Year] = 2023, DATESYTD(Cal[Date]))"), None),
    "calc_dytd_in": (at("CALCULATE(SUM(F[v]), DATESYTD(Cal[Date]), Cal[Date] IN {DATE(2024, 1, 2), "
                        "DATE(2024, 1, 5)})"), 96),
    "calc_dytd_ge_under_bit": (at("CALCULATE(SUM(F[v]), DATESYTD(Cal[Date]), Cal[Date] >= DATE(2024, 1, 2))",
                                  "Cal[Bit] = 16"), 96 + 8576),
    "calc_ge_dytd_under_bit": (at("CALCULATE(SUM(F[v]), Cal[Date] >= DATE(2024, 1, 2), DATESYTD(Cal[Date]))",
                                  "Cal[Bit] = 16"), 96 + 8576),
    # DATEADD (January 2 under January 3)
    "dateadd_ge_jan2": (at("CALCULATE(SUM(F[v]), DATEADD(Cal[Date], -1, DAY), Cal[Date] >= DATE(2024, 1, 2))"),
                        96),
    "dateadd_lt_jan2": (at("CALCULATE(SUM(F[v]), DATEADD(Cal[Date], -1, DAY), Cal[Date] < DATE(2024, 1, 2))"),
                        None),
    "dateadd_ge_under_bit": (at("CALCULATE(SUM(F[v]), DATEADD(Cal[Date], -1, DAY), "
                                "Cal[Date] >= DATE(2024, 1, 2))", "Cal[Bit] = 16"), 96),
    # two single-column table filters on one column
    "two_filters": ("CALCULATE(SUM(F[v]), FILTER(ALL(Cal[Date]), Cal[Date] >= DATE(2024, 1, 2)), "
                    "FILTER(ALL(Cal[Date]), Cal[Date] <= DATE(2024, 1, 3)))", 96 + 8576),
    # #78: two arguments on Cal[Date] still clear an outer Cal[Year]
    "two_preds_under_year": (at("CALCULATE(SUM(F[v]), Cal[Date] >= DATE(2024, 1, 2), "
                                "Cal[Date] <= DATE(2024, 1, 3))", "Cal[Year] = 2023"), 96 + 8576),
    "two_filters_under_year": (at("CALCULATE(SUM(F[v]), FILTER(ALL(Cal[Date]), Cal[Date] >= DATE(2024, 1, 2)), "
                                  "FILTER(ALL(Cal[Date]), Cal[Date] <= DATE(2024, 1, 3)))", "Cal[Year] = 2023"),
                               96 + 8576),
    # controls: right on 0.9.147 and still right
    "ytd_keep_le_jan3": (at("TOTALYTD(SUM(F[v]), Cal[Date], KEEPFILTERS(Cal[Date] <= DATE(2024, 1, 3)))"), 8696),
    "ytd_year_2024": (at("TOTALYTD(SUM(F[v]), Cal[Date], Cal[Year] = 2024)"), 8696),
    "ytd_all_cal": (at("TOTALYTD(SUM(F[v]), Cal[Date], ALL(Cal))"), 8696),
    "calc_dytd_dmtd": (at("CALCULATE(SUM(F[v]), DATESYTD(Cal[Date]), DATESMTD(Cal[Date]))"), 8696),
    "calc_dytd_prevday": (at("CALCULATE(SUM(F[v]), DATESYTD(Cal[Date]), PREVIOUSDAY(Cal[Date]))"), 96),
    "dateadd_rev_ge_jan2": (at("CALCULATE(SUM(F[v]), Cal[Date] >= DATE(2024, 1, 2), DATEADD(Cal[Date], -1, DAY))"),
                            96),
    "keep_filter_under_in": (at("CALCULATE(SUM(F[v]), KEEPFILTERS(FILTER(ALL(Cal[Date]), "
                                "Cal[Date] >= DATE(2024, 1, 2))))",
                                "Cal[Date] IN {DATE(2024, 1, 1), DATE(2024, 1, 2)}"), 96),
    "ytd_plain": (at("TOTALYTD(SUM(F[v]), Cal[Date])"), 8696),
}
ALL_MEASURES = {**MEASURES, **{k: e for k, (e, _w) in PROBES.items()}}


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


def evaluate(name):
    return de.evaluate_measure(name, TABLES, ALL_MEASURES, {}, relationships=RELS)


@pytest.mark.parametrize("name", list(PROBES))
def test_each_probe_on_a_fresh_cache(name, path):
    got, want = evaluate(name), PROBES[name][1]
    assert same(got, want), (name, got, want)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_all_probes_in_one_cache(order, path):
    names = list(PROBES) if order == "forward" else list(reversed(PROBES))
    got = [(n, evaluate(n)) for n in names]
    assert [(n, g, PROBES[n][1]) for n, g in got if not same(g, PROBES[n][1])] == []
