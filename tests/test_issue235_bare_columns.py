"""Issue #235: bare [Column] references -- a column argument and a scalar.

A bare [A] was evaluated as a scalar wherever it stood. Given to an
aggregate inside an iterator over another table that has an A, it became
that row's cell (an aggregate of one value is BLANK) or that table's column;
read as a scalar, it saw only the current row's keys, so an enclosing model
table's row and a row of one column (ALL / VALUES of a column) were never
read, and the (table, column) marker left in their place made the measure
BLANK.

Expected values: Power BI Desktop 2.152 over ADOMD (battery build_b234.py),
measures on table TA with TA[A] = 1, 2, 4; TBool[B, A] = (TRUE, 10),
(FALSE, 5); F4[A] = 10, 20; D4 three rows and no column A:

    SUMX(TBool, SUM([A]))                        14  (TA[A] once per TBool row)
    SUMX(VALUES(TBool[A]), SUM([A]))             14  (0.9.146: 30)
    SUMX(F4, SUMX(D4, [A]))                      90  (the outer F4 row's A)
    COUNTROWS(FILTER(ALL(TBool[B]), NOT [B]))     1

A bare COLUMN argument names the measure's home table's column, even inside
an iterator over another table that has one; a bare SCALAR reads the
innermost row context that has the column -- a model table's row too, and a
row of one column. The derived cases apply those two rules to the other
column functions, to deeper nesting and to CROSSJOIN rows; without a home
table that has the column the argument resolves as in 0.9.146.
"""
from __future__ import annotations

import json

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TA": {"columns": ["S", "A"], "rows": [["None", 1], [None, 2], ["x", 4]]},
    "TX": {"columns": ["S"], "rows": [["None"], ["y"]]},
    "TBool": {"columns": ["B", "A"], "rows": [[True, 10], [False, 5]]},
    "F4": {"columns": ["K", "A"], "rows": [[1, 10], [2, 20]]},
    "D4": {"columns": ["K", "N"], "rows": [[1, "a"], [2, "b"], [3, "c"]]},
}


def _ev(expr, home="TA"):
    """``expr`` as a measure on ``home`` (None: no home tables at all)."""
    de._engine.eval_errors.clear()
    homes = {"m": home} if home else None
    return de.evaluate_measures_batch(["m"], TABLES, {"m": expr}, measure_tables=homes)["m"]


DESKTOP = [
    ("b_sum_bare", "SUMX(TBool, SUM([A]))", 14),
    ("b_sum_bare_values", "SUMX(VALUES(TBool[A]), SUM([A]))", 14),
    ("b_nested_outer", "SUMX(F4, SUMX(D4, [A]))", 90),
    ("b_filter_bare_all", "COUNTROWS(FILTER(ALL(TBool[B]), NOT [B]))", 1),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want
    assert "m" not in de._engine.eval_errors


def test_matches_desktop_through_the_tool(tmp_path):
    """The battery's model, built and opened as a file: the measures' home
    table comes from the model, as in Desktop."""
    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    b = PBIXBuilder("b235")
    for name, cols in (("TA", [("S", "String"), ("A", "Int64")]),
                       ("TBool", [("B", "Boolean"), ("A", "Int64")]),
                       ("F4", [("K", "Int64"), ("A", "Int64")]),
                       ("D4", [("K", "Int64"), ("N", "String")])):
        b.add_table(name, [{"name": c, "data_type": t} for c, t in cols],
                    rows=[{c: v for (c, _t), v in zip(cols, r)} for r in TABLES[name]["rows"]])
    for probe, expr, _want in DESKTOP:
        b.add_measure("TA", probe, expr)
    b.add_page("Page 1")
    path = str(tmp_path / "b235.pbix")
    b.save(path)
    alias = "i235_" + tmp_path.name[-6:]
    assert json.loads(S.pbix_open(path, alias))["success"]
    try:
        r = json.loads(S.pbix_evaluate_dax(alias, ",".join(p for p, _e, _w in DESKTOP),
                                           apply_default_filters=False))
        got = {x["name"]: x.get("value") for x in r["results"]}
        assert got == {p: w for p, _e, w in DESKTOP}
    finally:
        S.pbix_close(alias)


# A bare column argument names the home table's column (TA[A] = 1, 2, 4)
# whatever row context is open: once per TBool row.
COLUMN_ARGUMENTS = [
    ("count_values", "SUMX(TBool, COUNTROWS(VALUES([A])))", 6),
    ("distinctcount", "SUMX(TBool, DISTINCTCOUNT([A]))", 6),
    ("max", "SUMX(TBool, MAX([A]))", 8),
    ("min", "SUMX(TBool, MIN([A]))", 2),
    ("count", "SUMX(TBool, COUNT([A]))", 6),
    ("sum_over_f4", "SUMX(F4, SUM([A]))", 14),
    ("sum_over_values", "SUMX(VALUES(F4[A]), SUM([A]))", 14),
]


@pytest.mark.parametrize("probe,expr,want", COLUMN_ARGUMENTS, ids=[p for p, _e, _w in COLUMN_ARGUMENTS])
def test_column_argument_names_the_home_tables_column(probe, expr, want):
    assert _ev(expr) == want


# A bare scalar reads the innermost row context that has the column.
SCALARS = [
    ("current_row", "SUMX(TBool, [A])", 15),
    ("outer_in_arithmetic", "SUMX(F4, SUMX(D4, [A] * 2))", 180),
    ("two_levels_out", "SUMX(F4, SUMX(D4, SUMX(D4, [A])))", 270),
    # TBool's own A, not F4's: 2 * (10 + 5)
    ("innermost_wins", "SUMX(F4, SUMX(TBool, [A]))", 30),
    ("one_column_row", "COUNTROWS(FILTER(VALUES(TBool[A]), [A] > 6))", 1),
    ("one_column_row_true", "COUNTROWS(FILTER(ALL(TBool[B]), [B]))", 1),
    ("one_column_row_text", 'CONCATENATEX(VALUES(TBool[B]), IF([B], "t", "f"), ",", [B], ASC)', "f,t"),
    # rows of a one-column table have that column only: [A] is the F4 row's
    ("past_values_rows", "SUMX(F4, SUMX(VALUES(D4[N]), [A]))", 90),
    ("past_selectcolumns_rows", 'SUMX(F4, SUMX(SELECTCOLUMNS(D4, "n2", D4[N]), [A]))', 90),
    ("past_summarize_rows", "SUMX(F4, SUMX(SUMMARIZE(D4, D4[N]), [A]))", 90),
    # each part of a CROSSJOIN row has its column: (10 + 5) * (1 + 2)
    ("crossjoin_parts", "SUMX(CROSSJOIN(VALUES(TBool[A]), VALUES(F4[K])), [A] * [K])", 45),
    ("crossjoin_parts_swapped", "SUMX(CROSSJOIN(VALUES(F4[K]), VALUES(TBool[A])), [A] * [K])", 45),
]


@pytest.mark.parametrize("probe,expr,want", SCALARS, ids=[p for p, _e, _w in SCALARS])
def test_scalar_reads_the_row_contexts(probe, expr, want):
    assert _ev(expr) == want
    assert "m" not in de._engine.eval_errors


@pytest.mark.parametrize("home", [None, "TX"], ids=["no_home_tables", "home_without_the_column"])
def test_without_a_home_column_the_argument_resolves_as_before(home):
    """No home table, or one without an A (a table of measures): 0.9.146's
    resolution -- the current row's cell for a whole row (an aggregate of
    one value, BLANK), the row's table's column for a row of one column."""
    assert _ev("SUMX(TBool, SUM([A]))", home) is None
    assert _ev("SUMX(VALUES(TBool[A]), SUM([A]))", home) == 30


def test_a_measure_still_wins_over_a_column():
    measures = {"m": "SUMX(TBool, [A])", "A": "100"}
    out = de.evaluate_measures_batch(["m"], TABLES, measures, measure_tables={"m": "TA", "A": "TA"})
    assert out["m"] == 200


def test_unknown_name_is_still_an_error():
    assert _ev("SUMX(TBool, SUM([nope]))") is None
    assert "cannot be found" in de._engine.eval_errors.get("m", "")
