"""#262: PARTITIONBY over columns a table function added.

Power BI Desktop 2.152 refuses a window function whose PARTITIONBY names a
column of a relation built by a table function -- here GENERATESERIES -- with
"The function PARTITIONBY cannot be used with columns added by DAX table
functions." (its Norwegian message, "Funksjonen PARTITIONBY kan ikke brukes
med kolonner som er lagt til av DAX-tabellfunksjoner.", translated). It
reports it as it reports ORDERBY's order argument, a definition error that
names no measure, and before the duplicate-rows refusal a current row would
otherwise bring (#247). 0.9.147 answered '1,2,3'.

build_b253d.py settled the rest: the refusal is a definition error of the
measure, so it fails the measure in an IF branch never taken (a_part_untaken;
the engine answered "ok"), while IFERROR around a reference to such a measure
catches it (a_part_ife_ref) and a reference never taken is no part of the
definition (c_order_ref_untaken, the ORDER error's twin). It comes before
ROWNUMBER's built-columns refusal and OFFSET's duplicate rows (a_part_rn,
a_part_off), and it applies to a column ADDCOLUMNS added to a relation of
model columns too (a_part_addcol; the engine failed it with "Measure or
column '[g]' cannot be found").

Expected values are Desktop's answers by probe id: build_b253c.py
(c_gs_idx_part; Desktop answers F:\\_tmp_pbix\\rel148\\desk\\desk_b253c.json) and
build_b253d.py (group A; F:\\_tmp_pbix\\rel148\\desk\\desk_b253d.json).
"""
from __future__ import annotations

import pytest

from tests.test_issue247_window_duplicates import ev, per_s, refused

pytestmark = pytest.mark.unit

PART_BUILT = "The function PARTITIONBY cannot be used with columns added by DAX table functions."


def test_partitionby_over_generateseries_is_refused():
    # c_gs_idx_part
    err = refused('CONCATENATEX(GENERATESERIES(1, 3), CONCATENATEX(INDEX(1, GENERATESERIES(1, 3), '
                  'ORDERBY([Value]), PARTITIONBY([Value])), [Value]), ",", [Value], ASC)')
    assert PART_BUILT in err, err


def test_partitionby_over_model_columns_is_not():
    # build_b253.py o_allcols_p: OFFSET over ALL(S[Reg], S[Cat]) partitioned by
    # S[Reg], unchanged
    assert ev(per_s("h_o_allcols_p")) == (",A,,C", None)


GS = "GENERATESERIES(1, 3)"


@pytest.mark.parametrize("expr", [
    # a_part_untaken: in an IF branch never taken (0.9.147 and the round-3
    # engine: "ok")
    f'IF(FALSE(), CONCATENATEX(INDEX(1, {GS}, ORDERBY([Value]), PARTITIONBY([Value])), '
    '[Value]), "ok")',
    # a_part_rn: before ROWNUMBER's built-columns refusal
    f'CONCATENATEX({GS}, ROWNUMBER({GS}, ORDERBY([Value]), PARTITIONBY([Value])), ",", '
    '[Value], ASC)',
    # a_part_off: before OFFSET's duplicate rows
    f'CONCATENATEX({GS}, CONCATENATEX(OFFSET(-1, {GS}, ORDERBY([Value]), '
    'PARTITIONBY([Value])), [Value]), ",", [Value], ASC)',
    # a_part_addcol: a column ADDCOLUMNS added beside a model column
    'CONCATENATEX(ALL(S[Cat]), CONCATENATEX(OFFSET(-1, ADDCOLUMNS(ALL(S[Cat]), "g", '
    'IF(S[Cat] < "C", 1, 2)), ORDERBY(S[Cat]), PARTITIONBY([g])), S[Cat]), ",", S[Cat], ASC)',
], ids=["a_part_untaken", "a_part_rn", "a_part_off", "a_part_addcol"])
def test_partitionby_over_table_function_columns_b253d(expr):
    err = refused(expr)
    assert PART_BUILT in err, err


def test_iferror_around_a_reference_catches_it():
    # a_part_ife_ref
    assert ev('IFERROR([h_part_built], "caught")') == ("caught", None)


def test_a_partition_by_a_model_column_in_a_branch_never_taken_is_not():
    # o_allcols_p's call, untaken: nothing to refuse
    assert ev('IF(FALSE(), [h_o_allcols_p], "ok")') == ("ok", None)
