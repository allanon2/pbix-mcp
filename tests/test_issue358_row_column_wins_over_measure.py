"""#358: a bare [name] that an open row context has as a column reads that
column, not a measure of the same name, whatever the case.

What was wrong: 0.9.149 read the measure whenever its name matched the
reference exactly (SUMX(ADDCOLUMNS(P, "MVAL", 1), [MVAL]) was 100 per row,
SUMX(T, [Amount]) with a measure Amount summed the measure) or matched it in
another case while the row's key did not; only a reference spelled as the
row's key and unlike the measure read the column. The first draft of #355
made the measure win in every case, and the next one let only the current
row's column win. PR #352's description assumed the measure wins; Desktop
refutes it.

Expected values, Power BI Desktop 2.152 (desk.json under
F:\\_tmp_pbix\\pr352\\b358\\ and ...\\b358b\\):
- build_b358.py, P = two rows, measures MVAL = 100 and Kval = 100:
  n_measure_same_case SUMX(ADDCOLUMNS(P, "MVAL", 1), [MVAL]) = 2, and 2 for
  the other-case variants n_measure_other_case / _case2 / _case3
  (0.9.149: 200, 200, 200, 2).
- build_b358b.py, T = (Region, Amount) E 1, W 2, N 3, P two rows, measures
  Amount = 100 and MVAL = 100 on P: t_sumx SUMX(T, [Amount]) 6, t_sumx_case
  (with [amount]) 6, t_filter COUNTROWS(FILTER(T, [Amount] > 1)) 2, t_maxx 3,
  t_values (VALUES(T[Amount])) 6, t_summarize 6, t_selcols
  (SELECTCOLUMNS(T, "Amount", T[Amount] * 10)) 60, t_crossjoin (CROSSJOIN(T,
  P)) 12; inside CALCULATE the measure: t_calc SUMX(T, CALCULATE([Amount]))
  300, a_calc 200; an ENCLOSING row's column under an inner iterator over a
  table without it: a_enclosing SUMX(ADDCOLUMNS(P, "MVAL", 3), SUMX(T,
  [MVAL])) 18; outside any row the measure: m_plain [Amount] 100.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

B358 = ({"P": {"columns": ["z"], "rows": [[1], [2]]}}, {"MVAL": "100", "Kval": "100"})
B358B = ({"T": {"columns": ["Region", "Amount"], "rows": [["E", 1], ["W", 2], ["N", 3]]},
          "P": {"columns": ["z"], "rows": [[1], [2]]}},
         {"Amount": "100", "MVAL": "100"})


def _ev(dax, model):
    tables, measures = model
    return de.DAXEngine()._eval_expr(dax, de.DAXContext(tables, measures, None, None, None, []))


@pytest.mark.parametrize("probe, dax, desktop", [
    ("n_measure_same_case", 'SUMX(ADDCOLUMNS(P, "MVAL", 1), [MVAL])', 2),
    ("n_measure_other_case", 'SUMX(ADDCOLUMNS(P, "mval", 1), [MVAL])', 2),
    ("n_measure_other_case2", 'SUMX(ADDCOLUMNS(P, "KVAL", 1), [kval])', 2),
    ("n_measure_other_case3", 'SUMX(ADDCOLUMNS(P, "kval", 1), [kval])', 2),
])
def test_desktop_b358(probe, dax, desktop):
    assert _ev(dax, B358) == desktop, probe


@pytest.mark.parametrize("probe, dax, desktop", [
    ("t_sumx", "SUMX(T, [Amount])", 6),
    ("t_sumx_case", "SUMX(T, [amount])", 6),
    ("t_filter", "COUNTROWS(FILTER(T, [Amount] > 1))", 2),
    ("t_maxx", "MAXX(T, [Amount])", 3),
    ("t_values", "SUMX(VALUES(T[Amount]), [Amount])", 6),
    ("t_summarize", "SUMX(SUMMARIZE(T, T[Amount]), [Amount])", 6),
    ("t_selcols", 'SUMX(SELECTCOLUMNS(T, "Amount", T[Amount] * 10), [Amount])', 60),
    ("t_crossjoin", "SUMX(CROSSJOIN(T, P), [Amount])", 12),
    ("t_calc", "SUMX(T, CALCULATE([Amount]))", 300),
    ("a_calc", 'SUMX(ADDCOLUMNS(P, "MVAL", 1), CALCULATE([MVAL]))', 200),
    ("a_enclosing", 'SUMX(ADDCOLUMNS(P, "MVAL", 3), SUMX(T, [MVAL]))', 18),
    ("a_plain", 'SUMX(ADDCOLUMNS(P, "MVAL", 1), [MVAL])', 2),
    ("m_plain", "[Amount]", 100),
])
def test_desktop_b358b(probe, dax, desktop):
    assert _ev(dax, B358B) == desktop, probe


def test_rows_of_other_shapes_follow_the_rule():
    # derived from the probes above (not measured themselves): a CALENDAR
    # row's one column, GENERATE's inner column, a FILTER over a virtual table
    assert _ev("SUMX(CALENDAR(DATE(2024, 1, 1), DATE(2024, 1, 2)), [date] * 1)",
               ({}, {"Date": "7"})) == 45292 + 45293
    assert _ev('SUMX(GENERATE(P, ROW("kVal", [z] * 10)), [KVAL])', B358) == 30
    assert _ev('COUNTROWS(FILTER(ADDCOLUMNS(P, "MVAL", [z]), [MVAL] > 1))', B358) == 1
    # a conjunct a measure name alone would make row-free, evaluated once for
    # the scan (#249), reads each row when the row has that column
    assert _ev('COUNTROWS(FILTER(ADDCOLUMNS(P, "MVAL", [z]), [z] > 0 && [MVAL] > 1))', B358) == 1
    assert _ev('COUNTROWS(FILTER(ADDCOLUMNS(P, "mval", [z]), [MVAL] > 1 && [z] > 0))', B358) == 1
    # a row without the column reads the measure
    assert _ev("SUMX(P, [mval])", B358) == 200
