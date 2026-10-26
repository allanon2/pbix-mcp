"""Issue #252 (the window functions' part): ORDERBY past 2**53.

The window functions sorted by DAXEngine._win_ord, which keyed a number by
float(v), so Int64 values past 2**53 that share a double tied: 2**53 and
2**53 + 1, 2**53 + 2 and 2**53 + 3. Power BI Desktop 2.152 orders them one by
one. _win_ord now compares numbers exactly; a plain date is the moment it
starts, as a datetime is (it sorted as text, after every number and
datetime).

Expected values are Desktop's answers by probe id -- build_b257.py (w_*)
and build_b257b.py (group G: wi_*, wo_*, ww_col) over the same tables;
labels a..f are 2**53 - 1 .. 2**53 + 4, TKR holds them in reverse, TKD is
the one side of FK -> TKD -- and, for the dates, the calendar.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

P = 2 ** 53
K6 = [["a", P - 1], ["b", P], ["c", P + 1], ["d", P + 2], ["e", P + 3], ["f", P + 4]]
TABLES = {
    "TK": {"columns": ["L", "K"], "rows": [list(r) for r in K6]},
    "TKR": {"columns": ["L", "K"], "rows": [list(r) for r in reversed(K6)]},
    "TKD": {"columns": ["L", "K"], "rows": [list(K6[i]) for i in (3, 0, 5, 1, 4, 2)]},
    "FK": {"columns": ["K", "V"], "rows": [[k, i + 1] for i, (_l, k) in enumerate(K6)]},
    # hand-built: a datetime among plain dates
    "TDT": {"columns": ["L", "d"],
            "rows": [["x", datetime(2024, 1, 3)], ["y", date(2023, 12, 31)],
                     ["z", date(2024, 1, 2)]]},
}
RELS = [{"FromTable": "FK", "FromColumn": "K", "ToTable": "TKD", "ToColumn": "K",
         "IsActive": 1}]


def _ev(expr):
    de._engine.eval_errors.clear()
    got = de.evaluate_measures_batch(["m"], TABLES, {"m": expr}, relationships=RELS,
                                     measure_tables={"m": "TK"})
    return got["m"], de._engine.eval_errors.get("m")


@pytest.mark.parametrize("expr,want", [
    ('CONCATENATEX(TKR, RANK(DENSE, ALL(TKR), ORDERBY(TKR[K])), ",", TKR[L], ASC)',
     "1,2,3,4,5,6"),                                                    # w_rank_dense_r
    ('CONCATENATEX(TKR, RANK(DENSE, ALL(TKR[K]), ORDERBY(TKR[K])), ",", TKR[L], ASC)',
     "1,2,3,4,5,6"),                                                    # w_rank_col_r
    ('CONCATENATEX(TKR, ROWNUMBER(ALL(TKR[K]), ORDERBY(TKR[K])), ",", TKR[L], ASC)',
     "1,2,3,4,5,6"),                                                    # w_rownum_col_r
    ('CONCATENATEX(TKR, ROWNUMBER(ALL(TKR), ORDERBY(TKR[K], ASC, TKR[L], DESC)), ",", '
     'TKR[L], ASC)', "1,2,3,4,5,6"),                                    # w_rownum_r
    ("MAXX(INDEX(1, ALL(TKR[K]), ORDERBY(TKR[K], DESC)), TKR[K]) - 9007199254740992",
     4),                                                                # w_index_col_r
    ('CONCATENATEX(TKR, VAR p = MAXX(OFFSET(-1, ALL(TKR[K]), ORDERBY(TKR[K])), TKR[K]) '
     'RETURN IF(ISBLANK(p), "-", p - 9007199254740991), ",", TKR[L], ASC)',
     "-,0,1,2,3,4"),                                                    # w_offset_col_r
    ("MAXX(INDEX(2, ALL(TKR[K]), ORDERBY(TKR[K], DESC)), TKR[K]) - 9007199254740991",
     4),                                                                # wi_col_desc
    ("MAXX(INDEX(3, ALL(TKR[K]), ORDERBY(TKR[K], ASC)), TKR[K]) - 9007199254740991",
     2),                                                                # wi_col_asc3
    ('CONCATENATEX(TKR, VAR p = MAXX(OFFSET(1, ALL(TKR[K]), ORDERBY(TKR[K], DESC)), TKR[K]) '
     'RETURN IF(ISBLANK(p), "-", p - 9007199254740991), ",", TKR[L], ASC)',
     "-,0,1,2,3,4"),                                                    # wo_col_next
    ('CONCATENATEX(WINDOW(2, ABS, 3, ABS, ALL(TKR[K]), ORDERBY(TKR[K], DESC)), '
     'TKR[K] - 9007199254740991, ",", TKR[K], ASC)', "3,4"),            # ww_col
    ('CONCATENATEX(INDEX(1, ALL(TKD), ORDERBY(TKD[K], DESC, TKD[L], ASC)), TKD[L], "")',
     "f"),                                                              # wi_keyed
    ('CONCATENATEX(TKD, CONCATENATEX(OFFSET(1, ALL(TKD), ORDERBY(TKD[K], ASC, TKD[L], DESC)), '
     'TKD[L], ""), ",", TKD[L], ASC)', "b,c,d,e,f,"),                   # wo_keyed
])
def test_int64_past_2_53_ordered_exactly(expr, want):
    assert _ev(expr) == (want, None)


def test_a_date_is_the_moment_it_starts():
    # 2023-12-31 < 2024-01-02 < 2024-01-03 00:00, whatever the type
    assert _ev('CONCATENATEX(TDT, ROWNUMBER(ALL(TDT[d]), ORDERBY(TDT[d])), ",", TDT[L], ASC)') \
        == ("3,1,2", None)
    ord_ = de.DAXEngine._win_ord
    assert ord_(date(2023, 12, 31)) < ord_(datetime(2024, 1, 3)) < ord_(date(2024, 1, 4))
    assert ord_(P + 1) > ord_(P) and ord_(P) == ord_(float(P))
