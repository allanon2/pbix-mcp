"""#282: TOTALYTD / TOTALQTD / TOTALMTD and OPENING/CLOSINGBALANCE* apply their filter arguments.

What was wrong: _fn_totalytd handed every argument after the dates to DATESYTD, which read the
filter as a year end and dropped it; _fn_totalmtd, _fn_totalqtd and _balance_at ignored
everything after the dates. So TOTALYTD(SUM(F[v]), Cal[Date], USERELATIONSHIP(F[Ship],
Cal[Date])) summed through F[Order] (8696 under January 3, where Desktop says 8254), a boolean
filter F[pk] = 1 did nothing (8696 for 72), and ALL(F[pk]) did not lift an outer F[pk] = 1.

The rule, from Desktop: TOTALxTD(e, d, f) is CALCULATE(e, DATESxTD(d), f) -- the filters
evaluated in the outer context and applied as CALCULATE's filter arguments, intersecting the
dates (TOTALYTD(.., Cal[Year] = 2023) under a 2024 day is BLANK) -- and the balances take theirs
the same way beside their date. A lone third argument of TOTALYTD is the year end when it is a
string literal, else a filter; a fourth is the year end. Without filters nothing changes
(c_ytd_jan3).

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, batteries B256
(build_b256.py: c_*, s_ytdship) and B256b (build_b256b.py: t_*), over the model of
tests/test_issue250_userelationship_date_part.py; each value is Desktop's answer to the probe
of that name.
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES, same

pytestmark = pytest.mark.unit

UR = "USERELATIONSHIP(F[Ship], Cal[Date])"
J3 = "Cal[Date] = DATE(2024, 1, 3)"
PROBES = {
    # B256
    "c_ytdship_jan3": ("CALCULATE([YTDship], Cal[Date] = DATE(2024, 1, 3))", 8254),
    "c_ytdship_dec31": ("CALCULATE([YTDship], Cal[Date] = DATE(2023, 12, 31))", 2049),
    "c_mtdship_jan3": (f"CALCULATE(TOTALMTD(SUM(F[v]), Cal[Date], {UR}), {J3})", 8254),
    "s_ytdship": ('CONCATENATEX(FILTER(VALUES(Cal[Date]), NOT ISBLANK(Cal[Date])), [YTDship] & "", ",", '
                  'Cal[Date], ASC)', "2048,2049,6,30,8254,8446,9982"),
    "c_dytdship_jan3": ("CALCULATE([DYTDship], Cal[Date] = DATE(2024, 1, 3))", 8254),
    "c_ytd_jan3": ("CALCULATE([YTD], Cal[Date] = DATE(2024, 1, 3))", 8696),
    # B256b
    "t_qtd_ur_j3": (f"CALCULATE(TOTALQTD(SUM(F[v]), Cal[Date], {UR}), {J3})", 8254),
    "t_ytd_bool_j3": (f"CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date], F[pk] = 1), {J3})", 72),
    "t_mtd_bool_j3": (f"CALCULATE(TOTALMTD(SUM(F[v]), Cal[Date], F[pk] = 1), {J3})", 72),
    "t_qtd_bool_j3": (f"CALCULATE(TOTALQTD(SUM(F[v]), Cal[Date], F[pk] = 1), {J3})", 72),
    "t_ytd_allpk_j3": (f"CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date], ALL(F[pk])), {J3}, F[pk] = 1)", 8696),
    "t_ytd_year_j3": (f"CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date], Cal[Year] = 2023), {J3})", None),
    "t_ytd_ur_ye_j2": (f'CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date], {UR}, "2023-12-30"), '
                       'Cal[Date] = DATE(2024, 1, 2))', 31),
    "t_ytd_ur_ye_dec31": (f'CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date], {UR}, "2023-12-30"), '
                          'Cal[Date] = DATE(2023, 12, 31))', 1),
    "t_cbm_ur_j3": (f"CALCULATE(CLOSINGBALANCEMONTH(SUM(F[v]), Cal[Date], {UR}), {J3})", 1536),
    "t_obm_ur_j3": (f"CALCULATE(OPENINGBALANCEMONTH(SUM(F[v]), Cal[Date], {UR}), {J3})", 1),
    "t_cby_ur_dec30": (f"CALCULATE(CLOSINGBALANCEYEAR(SUM(F[v]), Cal[Date], {UR}), "
                       "Cal[Date] = DATE(2023, 12, 30))", 1),
    "t_oby_ur_j3": (f"CALCULATE(OPENINGBALANCEYEAR(SUM(F[v]), Cal[Date], {UR}), {J3})", 1),
    "t_cbq_pk2_j3": (f"CALCULATE(CLOSINGBALANCEQUARTER(SUM(F[v]), Cal[Date], F[pk] = 2), {J3})", None),
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
