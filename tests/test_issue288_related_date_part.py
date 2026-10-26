"""#288: RELATED over a DatePartOnly relationship joins the keys' date parts.

What was wrong: _related_lookup looked every key up by value (_rel_key), so across a
DatePartOnly relationship (JoinOnDateBehavior 2) a key with a time of day found no row:
SUMX(H, RELATED(Cal3[Bit3])) over the active DatePartOnly H[Order], whose orders carry times,
answered 1 (only the one midnight order), and RELATED through an activated DatePartOnly F[Ship]
110 (the midnight ships only).

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256b
(build_b256b.py), over the model of tests/test_issue250_userelationship_date_part.py:
rl_dp_active 8 (1 + 1 + 2 + 4, every order on its day) and rl_inside_ur 251 (every ship on its
day: the bits of Dec 31, Jan 1 twice, Jan 2 twice, Jan 3 twice, Jan 4 twice, Jan 5 twice and
Dec 30; the ship past the calendar and the blank ship relate to nothing). An activated BY-VALUE
relationship keeps its own join: rl_dp_inside 6 (H[Ship]'s two midnight ships).
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

PROBES = {
    "rl_dp_active": ("SUMX(H, RELATED(Cal3[Bit3]))", 8),
    "rl_inside_ur": ("CALCULATE(SUMX(F, RELATED(Cal[Bit])), USERELATIONSHIP(F[Ship], Cal[Date]))", 251),
    "rl_dp_inside": ("CALCULATE(SUMX(H, RELATED(Cal3[Bit3])), USERELATIONSHIP(H[Ship], Cal3[Date]))", 6),
}


@pytest.mark.parametrize("name", list(PROBES))
def test_related_joins_as_the_relationship_does(name):
    expr, want = PROBES[name]
    de._SHARED_FILTER_CACHES.clear()
    assert de.evaluate_measure(name, TABLES, {**MEASURES, name: expr}, {}, relationships=RELS) == want
