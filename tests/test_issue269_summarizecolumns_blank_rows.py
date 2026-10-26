"""#269: SUMMARIZECOLUMNS kept the rows on which every
expression is BLANK.

_fn_summarizecolumns dropped only the relationship blank group, so
SUMMARIZECOLUMNS(T[c], "s", <a measure BLANK for some groups>) listed those
groups with a BLANK -- and COUNTROWS / MAXX over it counted them. Microsoft
Learn (SUMMARIZECOLUMNS, IGNORE): "Rows for which all expressions not using
IGNORE return BLANK/NULL will be excluded".

Expected values: Power BI Desktop 2.152 over ADOMD, raw EVALUATE
SUMMARIZECOLUMNS queries of F:/_desktop_oracle/build_b118.py (the model below
is that battery's T and D): by_zone|s_in_calc answers one row, (Z1, 4);
by_zone|f_rows_allsel and by_cat|f_rows_allsel answer no row. The docs cases
(one or both of the two T[Cat] groups non-blank) follow the documented rule.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "T": {"columns": ["Region", "Cat", "Amount"],
          "rows": [["North", "A", 10.0], ["North", "B", 20.0], ["South", "A", 30.0], ["South", "B", 15.0]]},
    "D": {"columns": ["Region", "Zone"], "rows": [["North", "Z1"], ["South", "Z2"]]},
}
RELS = [{"FromTable": "T", "FromColumn": "Region", "ToTable": "D", "ToColumn": "Region", "IsActive": 1,
         "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
MEASURES = {
    "Sales": "SUM(T[Amount])",
    "s_in_calc": 'CALCULATE(SUMX(VALUES(T[Cat]), CALCULATE(COUNTROWS(ALLSELECTED(T)))), T[Region] = "North")',
    "f_rows_allsel": "SUMX(FILTER(T, CALCULATE(COUNTROWS(ALLSELECTED(T))) = 4), 1)",
}


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {**MEASURES, "p": dax}, relationships=RELS))
    assert not eng.eval_errors
    return got


@pytest.mark.parametrize("expr,want", [
    ('COUNTROWS(SUMMARIZECOLUMNS(D[Zone], "v", [s_in_calc]))', 1),           # by_zone|s_in_calc
    ('MAXX(SUMMARIZECOLUMNS(D[Zone], "v", [s_in_calc]), [v])', 4),
    ('CONCATENATEX(SUMMARIZECOLUMNS(D[Zone], "v", [s_in_calc]), D[Zone])', "Z1"),
    ('COUNTROWS(SUMMARIZECOLUMNS(D[Zone], "v", [f_rows_allsel]))', None),    # by_zone|f_rows_allsel
    ('COUNTROWS(SUMMARIZECOLUMNS(T[Cat], "v", [f_rows_allsel]))', None),     # by_cat|f_rows_allsel
    ('COUNTROWS(SUMMARIZECOLUMNS(D[Zone], "v", [Sales]))', 2),
], ids=["s_in_calc_rows", "s_in_calc_value", "s_in_calc_zone", "f_rows_allsel_zone",
        "f_rows_allsel_cat", "sales_control"])
def test_matches_desktop(expr, want):
    assert _engine(expr) == want


def test_a_row_is_kept_when_one_expression_is_not_blank():
    """The documented rule, per row: only rows whose every expression is
    BLANK go."""
    assert _engine('COUNTROWS(SUMMARIZECOLUMNS(T[Cat], "a", IF(SELECTEDVALUE(T[Cat]) = "A", 1)))') == 1
    assert _engine('COUNTROWS(SUMMARIZECOLUMNS(T[Cat], "a", IF(SELECTEDVALUE(T[Cat]) = "A", 1), '
                   '"b", IF(SELECTEDVALUE(T[Cat]) = "B", 1)))') == 2
