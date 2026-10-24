"""Issue #223: DISTINCT(column) tells values apart as the column holds them.

DISTINCT(T[C]) removed duplicates by their text, str(v): BLANK and the text
"None" were one value, while the same number stored as 1 and as 1.0 was
two. VALUES(T[C]) already compared the cells themselves, so the two
disagreed on one column.

Expected values: Power BI Desktop 2.152 over ADOMD (battery build_b210.py,
table TA with S = "None", BLANK, "x"): COUNTROWS(DISTINCT(TA[S])) is 3 and
COUNTROWS(VALUES(TA[S])) is 3. The other cases follow from DISTINCT being
VALUES without the blank row a relationship adds (DAX reference). NaN, which
equals nothing, counts once in both, as DISTINCT counted it when it
compared texts; that one is the engine's own rule, not measured on Desktop.
"""
from __future__ import annotations

import math

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TA": {"columns": ["S", "A"], "rows": [["None", 1], [None, 2], ["x", 4], ["None", 8], [None, 16]]},
    # one Double column read from Python data: the same number as 1 and as 1.0
    "TN": {"columns": ["V"], "rows": [[1], [1.0], [2.5], [2], [2.0]]},
    # NaN cells, each its own float object
    "TF": {"columns": ["V"], "rows": [[float("nan")], [float("nan")], [1.0], [float("nan")]]},
    # a dimension with an orphan fact key: VALUES lists the blank row, DISTINCT does not
    "D3": {"columns": ["K", "R"], "rows": [[1, "North"], [2, "South"], [3, "North"]]},
    "F3": {"columns": ["K", "A"], "rows": [[1, 10], [2, 20], [9, 7]]},
}
RELS = [{"FromTable": "F3", "FromColumn": "K", "ToTable": "D3", "ToColumn": "K", "IsActive": 1}]


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr}, relationships=RELS)


DESKTOP = [
    ("a_distinct", "COUNTROWS(DISTINCT(TA[S]))", 3),
    ("a_values", "COUNTROWS(VALUES(TA[S]))", 3),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


def test_blank_and_none_text_stay_apart():
    got = _ev('CONCATENATEX(DISTINCT(TA[S]), IF(ISBLANK(TA[S]), "(blank)", TA[S]), "|")')
    assert got == "None|(blank)|x"          # the order of first appearance


def test_one_number_stored_two_ways_is_one_value():
    assert _ev("COUNTROWS(DISTINCT(TN[V]))") == 3
    assert _ev("COUNTROWS(DISTINCT(TN[V]))") == _ev("COUNTROWS(VALUES(TN[V]))")


def test_nan_counts_once():
    assert _ev("COUNTROWS(DISTINCT(TF[V]))") == 2
    assert _ev("COUNTROWS(VALUES(TF[V]))") == 2


def test_distinct_is_values_without_the_blank_row():
    assert _ev("COUNTROWS(VALUES(D3[R]))") == 3           # North, South and the blank row
    assert _ev("COUNTROWS(DISTINCT(D3[R]))") == 2
    assert _ev("COUNTROWS(DISTINCT(D3[K]))") == 3


def test_distinct_cells_helper():
    nan_a, nan_b = float("nan"), float("nan")
    got = de._distinct_cells([None, "None", nan_a, 1, 1.0, nan_b, "x", None, "01", "1"])
    assert got[:2] == [None, "None"]
    assert got[2] is nan_a and math.isnan(got[2])        # the first NaN stays
    assert got[3:] == [1, "x", "01", "1"]                 # text stays text: '01' is not '1'
    assert de._distinct_cells([]) == []
