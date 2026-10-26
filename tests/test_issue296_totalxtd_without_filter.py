"""#296: TOTALYTD / TOTALQTD / TOTALMTD without a filter are CALCULATE(e, DATESxTD(d)).

What was wrong: without a filter argument, TOTALxTD took a path of its own
(_total_over_dates) that applied the dates without the context transition of an iterated row,
and evaluated the expression unfiltered when there were no dates. SUMX(F, TOTALYTD(SUM(F[v]),
Cal[Date])) was 16383 where SUMX(F, CALCULATE(SUM(F[v]), DATESYTD(Cal[Date]))) is 13311, and
under Cal's blank row TOTALYTD was 3072 (the blank row's orders) where the CALCULATE is BLANK.
With a filter argument (#282) it already went through CALCULATE, so a filter doing
nothing changed the answer.

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256c
(build_b256c.py q_*), over the model of tests/test_issue250_userelationship_date_part.py: each
row counts its own v where its order day is in its year to date, and rows 10 and 11 reach Cal's
blank row, whose year to date is empty: 16383 - 1024 - 2048 = 13311. TOTALMTD and TOTALQTD in the
same shapes are exact arithmetic by the same reading (each order day is in its own month and
quarter to date).
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

PROBES = {
    "q_sumx_ytd": ("SUMX(F, TOTALYTD(SUM(F[v]), Cal[Date]))", 13311),
    "q_sumx_dytd": ("SUMX(F, CALCULATE(SUM(F[v]), DATESYTD(Cal[Date])))", 13311),
    "q_sumx_ytd_noop": ("SUMX(F, TOTALYTD(SUM(F[v]), Cal[Date], ALL(P[x])))", 13311),
    "q_ytd_blank": ("CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date]), ISBLANK(Cal[Date]))", None),
    "q_dytd_blank": ("CALCULATE(CALCULATE(SUM(F[v]), DATESYTD(Cal[Date])), ISBLANK(Cal[Date]))", None),
    # exact arithmetic, as q_sumx_ytd
    "q_sumx_mtd": ("SUMX(F, TOTALMTD(SUM(F[v]), Cal[Date]))", 13311),
    "q_sumx_qtd": ("SUMX(F, TOTALQTD(SUM(F[v]), Cal[Date]))", 13311),
    "q_mtd_blank": ("CALCULATE(TOTALMTD(SUM(F[v]), Cal[Date]), ISBLANK(Cal[Date]))", None),
    # controls: B256's YTD under a day (c_ytd_jan3 8696) and over the whole calendar
    "q_ytd_jan3": ("CALCULATE(TOTALYTD(SUM(F[v]), Cal[Date]), Cal[Date] = DATE(2024, 1, 3))", 8696),
    "q_ytd_all": ("TOTALYTD(SUM(F[v]), Cal[Date])", 13304),
}
ALL_MEASURES = {**MEASURES, **{k: e for k, (e, _w) in PROBES.items()}}


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


@pytest.mark.parametrize("name", list(PROBES))
def test_each_probe_on_a_fresh_cache(name, path):
    got = de.evaluate_measure(name, TABLES, ALL_MEASURES, {}, relationships=RELS)
    assert got == PROBES[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_all_probes_in_one_batch(order, path):
    names = list(PROBES) if order == "forward" else list(reversed(PROBES))
    got = de.evaluate_measures_batch(names, TABLES, dict(ALL_MEASURES), {}, relationships=RELS)
    assert got == {n: w for n, (_e, w) in PROBES.items()}
