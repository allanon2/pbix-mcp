"""Issue #225: a bare [column] of an enclosing row context's virtual table.

Inside a nested iterator, a bare [v] that the current row does not hold was
looked up in the current row and then in the model's tables only. A column
that SELECTCOLUMNS / ADDCOLUMNS / GENERATESERIES made for an OUTER row
context is in neither, so the reference raised and the whole measure was
BLANK -- while the same value through VAR x = [v] worked. DAX resolves an
unqualified column against the row contexts from the innermost out.

Expected values: Power BI Desktop 2.152 over ADOMD (the SAMPLE battery,
probes x-rowctx-sumx-bare, x-expr-countrows-asc and
x-expr-countrows-var-asc): SUMX(SELECTCOLUMNS(GENERATESERIES(1, 20), "v",
[Value]), COUNTROWS(FILTER(SELECTCOLUMNS(GENERATESERIES(1, 20), "w",
[Value]), [w] > [v]))) is 190; SAMPLE(5, ..., <that count>, ASC) picks
v = 1, 6, 10, 15, 20, with the count written bare or through VAR x = [v].
The VAR form of the SUMX probe was not measured; it is checked here as a
derived case. The other cases check the same rule on other shapes: a bare
reference reads what VAR x = [v] reads, the innermost row that has the
column wins, a model table's column keeps its old path, and a measure's
body sees none of its caller's rows (the outer row contexts end at a
measure reference, so SUM([Value]) there is the model's column as in
0.9.145, not a table constructor's Value column).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

T20 = 'SELECTCOLUMNS(GENERATESERIES(1, 20), "v", [Value])'
W20 = 'SELECTCOLUMNS(GENERATESERIES(1, 20), "w", [Value])'
TABLES = {
    "D": {"columns": ["x"], "rows": [[1]]},
    "Orders": {"columns": ["Region", "Amount"], "rows": [["N", 10], ["S", 20], ["N", 5]]},
    "Dim": {"columns": ["Region", "Name"], "rows": [["N", "North"], ["S", "South"]]},
    "A": {"columns": ["a"], "rows": [[1], [2]]},
    "B": {"columns": ["b"], "rows": [[10]]},
}
RELS = [{"FromTable": "Orders", "FromColumn": "Region", "ToTable": "Dim", "ToColumn": "Region", "IsActive": 1}]


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr, "M": "SUM(Orders[Amount])"}, relationships=RELS)


DESKTOP = [
    ("x-rowctx-sumx-bare", f"SUMX({T20}, COUNTROWS(FILTER({W20}, [w] > [v])))", 190),
    ("x-expr-countrows-asc",
     f'CONCATENATEX(SAMPLE(5, {T20}, COUNTROWS(FILTER({W20}, [w] > [v])), ASC), [v], ",", [v], ASC)',
     "1,6,10,15,20"),
    ("x-expr-countrows-var-asc",
     f'CONCATENATEX(SAMPLE(5, {T20}, VAR x = [v] RETURN COUNTROWS(FILTER({W20}, [w] > x)), ASC), '
     '[v], ",", [v], ASC)', "1,6,10,15,20"),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


def test_var_form_of_the_sumx_probe():
    # derived, not measured: the VAR spelling of x-rowctx-sumx-bare
    assert _ev(f"SUMX({T20}, VAR x = [v] RETURN COUNTROWS(FILTER({W20}, [w] > x)))") == 190


T3 = 'SELECTCOLUMNS(GENERATESERIES(1, 3), "v", [Value])'
W2 = 'SELECTCOLUMNS(GENERATESERIES(1, 2), "w", [Value])'
# (bare, the same through a VAR)
SAME_AS_VAR = [
    ("filter", f"SUMX({T3}, COUNTROWS(FILTER({W2}, [w] >= [v])))",
     f"SUMX({T3}, VAR x = [v] RETURN COUNTROWS(FILTER({W2}, [w] >= x)))"),
    ("sumx", f"SUMX({T3}, SUMX({W2}, [v] * 10 + [w]))",
     f"SUMX({T3}, VAR x = [v] RETURN SUMX({W2}, x * 10 + [w]))"),
    ("concatenatex", f'CONCATENATEX({T3}, CONCATENATEX({W2}, [v] & ":" & [w], ","), ";")',
     f'CONCATENATEX({T3}, VAR x = [v] RETURN CONCATENATEX({W2}, x & ":" & [w], ","), ";")'),
    ("addcolumns", f'SUMX(ADDCOLUMNS({T3}, "n", COUNTROWS(FILTER({W2}, [w] < [v]))), [n])',
     f'SUMX(ADDCOLUMNS({T3}, "n", VAR x = [v] RETURN COUNTROWS(FILTER({W2}, [w] < x))), [n])'),
    ("three_levels", f"SUMX({T3}, SUMX({W2}, COUNTROWS(FILTER(GENERATESERIES(1, 4), [Value] > [v] + [w]))))",
     f"SUMX({T3}, VAR x = [v] RETURN SUMX({W2}, VAR y = [w] RETURN "
     "COUNTROWS(FILTER(GENERATESERIES(1, 4), [Value] > x + y))))"),
    ("model_inner", 'SUMX(ADDCOLUMNS(Dim, "k", 2), SUMX(FILTER(Orders, Orders[Region] = Dim[Region]), '
                    'Orders[Amount] * [k]))',
     'SUMX(ADDCOLUMNS(Dim, "k", 2), VAR x = [k] RETURN SUMX(FILTER(Orders, Orders[Region] = Dim[Region]), '
     'Orders[Amount] * x))'),
    # SELECTCOLUMNS keeps its source's single-column meta as lineage, not as a column Value
    ("selectcolumns_lineage", 'SUMX({1, 2, 3}, COUNTROWS(FILTER(SELECTCOLUMNS({1, 2}, "w", [Value]), [w] < [Value])))',
     'SUMX({1, 2, 3}, VAR x = [Value] RETURN COUNTROWS(FILTER(SELECTCOLUMNS({1, 2}, "w", [Value]), [w] < x)))'),
    # ADDCOLUMNS over CROSSJOIN puts its column on the joined row, not on a part
    ("crossjoin_extension", f'SUMX(ADDCOLUMNS(CROSSJOIN(A, B), "k", 1), SUMX({W2}, [k]))',
     f'SUMX(ADDCOLUMNS(CROSSJOIN(A, B), "k", 1), VAR x = [k] RETURN SUMX({W2}, x))'),
]


@pytest.mark.parametrize("probe,bare,var", SAME_AS_VAR, ids=[p for p, _b, _v in SAME_AS_VAR])
def test_bare_reads_what_a_var_reads(probe, bare, var):
    want = _ev(var)
    assert want is not None
    assert _ev(bare) == want


def test_values():
    assert _ev(f"SUMX({T3}, COUNTROWS(FILTER({W2}, [w] >= [v])))") == 3
    assert _ev(f'CONCATENATEX({T3}, CONCATENATEX({W2}, [v] & ":" & [w], ","), ";")') == \
        "1:1,1:2;2:1,2:2;3:1,3:2"
    assert _ev('SUMX(ADDCOLUMNS(Dim, "k", 2), SUMX(FILTER(Orders, Orders[Region] = Dim[Region]), '
               'Orders[Amount] * [k]))') == 70


def test_innermost_row_wins():
    inner_v = 'SELECTCOLUMNS(GENERATESERIES(1, 2), "v", [Value] * 100)'
    assert _ev(f"SUMX({T3}, SUMX({inner_v}, [v]))") == 900
    # the CROSSJOIN row's own k (1), not the outermost row's k (100, 200): 2 * 2 * 1 * 2
    outer_k = 'SELECTCOLUMNS(GENERATESERIES(1, 2), "k", [Value] * 100)'
    assert _ev(f'SUMX({outer_k}, SUMX(ADDCOLUMNS(CROSSJOIN(A, B), "k", 1), SUMX({W2}, [k])))') == 8


def test_selectcolumns_lineage_values():
    assert _ev('SUMX({1, 2, 3}, COUNTROWS(FILTER(SELECTCOLUMNS({1, 2}, "w", [Value]), [w] < [Value])))') == 3
    assert _ev(f"SUMX(GENERATESERIES(1, 3), COUNTROWS(FILTER({W2}, [w] < [Value])))") == 3


def test_outer_virtual_column_named_like_a_model_column():
    # Orders has an Amount column; the outer row's own Amount is the one meant
    assert _ev('SUMX(SELECTCOLUMNS(GENERATESERIES(1, 2), "Amount", [Value]), SUMX(Dim, [Amount]))') == 6


def test_model_column_keeps_its_path():
    # SUM takes [Amount] as the column, not the outer row's value: 35 per (order, region)
    assert _ev("SUMX(Orders, SUMX(Dim, SUM([Amount])))") == 210
    assert _ev("SUMX(Orders, COUNTROWS(FILTER(Dim, Dim[Region] = [Region])))") == 6


def test_unknown_name_still_an_error():
    de._engine.eval_errors.clear()
    assert de.evaluate_measure("p", TABLES, {"p": f"SUMX({T3}, SUMX({W2}, [nope]))"}) is None
    assert "cannot be found" in de._engine.eval_errors.get("p", "")


# A measure reference ends the outer row contexts: its body keeps the
# caller's row bound, but none of the body's iterators may read it. With a
# model column Value, SUM([Value]) in the body is Fact[Value] (0.9.145's
# answers); read as the caller's table-constructor / ADDCOLUMNS column it
# was a silent BLANK.
FACT = {
    "Fact": {"columns": ["Region", "Value"], "rows": [["N", 10], ["S", 20], ["N", 5]]},
    "Dim": {"columns": ["Region", "Name"], "rows": [["N", "North"], ["S", "South"]]},
}
FACT_RELS = [{"FromTable": "Fact", "FromColumn": "Region", "ToTable": "Dim", "ToColumn": "Region", "IsActive": 1}]
FACT_MEASURES = {
    "M": "SUM(Fact[Value])",
    "MV": "SUMX(VALUES(Dim[Region]), SUM([Value]))",
    "M4": "SUMX(Dim, SUM([Value]))",
    "MBody": f"SUMX({W2}, [v])",
    # the body's own nested iterators still see the body's outer rows: 1 pair (v 1, w 2)
    "MN": f"SUMX({T3}, COUNTROWS(FILTER({W2}, [w] > [v])))",
}
MEASURE_BOUNDARY = [
    ("constructor", "SUMX({1, 2, 3}, [MV])", 210),
    ("generateseries", "SUMX(GENERATESERIES(1, 3), [MV])", 210),
    ("addcolumns_dim", 'SUMX(ADDCOLUMNS(Dim, "Value", [M]), [M4])', 35),
    ("addcolumns_values", 'SUMX(ADDCOLUMNS(VALUES(Dim[Region]), "Value", [M]), [M4])', 35),
    ("nested_in_body", "SUMX({1, 2}, [MN])", 2),
]


@pytest.mark.parametrize("probe,expr,want", MEASURE_BOUNDARY, ids=[p for p, _e, _w in MEASURE_BOUNDARY])
def test_measure_body_sees_no_caller_row(probe, expr, want):
    measures = dict(FACT_MEASURES, m=expr)
    de._engine.eval_errors.clear()
    assert de.evaluate_measure("m", FACT, measures, relationships=FACT_RELS) == want
    assert "m" not in de._engine.eval_errors


def test_measure_body_cannot_read_the_callers_virtual_column():
    # [v] is no column of the body's own row contexts: the body fails, as on
    # 0.9.145 (it read the caller's v and summed 18 before the walk ended at
    # the measure reference)
    de._engine.eval_errors.clear()
    measures = dict(FACT_MEASURES, m=f"SUMX({T3}, [MBody])")
    assert de.evaluate_measure("m", FACT, measures, relationships=FACT_RELS) is None
    assert "cannot be found" in de._engine.eval_errors.get("MBody", "")
