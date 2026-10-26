"""#285: a row transition's own filter does not reach a table its expansion holds.

What was wrong: SUMX(F, CALCULATE(SUM(Cal[Bit]), USERELATIONSHIP(F[Ship], Cal[Date]),
CROSSFILTER(F[Ship], Cal[Date], Both))). The transition of each F row filters the row's expanded
table -- Cal at the row's ORDER day, through the F[Order] relationship active when the
iteration ran (issue #115) -- and the engine also carried the row's own F filter, and the
expansion's P part, to Cal through the bidirectional F[Ship], intersecting the order day with the
ship day: only row 13, shipped the day it was ordered, kept a day (16; COUNTROWS(Cal) 1).

The rule, from Desktop: the transition is ONE filter, on the row's expanded table; while its Cal
part is there, no part of it reaches Cal through a relationship. Taken back out --
REMOVEFILTERS(Cal) -- the rest of it filters F, and that reaches Cal through the bidirectional
relationship like any filter (251, the ship days). (The other way round the expansion does reach
F: #286.)

"Its Cal part is there" means its filter on the column the relationship joins Cal on, Cal[Date],
and F's own filters are still the row's (battery B256c, build_b256c.py t_*): REMOVEFILTERS(
Cal[Date]) lets the row through F[Ship] to meet the order day's Cal[Bit] and Cal[Year] -- row 13
alone, 16 -- while REMOVEFILTERS(Cal[Bit]) and REMOVEFILTERS(P) leave 172; a filter written on F
in the CALCULATE (F[pk] = 1, or KEEPFILTERS of it) lets the whole F filter reach Cal: BLANK. The
first draft of this fix skipped the row's filters whenever any part of Cal's expansion was left
and whatever was written on F, and answered 172, 114 and 104 there (0.9.147: 16, BLANK, BLANK).

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, batteries B256
(build_b256.py: d_both_rowctx, d_both_rowctx_count) and B256b (build_b256b.py: r_*), over the
model of tests/test_issue250_userelationship_date_part.py; 172 is the sum of the twelve order
days' bits (rows 10 and 11 order off the calendar), 251 the ship days'.
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

UR = "USERELATIONSHIP(F[Ship], Cal[Date])"
XB = "CROSSFILTER(F[Ship], Cal[Date], Both)"
PROBES = {
    "d_both_rowctx": ("SUMX(F, [XShipBits])", 172),
    "d_both_rowctx_count": (f"SUMX(F, CALCULATE(COUNTROWS(Cal), {UR}, {XB}))", 12),
    "r_ur_xb_inline": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}))", 172),
    "r_ur_xb_outer": (f"SUMX(F, CALCULATE(CALCULATE(SUM(Cal[Bit]), {UR}, {XB})))", 172),
    "r_filter_ur_xb": (f"SUMX(FILTER(F, F[v] > 0), CALCULATE(SUM(Cal[Bit]), {UR}, {XB}))", 172),
    "r_ur_xb_rmcal": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}, REMOVEFILTERS(Cal)))", 251),
    "r_ids_ur_xb": (f"SUMX(VALUES(F[Id]), CALCULATE(SUM(Cal[Bit]), {UR}, {XB}))", 251),
    "r_cfboth_bits": ("SUMX(F, CALCULATE(SUM(Cal[Bit]), CROSSFILTER(F[Order], Cal[Date], Both)))", 172),
    "r_plain_bits": ("SUMX(F, CALCULATE(SUM(Cal[Bit])))", 172),
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
    got = evaluate(name)
    assert got == PROBES[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_all_probes_in_one_cache(order, path):
    names = list(PROBES) if order == "forward" else list(reversed(PROBES))
    got = [(n, evaluate(n)) for n in names]
    assert [(n, g) for n, g in got if g != PROBES[n][1]] == []


# Battery B256c (build_b256c.py), Desktop 2.152: what else the CALCULATE holds.
PROBES_T = {
    "t_pk_inside": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}, F[pk] = 1))", None),
    "t_keep_pk": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}, KEEPFILTERS(F[pk] = 1)))", None),
    "t_rm_bit": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}, REMOVEFILTERS(Cal[Bit])))", 172),
    "t_rm_date": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}, REMOVEFILTERS(Cal[Date])))", 16),
    "t_rm_p": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}, REMOVEFILTERS(P)))", 172),
    "t_year_filter": ("SUMX(F, CALCULATE(SUM(Cal[Bit]), Cal[Year] = 2024))", 168),
    "t_count_rm_bit": (f"SUMX(F, CALCULATE(COUNTROWS(F), {UR}, REMOVEFILTERS(Cal[Bit])))", 1),
    "t_count_rm_date": (f"SUMX(F, CALCULATE(COUNTROWS(F), {UR}, REMOVEFILTERS(Cal[Date])))", 1),
    "t_count_order_kept": (f"SUMX(F, CALCULATE(COUNTROWS(F), {UR}, ALL(F[Order])))", 1),
}
T_MEASURES = {**ALL_MEASURES, **{k: e for k, (e, _w) in PROBES_T.items()}}


@pytest.mark.parametrize("name", list(PROBES_T))
def test_what_else_the_calculate_holds(name, path):
    got = de.evaluate_measure(name, TABLES, T_MEASURES, {}, relationships=RELS)
    assert got == PROBES_T[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_what_else_in_one_cache(order, path):
    names = list(PROBES_T) + list(PROBES)
    if order == "reverse":
        names.reverse()
    want = {**PROBES, **PROBES_T}
    got = [(n, de.evaluate_measure(n, TABLES, T_MEASURES, {}, relationships=RELS)) for n in names]
    assert [(n, g) for n, g in got if g != want[n][1]] == []


# Battery B256d (build_b256d.py), Desktop 2.152: a filter the CALCULATE WRITES on another part of
# the expansion -- P, or Cal[Bit], also through KEEPFILTERS -- lets the whole F filter reach Cal
# as F[pk] = 1 does (t_pk_inside): with P[x] = 1 only the rows with pk 1 are left and none of them
# ships the day it was ordered (BLANK); with Cal[Bit] >= 1 the row meets its order day through
# F[Ship] for row 13 alone (16). REMOVEFILTERS of such a column does not (t_rm_bit, t_rm_p: 172).
# The first fix of this issue kept the expansion holding Cal: 114, 172, 172 (0.9.147: BLANK, 16, 16).
PROBES_W = {
    "t_p_written": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}, P[x] = 1))", None),
    "t_keep_bit": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}, KEEPFILTERS(Cal[Bit] >= 1)))", 16),
    "t_cal_bit_written": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}, {XB}, Cal[Bit] >= 1))", 16),
}
W_MEASURES = {**T_MEASURES, **{k: e for k, (e, _w) in PROBES_W.items()}}


@pytest.mark.parametrize("name", list(PROBES_W))
def test_a_filter_written_on_the_expansion(name, path):
    got = de.evaluate_measure(name, TABLES, W_MEASURES, {}, relationships=RELS)
    assert got == PROBES_W[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_written_and_removed_in_one_cache(order, path):
    want = {**PROBES, **PROBES_T, **PROBES_W}
    names = list(want) if order == "forward" else list(reversed(want))
    got = [(n, de.evaluate_measure(n, TABLES, W_MEASURES, {}, relationships=RELS)) for n in names]
    assert [(n, g) for n, g in got if g != want[n][1]] == []
