"""#263: TREATAS used as a table.

TREATAS answered only the filter CALCULATE applies, which no reader of a
table reads a row of. Power BI Desktop 2.152 reads the rows of a TREATAS
relation as rows of its target columns: INDEX(1, TREATAS(VALUES(Dim[Region]),
S[Reg]), ORDERBY(S[Reg])) gives the row whose S[Reg] is 'N', and so does
INDEX(1, TREATAS(SELECTCOLUMNS(ALL(S), "c", S[Reg]), Dim[Region]),
ORDERBY(Dim[Region])) for Dim[Region] (neither is refused as "may have
duplicate rows", #247). 0.9.147 answered BLANK and ''. COUNTROWS,
CONCATENATEX, SUMX and FILTER over a TREATAS read no row either: BLANK, and a
variable holding a TREATAS filtered nothing as a CALCULATE filter. Over the
one-column SELECTCOLUMNS, whose rows carry no single-column value, TREATAS
returned the SELECTCOLUMNS rows unchanged, with S[Reg]'s lineage -- as a
CALCULATE filter too, so that CALCULATE(SUM(Fact[Value]), TREATAS(
SELECTCOLUMNS(ALL(S), "c", S[Reg]), Dim[Region])) filtered nothing: 42,
every Fact row, where the regions N and S keep 10 + 20 + 5 = 35, as
TREATAS(VALUES(S[Reg]), Dim[Region]) does.

As a table, TREATAS keeps repeated values and drops the values its target
column lacks (build_b253d.py): the first implementation of this fix kept
both, answering COUNTROWS(TREATAS({"X", "N"}, Dim[Region])) 2 where Desktop
answers 1, the last S[Reg] of TREATAS(VALUES(Dim[Region]), S[Reg]) 'W' where
Desktop answers 'S', and a SUMX over S[V]'s values as Fact[Value] 180 where
Desktop answers BLANK.

Expected values are Desktop's answers by probe id: build_b253c.py
(d_treatas_set, d_treatas_bag_cols), build_b253d.py (e_treatas_count_missing,
e_treatas_count_rep, f_treatas_*; Desktop answers
F:\\_tmp_pbix\\rel148\\desk\\desk_b253d.json), and exact arithmetic over the model:
S[Reg] holds N, N, S, S, S[V] 30, 35, 40, 75, Dim[Region] N, S, W, Fact
(Region, Value) N 10, S 20, N 5, W 7.
"""
from __future__ import annotations

import pytest

from tests.test_issue247_window_duplicates import ev, ix

pytestmark = pytest.mark.unit

SELCOLS = 'TREATAS(SELECTCOLUMNS(ALL(S), "c", S[Reg]), Dim[Region])'
VALUES_S = "TREATAS(VALUES(S[Reg]), Dim[Region])"


@pytest.mark.parametrize("expr", [
    ix("TREATAS(VALUES(Dim[Region]), S[Reg])", "S[Reg]", "S[Reg]"),          # d_treatas_set
    ix(SELCOLS, "Dim[Region]", "Dim[Region]"),                              # d_treatas_bag_cols
], ids=["d_treatas_set", "d_treatas_bag_cols"])
def test_a_treatas_relation_has_the_rows_of_its_target_columns(expr):
    assert ev(expr) == ("N", None)


@pytest.mark.parametrize("table", [
    'SELECTCOLUMNS(ALL(S), "c", S[Reg])',
    "VALUES(S[Reg])",
])
def test_treatas_of_a_one_column_table_filters_its_target(table):
    # S[Reg] holds N and S: the Fact rows of N and S, 10 + 20 + 5
    assert ev(f"CALCULATE(SUM(Fact[Value]), TREATAS({table}, Dim[Region]))") == (35, None)


@pytest.mark.parametrize("expr,want", [
    # VALUES(S[Reg]) is N, S, both regions of Dim: two rows whatever TREATAS
    # does with values its target lacks or repeats (0.9.147: BLANK, BLANK)
    (f"COUNTROWS({VALUES_S})", 2),
    (f'CONCATENATEX({VALUES_S}, Dim[Region], ",", Dim[Region], ASC)', "N,S"),
    # one row of Dim[Region] and Dim[Name] (0.9.147: BLANK)
    ('COUNTROWS(TREATAS({("N", "North")}, Dim[Region], Dim[Name]))', 1),
    # its rows filter Fact through Dim[Region]: N 10 + 5, S 20 (0.9.147: BLANK)
    (f"SUMX({VALUES_S}, CALCULATE(SUM(Fact[Value])))", 35),
    (f'CALCULATE(SUM(Fact[Value]), FILTER({VALUES_S}, Dim[Region] = "N"))', 15),
    # held in a variable, the table filters as written inline (0.9.147: 42)
    (f"VAR t = {VALUES_S} RETURN CALCULATE(SUM(Fact[Value]), t)", 35),
    # as the filter itself, with a space before its parenthesis (35 before too)
    ("CALCULATE(SUM(Fact[Value]), TREATAS (VALUES(S[Reg]), Dim[Region]))", 35),
], ids=["countrows", "concatenatex", "two_columns", "sumx", "filter", "variable",
        "spaced_filter"])
def test_treatas_as_a_table_has_rows(expr, want):
    assert ev(expr) == (want, None)


def test_treatas_of_a_one_column_selectcolumns_as_a_table():
    # S[Reg]'s N, N, S, S as Dim[Region], repeated values kept: 4 rows
    # (e_treatas_count_rep; the first implementation of this fix answered
    # BLANK, 0.9.147 4) of the regions N, N, S, S (f_treatas_cx_rows; 0.9.147
    # ',,,')
    assert ev(f"COUNTROWS({SELCOLS})") == (4, None)
    assert ev(f'CONCATENATEX({SELCOLS}, Dim[Region], ",", Dim[Region], ASC)') == ("N,N,S,S", None)


@pytest.mark.parametrize("expr,want", [
    # e_treatas_count_missing: "X" is no Dim[Region] (round 3: 2)
    ('COUNTROWS(TREATAS({"X", "N"}, Dim[Region]))', 1),
    # f_treatas_rel_missing: Dim's W is no S[Reg], so the last row is S's
    # (round 3: 'W')
    (ix("TREATAS(VALUES(Dim[Region]), S[Reg])", "S[Reg]", "S[Reg]", pos=-1), "S"),
    # f_treatas_sumx_missing: none of S[V]'s 30, 35, 40, 75 is a Fact[Value]
    # (round 3: 180)
    ('SUMX(TREATAS(SELECTCOLUMNS(ALL(S), "c", S[V]), Fact[Value]), Fact[Value])', None),
    # f_treatas_count_values
    (f"COUNTROWS({VALUES_S})", 2),
], ids=["e_treatas_count_missing", "f_treatas_rel_missing", "f_treatas_sumx_missing",
        "f_treatas_count_values"])
def test_values_the_target_lacks_are_dropped(expr, want):
    assert ev(expr) == (want, None)
