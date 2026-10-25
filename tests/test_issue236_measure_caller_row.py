"""Issue #236: a measure's body read its caller's row.

A measure reference is a context transition: inside the measure, the
caller's row filters the model, but it is no row context -- its cells
cannot be read. The engine applied the filters and kept the caller's row
bound as the body's current row, so a body could read the caller's columns
directly (a bare [v], a qualified reference through the #116 walk, EARLIER),
including a virtual table's column that no filter carries.

Expected values: Power BI Desktop 2.152 over ADOMD (battery build_b234.py):
measure mrow = [v] + 0, called as SUMX(SELECTCOLUMNS(GENERATESERIES(1, 3),
"v", [Value]), [mrow]), is an error ("The value for v cannot be determined.
Either the column doesn't exist, or there is no current row for this
column."); 0.9.146 answered 6. The other cases are derived from the same
rule: the caller's filters still apply (one region's sum per Dim row), the
body's own row contexts still work, and EARLIER inside the body finds no
outer row of the caller's.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

T3 = 'SELECTCOLUMNS(GENERATESERIES(1, 3), "v", [Value])'
TABLES = {
    "TA": {"columns": ["A"], "rows": [[1], [2], [4]]},
    "Fact": {"columns": ["Region", "Value"], "rows": [["N", 10], ["S", 20], ["N", 5]]},
    "Dim": {"columns": ["Region", "Name"], "rows": [["N", "North"], ["S", "South"]]},
}
RELS = [{"FromTable": "Fact", "FromColumn": "Region", "ToTable": "Dim", "ToColumn": "Region", "IsActive": 1}]
MEASURES = {
    "mrow": "[v] + 0",
    "mf": "SUM(Fact[Value])",
    "mbody": "SUMX(GENERATESERIES(1, 2), [Value])",
    "mearlier": "COUNTROWS(FILTER(Fact, Fact[Value] < EARLIER(Fact[Value])))",
}


def _ev(expr, home=True):
    de._engine.eval_errors.clear()
    measures = dict(MEASURES, m=expr)
    homes = {k: "TA" for k in measures} if home else None
    return de.evaluate_measures_batch(["m"], TABLES, measures, relationships=RELS,
                                      measure_tables=homes)["m"]


@pytest.mark.parametrize("home", [True, False], ids=["home_tables", "no_home_tables"])
def test_matches_desktop(home):
    # b_measure_caller_row: Desktop refuses it; the body's [v] is no column
    # of any row context it has
    assert _ev(f"SUMX({T3}, [mrow])", home) is None
    assert "cannot be found" in de._engine.eval_errors.get("mrow", "")


def test_the_measure_alone_is_an_error_too():
    assert _ev("[mrow]") is None
    assert "cannot be found" in de._engine.eval_errors.get("mrow", "")


def test_the_callers_filters_still_apply():
    assert _ev("SUMX(Dim, [mf])") == 35
    assert _ev("SUMX(VALUES(Dim[Region]), [mf])") == 35
    assert _ev("CONCATENATEX(Dim, [mf], \",\", Dim[Region], ASC)") == "15,20"


def test_the_bodys_own_row_contexts_still_work():
    # (1 + 2) once per caller row
    assert _ev(f"SUMX({T3}, [mbody])") == 9
    assert "m" not in de._engine.eval_errors and "mbody" not in de._engine.eval_errors


def test_earlier_in_the_body_finds_no_caller_row():
    # the body's only row context is its FILTER's; 0.9.146 read the caller's
    # Fact row as an earlier one
    assert _ev("SUMX(Fact, [mearlier])") is None
    assert "earlier row context" in de._engine.eval_errors.get("mearlier", "")


def test_the_callers_row_is_bound_again_after_the_call():
    # the reference clears the row for the body only: [v] after it is the row's
    assert _ev(f"SUMX({T3}, [mf] * 0 + [v])") == 6
    assert _ev(f"SUMX({T3}, [v] + [mf] * 0 + [v])") == 12
