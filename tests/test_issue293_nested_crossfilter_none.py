"""#293: CROSSFILTER(.., None) and USERELATIONSHIP of one relationship in NESTED CALCULATEs.

What was wrong: each modifier rewrote the relationship list of the context it was applied to.
An inner CROSSFILTER(F[Ship], Cal[Date], None) under an outer USERELATIONSHIP(F[Ship],
Cal[Date]) left no active relationship between F and Cal (16383, every row); an inner
USERELATIONSHIP(F[Ship], Cal[Date]) under an outer CROSSFILTER(F[Ship], Cal[Date], None)
activated F[Ship] anyway (16 on 0.9.147, 24 with issue #250's date-part join alone).

The rule, from Desktop: CROSSFILTER None on a relationship wins over USERELATIONSHIP of it at
any depth, and a USERELATIONSHIP that loses has no effect at all -- the relationship it would
have deactivated stays (or becomes again) the active one. In ONE CALCULATE the two are an error
(#283).

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256b
(build_b256b.py), over the model of tests/test_issue250_userelationship_date_part.py: all three
are 96, the rows F[Order] puts on January 2 (x_nested_none_in, x_nested_ur_in, and x_meas_none
through the [ShipSum] measure).
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

UR = "USERELATIONSHIP(F[Ship], Cal[Date])"
XNONE = "CROSSFILTER(F[Ship], Cal[Date], None)"
J2 = "Cal[Date] = DATE(2024, 1, 2)"
PROBES = {
    "x_nested_none_in": f"CALCULATE(CALCULATE(SUM(F[v]), {XNONE}), {UR}, {J2})",
    "x_nested_ur_in": f"CALCULATE(CALCULATE(SUM(F[v]), {UR}), {XNONE}, {J2})",
    "x_meas_none": f"CALCULATE([ShipSum], {XNONE}, {J2})",
}


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


@pytest.mark.parametrize("name", list(PROBES))
def test_none_wins_at_any_depth(name, path):
    got = de.evaluate_measure(name, TABLES, {**MEASURES, **PROBES}, {}, relationships=RELS)
    assert got == 96, (name, got)


# Battery B256c (build_b256c.py), Desktop 2.152: deeper nesting. A CROSSFILTER with another
# direction takes an enclosing None back (n_none_both 96: F[Order] filters again), also beside a
# USERELATIONSHIP of the same relationship in its CALCULATE (n_none_ur_both 24: F[Ship] joins by
# date part), while a USERELATIONSHIP alone does not (n_none_uo 16383: F[Order] stays off). A None
# undoing an outer USERELATIONSHIP gives back what that one deactivated (n_ur_uo_none 24).
# 0.9.147: 16383, 16383, 16383, 24, 96; the first draft of this fix: 24, 16383, 16383, 96, 16383.
OBOTH = "CROSSFILTER(F[Order], Cal[Date], Both)"
ONONE = "CROSSFILTER(F[Order], Cal[Date], None)"
UO = "USERELATIONSHIP(F[Order], Cal[Date])"
XB = "CROSSFILTER(F[Ship], Cal[Date], Both)"
DEEPER = {
    "n_ur_uo_none": (f"CALCULATE(CALCULATE(CALCULATE(SUM(F[v]), {ONONE}), {UO}), {UR}, {J2})", 24),
    "n_none_both": (f"CALCULATE(CALCULATE(SUM(F[v]), {OBOTH}), {ONONE}, {J2})", 96),
    "n_both_none": (f"CALCULATE(CALCULATE(SUM(F[v]), {ONONE}), {OBOTH}, {J2})", 16383),
    "n_none_ur_both": (f"CALCULATE(CALCULATE(SUM(F[v]), {UR}, {XB}), {XNONE}, {J2})", 24),
    "n_none_uo": (f"CALCULATE(CALCULATE(SUM(F[v]), {UO}), {ONONE}, {J2})", 16383),
    # the CALCULATE's own order does not matter (exact arithmetic: F[Ship]'s January 2 rows)
    "n_none_both_ur": (f"CALCULATE(CALCULATE(SUM(F[v]), {XB}, {UR}), {XNONE}, {J2})", 24),
}
DEEP_MEASURES = {**MEASURES, **PROBES, **{k: e for k, (e, _w) in DEEPER.items()}}


@pytest.mark.parametrize("name", list(DEEPER))
def test_deeper_nesting(name, path):
    got = de.evaluate_measure(name, TABLES, DEEP_MEASURES, {}, relationships=RELS)
    assert got == DEEPER[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_deeper_nesting_in_one_cache(order, path):
    want = {**{k: 96 for k in PROBES}, **{k: w for k, (_e, w) in DEEPER.items()}}
    names = list(want) if order == "forward" else list(reversed(want))
    got = [(n, de.evaluate_measure(n, TABLES, DEEP_MEASURES, {}, relationships=RELS)) for n in names]
    assert [(n, g) for n, g in got if g != want[n]] == []


# Battery B256d (build_b256d.py), Desktop 2.152: an inner Both after a None. On the inactive
# F[Ship] with no USERELATIONSHIP it stays inactive (n_none_both_ship 96, F[Order]'s rows); after a
# None that undid an outer USERELATIONSHIP(F[Ship]) the Both is that USERELATIONSHIP again and
# F[Order], which the None had brought back, is off once more (n_ur_none_both 24, F[Ship]'s rows by
# date part; the first fix of this issue refused to revive F[Ship] while F[Order] was active: 96;
# 0.9.147: 16383). n_ur_none_both_meas is the same through a measure reference.
REVIVE = {
    "n_none_both_ship": (f"CALCULATE(CALCULATE(SUM(F[v]), {XB}), {XNONE}, {J2})", 96),
    "n_ur_none_both": (f"CALCULATE(CALCULATE(CALCULATE(SUM(F[v]), {XB}), {XNONE}), {UR}, {J2})", 24),
    "n_ur_none_both_meas": (f"CALCULATE([ShipNoneBoth], {UR}, {J2})", 24),
}
REVIVE_MEASURES = {**DEEP_MEASURES, "ShipNoneBoth": f"CALCULATE(CALCULATE(SUM(F[v]), {XB}), {XNONE})",
                   **{k: e for k, (e, _w) in REVIVE.items()}}


@pytest.mark.parametrize("name", list(REVIVE))
def test_both_after_none(name, path):
    got = de.evaluate_measure(name, TABLES, REVIVE_MEASURES, {}, relationships=RELS)
    assert got == REVIVE[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_both_after_none_in_one_cache(order, path):
    want = {**{k: 96 for k in PROBES}, **{k: w for k, (_e, w) in DEEPER.items()},
            **{k: w for k, (_e, w) in REVIVE.items()}}
    names = list(want) if order == "forward" else list(reversed(want))
    got = [(n, de.evaluate_measure(n, TABLES, REVIVE_MEASURES, {}, relationships=RELS)) for n in names]
    assert [(n, g) for n, g in got if g != want[n]] == []
