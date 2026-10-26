"""#281: an INACTIVE DatePartOnly relationship decides its one side's blank row by date part.

What was wrong: _find_blank_row_tables walks every relationship, active or not, but
_has_unmatched_keys asked _date_part_joins, which held the ACTIVE relationships' DatePartOnly
joins only, so an inactive DatePartOnly relationship's keys were compared by value. Cal2 / G:
G[Order] -> Cal2[Date] is active and every key matches; G[Ship] -> Cal2[Date] is inactive
DatePartOnly, every ship falls on a calendar day but four carry a time of day. 0.9.147 gave Cal2
a blank row for those four (COUNTROWS(VALUES(Cal2[Date])) 6, the blank row holding G's 45) and
none under USERELATIONSHIP (5), and a window answered differently written inside or beside the
USERELATIONSHIP (47 against 3). Fixed with issue #250 (_relationship_indexes takes the
DatePartOnly joins of every relationship).

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, battery B256
(build_b256.py), probes g_* and h_rows*: Cal2 has no blank row, with or without the
USERELATIONSHIP; the inactive by-value H[Ship], whose timed ships match no Cal3 row, does give
Cal3 one (h_rows 5 over 4 days). Each value is Desktop's answer to the probe of that name.
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import DESKTOP, MEASURES, RELS, TABLES, same

pytestmark = pytest.mark.unit

NAMES = [n for n in DESKTOP if n.startswith('g_')] + ['h_rows', 'h_rows_userel']


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


def evaluate(name):
    return de.evaluate_measure(name, TABLES, MEASURES, {}, relationships=RELS)


def test_the_probes_are_desktops():
    assert DESKTOP['g_rows'] == DESKTOP['g_rows_userel'] == 5
    assert DESKTOP['g_blank_ship'] is None and DESKTOP['g_series_all'] == ',3,4,16,40'


@pytest.mark.parametrize("name", NAMES)
def test_each_probe_on_a_fresh_cache(name, path):
    got = evaluate(name)
    assert same(got, DESKTOP[name]), (name, got, DESKTOP[name])


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_all_probes_in_one_cache(order, path):
    names = NAMES if order == "forward" else list(reversed(NAMES))
    got = [(n, evaluate(n)) for n in names]
    assert [(n, g, DESKTOP[n]) for n, g in got if not same(g, DESKTOP[n])] == []


def test_no_blank_row_is_found_for_cal2():
    ctx = de.DAXContext(TABLES, MEASURES, None, None, {}, RELS)
    de._SHARED_FILTER_CACHES.clear()
    blank = ctx.blank_row_tables()
    assert 'Cal2' not in blank
    assert {'Cal', 'Cal3'} <= blank
