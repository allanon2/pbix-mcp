"""Two table functions a DAX query's own quick queries use, in the engine:

* SELECTCOLUMNS(table, T[C], ...): a column reference with no name keeps
  its name and lineage. The engine read the arguments as name, expression
  pairs, so SELECTCOLUMNS(T, T[A], T[B]) was one column named after T[A]'s
  marker holding T[B], and SELECTCOLUMNS(T, T[A]) no rows at all.
* SUMMARIZECOLUMNS with no column to group by: one row of its expressions,
  or none when they are all BLANK (Microsoft Learn, SUMMARIZECOLUMNS). The
  engine answered no row whatever they gave.

Values computed by hand from the rows below; not yet measured on Desktop.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"Item": {"columns": ["Name", "Qty"],
                   "rows": [["b", 1], ["a", None], ["c", 0], ["d", 2]]}}
MEASURES = {"Rows": "COUNTROWS('Item')", "Total": "SUM('Item'[Qty])"}


def ev(expr: str):
    m = dict(MEASURES, q=expr)
    de._engine.eval_errors.clear()
    out = de.evaluate_measures_smart(["q"], TABLES, m, {}, simulate_row_context=False)["q"]
    return out, dict(de._engine.eval_errors)


def test_unnamed_columns_keep_names_and_lineage():
    got, err = ev("TOCSV(SELECTCOLUMNS('Item', 'Item'[Name], 'Item'[Qty]))")
    assert not err
    assert got.splitlines() == ["'Item'[Name],'Item'[Qty]", "b,1", "a,", "c,0", "d,2"]


def test_one_unnamed_column():
    got, err = ev("COUNTROWS(SELECTCOLUMNS('Item', 'Item'[Name]))")
    assert (got, err) == (4, {})


def test_unnamed_and_named_mixed():
    got, err = ev("TOCSV(SELECTCOLUMNS('Item', \"n\", 'Item'[Name], 'Item'[Qty]))")
    assert not err
    assert got.splitlines()[0] == "[n],'Item'[Qty]"


def test_unnamed_column_read_in_a_row_context():
    got, err = ev("SUMX(SELECTCOLUMNS('Item', 'Item'[Qty]), 'Item'[Qty])")
    assert (got, err) == (3, {})


def test_unnamed_expression_is_refused():
    got, err = ev("COUNTROWS(SELECTCOLUMNS('Item', [Total]))")
    assert got is None and "has no name" in err.get("q", "")


def test_a_name_given_twice_is_refused():
    # two new columns of one name (a name beside a model column of that name
    # is Desktop's Item[Name] and [Name]: unsupported here, #338)
    got, err = ev("COUNTROWS(SELECTCOLUMNS('Item', \"Name\", 1, \"NAME\", 2))")
    assert got is None and "more than once" in err.get("q", "")


def test_summarizecolumns_no_group_is_one_row():
    got, err = ev('COUNTROWS(SUMMARIZECOLUMNS("r", [Rows], "t", [Total]))')
    assert (got, err) == (1, {})
    got, err = ev('MAXX(SUMMARIZECOLUMNS("r", [Rows], "t", [Total]), [t])')
    assert (got, err) == (3, {})


def test_summarizecolumns_no_group_all_blank_is_no_row():
    got, err = ev('ISEMPTY(SUMMARIZECOLUMNS("t", CALCULATE([Total], \'Item\'[Name] = "a")))')
    assert (got, err) == (True, {})


def test_summarizecolumns_no_group_one_blank_keeps_the_row():
    got, err = ev('COUNTROWS(SUMMARIZECOLUMNS("t", CALCULATE([Total], \'Item\'[Name] = "a"), "r", [Rows]))')
    assert (got, err) == (1, {})


def test_summarizecolumns_no_group_under_a_filter_table():
    got, err = ev('MAXX(SUMMARIZECOLUMNS(TREATAS({"b", "c"}, \'Item\'[Name]), "t", [Total]), [t])')
    assert (got, err) == (1, {})     # b and c: 1 + 0; unfiltered it is 3
