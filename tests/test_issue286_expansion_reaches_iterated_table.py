"""#286: under USERELATIONSHIP a row's expansion reaches its own table through the activated relationship.

What was wrong: SUMX(F, CALCULATE(SUM(F[v]), USERELATIONSHIP(F[Ship], Cal[Date]))). The
transition of each F row puts the row's ORDER day on Cal (its expanded table through F[Order],
issue #115). The engine never carried that Cal filter back to F: it assumed the row's own value
of the join column selects those rows already, which holds while the way back is the way out
(F[Order]). Under the USERELATIONSHIP the way back is F[Ship], and the order day selects the
rows SHIPPED that day -- the row itself only when it was shipped the day it was ordered. 0.9.147
took every row: 16383 (COUNTROWS(F) 14 in sum).

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256b
(build_b256b.py), over the model of tests/test_issue250_userelationship_date_part.py: r_ur_sum
and r_ur_xb_sum 8192 (row 13's v, the only row shipped on its order day), r_ur_count 1. A day
filter replacing the expansion keeps each row shipped that day (r_ur_day_sum 24); KEEPFILTERS of
it meets the order day (r_ur_keepday_sum BLANK). Controls that held already: r_ur_bits 172,
r_p_ur 27, r_cfnone_bits 172, r_inside_bits 251, r_inside_xb_bits 251.
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

UR = "USERELATIONSHIP(F[Ship], Cal[Date])"
XB = "CROSSFILTER(F[Ship], Cal[Date], Both)"
J2 = "Cal[Date] = DATE(2024, 1, 2)"
PROBES = {
    "r_ur_sum": (f"SUMX(F, CALCULATE(SUM(F[v]), {UR}))", 8192),
    "r_ur_count": (f"SUMX(F, CALCULATE(COUNTROWS(F), {UR}))", 1),
    "r_ur_xb_sum": (f"SUMX(F, CALCULATE(SUM(F[v]), {UR}, {XB}))", 8192),
    "r_ur_day_sum": (f"SUMX(F, CALCULATE(SUM(F[v]), {UR}, {J2}))", 24),
    "r_ur_keepday_sum": (f"SUMX(F, CALCULATE(SUM(F[v]), {UR}, KEEPFILTERS({J2})))", None),
    "r_ur_bits": (f"SUMX(F, CALCULATE(SUM(Cal[Bit]), {UR}))", 172),
    "r_p_ur": (f"SUMX(F, CALCULATE(SUM(P[x]), {UR}))", 27),
    "r_cfnone_bits": ("SUMX(F, CALCULATE(SUM(Cal[Bit]), CROSSFILTER(F[Order], Cal[Date], None)))", 172),
    "r_inside_bits": (f"CALCULATE(SUMX(F, CALCULATE(SUM(Cal[Bit]))), {UR})", 251),
    "r_inside_xb_bits": (f"CALCULATE(SUMX(F, CALCULATE(SUM(Cal[Bit]))), {UR}, {XB})", 251),
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
