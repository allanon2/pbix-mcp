"""Issue #108: iterating a CROSSJOIN reads every table's columns.

CROSSJOIN merged each row's right part under '_2_'-prefixed keys, so an
iterator's row context could not read the second table's columns: FILTER(...,
Prod[Cat] = "B") counted nothing, CONCATENATEX printed "('Prod', 'Cat')", and a
context transition left that table unfiltered. A FILTER over a CROSSJOIN used
as a CALCULATE filter, or fed to TREATAS, lost the right table too. Each row
now keeps its parts; expected values: Power BI Desktop 2.152 over ADOMD.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "Region": {"columns": ["Region"], "rows": [["N"], ["S"], ["W"]]},
    "Prod": {"columns": ["pk", "Cat"], "rows": [[1, "A"], [2, "B"]]},
    "Sales": {"columns": ["Region", "pk", "v"], "rows": [
        ["N", 1, 1], ["N", 2, 2], ["S", 1, 4], ["W", 2, 8], ["W", 1, 16]]},
}
RELS = [{"FromTable": "Sales", "FromColumn": "Region", "ToTable": "Region", "ToColumn": "Region",
         "IsActive": True, "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1},
        {"FromTable": "Sales", "FromColumn": "pk", "ToTable": "Prod", "ToColumn": "pk",
         "IsActive": True, "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
CJ = "CROSSJOIN(VALUES(Region[Region]), VALUES(Prod[Cat]))"
OR = 'Region[Region] = "S" || Prod[Cat] = "B"'
MEASURES = {
    "s3": "SUM(Sales[v])",
    "cj_n": f"COUNTROWS({CJ})",
    "cj_f2": f'COUNTROWS(FILTER({CJ}, Prod[Cat] = "B"))',
    "cj_or": f"COUNTROWS(FILTER({CJ}, {OR}))",
    "cj_cat": f'CONCATENATEX({CJ}, Region[Region] & Prod[Cat], ",", Region[Region], ASC, Prod[Cat], ASC)',
    "cj_sumx": f"SUMX({CJ}, [s3])",
    "cj_maxx": f"MAXX({CJ}, [s3])",
    "cj_tab": 'COUNTROWS(FILTER(CROSSJOIN(Region, Prod), Prod[Cat] = "B"))',
    "cj_calc": f"CALCULATE([s3], FILTER({CJ}, {OR}))",
    "tcj": f"CALCULATE([s3], TREATAS(FILTER({CJ}, {OR}), Region[Region], Prod[Cat]))",
}


def cell(measure, region=None):
    fc = {"Region.Region": [region]} if region else {}
    return de.evaluate_measures_smart(
        [measure], TABLES, MEASURES, fc, relationships=RELS, simulate_row_context=False,
        group_by={"Region.Region"} if region else None, selected_filters={} if region else None)[measure]


# (measure, grouped Region or None, Desktop 2.152)
CASES = [
    ("cj_n", None, 6), ("cj_f2", None, 3), ("cj_or", None, 4),
    ("cj_cat", None, "NA,NB,SA,SB,WA,WB"), ("cj_sumx", None, 31), ("cj_maxx", None, 16),
    ("cj_tab", None, 3), ("cj_calc", None, 14), ("tcj", None, 14),
    ("cj_n", "N", 2), ("cj_f2", "S", 1), ("cj_or", "S", 2), ("cj_cat", "W", "WA,WB"),
    ("cj_sumx", "W", 24), ("cj_maxx", "W", 16), ("cj_calc", "N", 2), ("cj_calc", "W", 8),
    ("tcj", "N", 2), ("tcj", "S", 4), ("tcj", "W", 8),
]


@pytest.mark.parametrize("measure,region,want", CASES, ids=[f"{m}|{r}" for m, r, _w in CASES])
def test_crossjoin_rows_match_desktop(measure, region, want):
    assert cell(measure, region) == want


def test_a_three_way_crossjoin_keeps_every_part():
    m = {"n": "COUNTROWS(FILTER(CROSSJOIN(VALUES(Region[Region]), VALUES(Prod[Cat]), "
              'VALUES(Prod[pk])), Prod[Cat] = "B" && Region[Region] = "N"))'}
    got = de.evaluate_measures_smart(["n"], TABLES, m, {}, relationships=RELS,
                                     simulate_row_context=False)["n"]
    assert got == 2      # N x B x {1, 2}
