"""#267: IFERROR / ISERROR around an iteration caught an error
raised in one of its rows; Power BI Desktop fails the query.

_eval_catching caught every exception its argument raised. Desktop catches a
row's error only inside that row: IFERROR(SUMX(TM, MOD(TM[n], TM[d])), -1)
fails where 0.9.147 answered -1, for every kind of iteration (SUMX, FILTER,
ADDCOLUMNS, VALUES, GENERATESERIES, a measure reached from the row, a
CALCULATE boolean filter), and also when the IFERROR sits in an outer row
(r19). Inside the row it catches (r21), and an iteration's infinite value
is still an error to IFERROR (r22).

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v",
[probe]) per probe -- F:/_desktop_oracle/build_b255.py (r5) and
build_b255b.py (r9-r25), over TM(n, d, z) = (7, 2, 1), (7, 0, 0), (-7, 2,
1), (7, BLANK, 0) and TS(s, k) = ("1", 1), ("x", 0), ("3", 1).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]},
    "TS": {"columns": ["s", "k"], "rows": [["1", 1], ["x", 0], ["3", 1]]},
}
HOME = {
    "m_zero": "COUNTROWS(ALL(TM)) - 4",
    "m_mod": "MOD(SELECTEDVALUE(TM[n]), SELECTEDVALUE(TM[d]))",
    "m_and_d": "IF(SELECTEDVALUE(TM[d]) <> 0 && MOD(7, SELECTEDVALUE(TM[d])) = 1, 1)",
}
ERROR = "ERROR"


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {**HOME, "p": dax}))
    if "p" in eng.eval_errors:
        return ERROR
    return got


DESKTOP = [
    ("r5", "IFERROR(SUMX(TM, IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1)), -1)", ERROR),
    ("r9", "IFERROR(SUMX(TM, MOD(TM[n], TM[d])), -1)", ERROR),
    ("r10", "IFERROR(SUMX(GENERATESERIES(-1, 2), IF([Value] <> 0 && MOD(7, [Value]) = 1, 1)), -1)", ERROR),
    ("r11", "IF(ISERROR(SUMX(TM, MOD(TM[n], TM[d]))), 1, 0)", ERROR),
    ("r12", "IFERROR(SUMX(TS, VALUE(TS[s])), -1)", ERROR),
    ("r13", "IFERROR(SUMX(VALUES(TM[d]), MOD(7, TM[d])), -1)", ERROR),
    ("r14", "IFERROR(COUNTROWS(FILTER(TM, MOD(TM[n], TM[d]) = 1)), -1)", ERROR),
    ("r15", 'IFERROR(SUMX(ADDCOLUMNS(TM, "q", MOD(TM[n], TM[d])), [q]), -1)', ERROR),
    ("r16", "IFERROR(SUMX(TM, [m_mod]), -1)", ERROR),
    ("r18", "IFERROR(CALCULATE(COUNTROWS(TM), MOD(TM[n], TM[d]) = 1), -1)", ERROR),
    ("r19", "SUMX(VALUES(TM[z]), IFERROR(CALCULATE(SUMX(TM, MOD(TM[n], TM[d]))), 100))", ERROR),
    ("r20", "IFERROR(MOD(1, [m_zero]), -1)", -1),
    ("r21", "IFERROR(SUMX(TM, IFERROR(MOD(TM[n], TM[d]), 0)), -1)", 2),
    ("r22", "IFERROR(SUMX(TM, TM[n] / TM[d]), -1)", -1),
    ("r24", "IFERROR(COUNTROWS(FILTER(GENERATESERIES(-1, 2), MOD(7, [Value]) = 1)), -1)", ERROR),
    ("r25", "IF(ISERROR(SUMX(TM, IF(TM[d] <> 0 && MOD(TM[n], TM[d]) = 1, 1))), 1, 0)", ERROR),
    # build_b255c.py: SUMMARIZECOLUMNS' groups are rows too (0.9.147 -1); an
    # error in TOPN's N is not caught (r27), one in CONCATENATEX's delimiter,
    # evaluated once, is (r28; the first version of this fix failed it)
    ("r26", 'IFERROR(COUNTROWS(SUMMARIZECOLUMNS(TM[d], "v", [m_and_d])), -1)', ERROR),
    ("r27", "IFERROR(COUNTROWS(TOPN(MOD(1, [m_zero]), TM, TM[n])), -1)", ERROR),
    ("r28", 'IFERROR(LEN(CONCATENATEX(TM, TM[n], FORMAT(MOD(1, [m_zero]), "0"))), -1)', -1),
    # build_b255d.py: RANKX's value and PERCENTILEX's k are evaluated once and
    # caught (r29, r30; rel148 failed both), SAMPLE's N and CONCATENATEX's
    # orderBy are not (r31, r32), nor a SUMMARIZECOLUMNS filter table (r34;
    # rel148 -1), while CONCATENATEX's delimiter inside a row is caught in
    # the row (r33)
    ("r29", "IFERROR(RANKX(ALL(TM), TM[n], MOD(1, [m_zero])), -1)", -1),
    ("r30", "IFERROR(PERCENTILEX.INC(TM, TM[n], MOD(1, [m_zero])), -1)", -1),
    ("r31", "IFERROR(COUNTROWS(SAMPLE(MOD(1, [m_zero]), TM, TM[n])), -1)", ERROR),
    ("r32", 'IFERROR(LEN(CONCATENATEX(TM, TM[n], ",", MOD(1, [m_zero]))), -1)', ERROR),
    ("r34", 'IFERROR(COUNTROWS(SUMMARIZECOLUMNS(TM[d], TREATAS({MOD(1, [m_zero])}, TM[d]), '
            '"v", COUNTROWS(TM))), -1)', ERROR),
    ("r33", 'SUMX(TM, IFERROR(LEN(CONCATENATEX(TM, TM[n], FORMAT(MOD(1, [m_zero]), "0"))), -1))', -4),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _engine(expr) == want


def test_the_delimiter_error_inside_a_row_is_a_row_error():
    """r28's CONCATENATEX inside SUMX's row: the delimiter's error leaves the
    CONCATENATEX catchable, then SUMX's row, where an IFERROR outside does
    not catch it (r9's rule). Inside the row an IFERROR catches it: -1 per
    row of TM, -4."""
    inner = 'LEN(CONCATENATEX(TM, TM[n], FORMAT(MOD(1, [m_zero]), "0")))'
    assert _engine(f"IFERROR(SUMX(TM, {inner}), -1)") == ERROR
    assert _engine(f"SUMX(TM, IFERROR({inner}, -1))") == -4


def test_a_summarizecolumns_filter_error_is_not_caught():
    """build_b255d.py r34: an error in a SUMMARIZECOLUMNS filter table is not
    caught (rel148 and 0.9.147 answered -1). The same table under NONVISUAL
    filters the same way, so its error is no different."""
    filt = "TREATAS({MOD(1, [m_zero])}, TM[d])"
    assert _engine(f'IFERROR(COUNTROWS(SUMMARIZECOLUMNS(TM[d], {filt}, "v", COUNTROWS(TM))), -1)') == ERROR
    assert _engine(f'IFERROR(COUNTROWS(SUMMARIZECOLUMNS(TM[d], NONVISUAL({filt}), '
                   f'"v", COUNTROWS(TM))), -1)') == ERROR
