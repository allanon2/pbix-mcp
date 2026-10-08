"""TREATAS onto several columns: a filter on their combinations.

The engine evaluated a row constructor ("a", "b") as BLANK and kept only
TREATAS's first target column, so CALCULATE(m, TREATAS({("North", "Art")},
Region[Region], Product[Category])) answered BLANK. Measured against Power BI
Desktop 2.157 (a small synthetic model): the combinations filter the fact
through both relationships; plain TREATAS replaces the filters on its columns,
KEEPFILTERS intersects; a dimension sees only its own column's projection.
"""
from __future__ import annotations

from pbix_mcp.dax import engine as de

TABLES = {
    "Region": {"columns": ["Region"], "rows": [["North"], ["South"], ["West"]]},
    "Product": {"columns": ["PK", "Category"], "rows": [[1, "Art"], [2, "Tools"]]},
    "Sales": {"columns": ["Region", "PK", "v"], "rows": [
        ["North", 1, 1.0], ["North", 2, 2.0], ["South", 1, 4.0], ["West", 2, 8.0], ["West", 1, 16.0]]},
}
RELS = [{"FromTable": "Sales", "FromColumn": "Region", "ToTable": "Region", "ToColumn": "Region", "IsActive": True},
        {"FromTable": "Sales", "FromColumn": "PK", "ToTable": "Product", "ToColumn": "PK", "IsActive": True}]
PAIRS = '{("North", "Art"), ("West", "Tools")}'


def _ev(expr, filters=None):
    return de.evaluate_measures_smart(["M"], TABLES, {"M": expr}, filters or {}, relationships=RELS,
                                      simulate_row_context=False)["M"]


def test_combinations_filter_the_fact_through_both_relationships():
    assert _ev(f"CALCULATE(SUM(Sales[v]), TREATAS({PAIRS}, Region[Region], Product[Category]))") == 9.0


def test_plain_treatas_replaces_and_keepfilters_intersects():
    plain = f"CALCULATE(SUM(Sales[v]), TREATAS({PAIRS}, Region[Region], Product[Category]))"
    keep = f"CALCULATE(SUM(Sales[v]), KEEPFILTERS(TREATAS({PAIRS}, Region[Region], Product[Category])))"
    assert _ev(plain, {"Region.Region": ["South"]}) == 9.0
    assert _ev(keep, {"Region.Region": ["North"]}) == 1.0
    assert _ev(keep, {"Region.Region": ["South"]}) is None


def test_one_table_tuple_and_a_dimension_sees_its_projection():
    assert _ev('CALCULATE(SUM(Sales[v]), TREATAS({(1, "Art")}, Product[PK], Product[Category]))') == 21.0
    assert _ev(f"CALCULATE(COUNTROWS(Region), TREATAS({PAIRS}, Region[Region], Product[Category]))") == 2


def test_a_row_of_the_wrong_width_is_an_error_not_a_blank():
    r = _ev('CALCULATE(SUM(Sales[v]), TREATAS({("North", "Art", 1)}, Region[Region], Product[Category]))')
    assert r is None and de._engine.eval_errors
