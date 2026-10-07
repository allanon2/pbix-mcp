"""Issue #79: pbix_evaluate_dax_grouped ignored marked date tables (#73) and
answered ISINSCOPE FALSE on its own grouped columns (#69).

The grouped tool evaluates through evaluate_measures_batch, which received
neither the model's date-table marking nor the grouping tag ISINSCOPE reads —
both were wired into pbix_evaluate_dax only. Expected values are Power BI
Desktop's (2.152, over ADOMD) for the same shapes and data.
"""
import json

import pytest

from pbix_mcp import server as S
from tests.test_issue78_date_table_rule import build_date_model

pytestmark = pytest.mark.unit


def _sales(b):
    b.add_table("S", [{"name": "Region", "data_type": "String"},
                      {"name": "V", "data_type": "Int64"}],
                rows=[{"Region": r, "V": v} for r, v in
                      (("West", 30), ("East", 70), ("North", 50), ("South", 60))])
    b.add_measure("S", "I_reg", "ISINSCOPE(S[Region])")
    b.add_measure("S", "I_kf", 'CALCULATE(ISINSCOPE(S[Region]), KEEPFILTERS(S[Region] = "West"))')
    b.add_measure("S", "I_rep", 'CALCULATE(ISINSCOPE(S[Region]), S[Region] = "West")')


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("i79") / "grouped.pbix")
    alias = build_date_model(path, extra=_sales)
    yield alias
    S._open_files.pop(alias, None)
    S._dax_cache.pop(alias, None)


def _grouped(alias, measures, group_by):
    r = json.loads(S.pbix_evaluate_dax_grouped(alias, measures, group_by))
    assert r["success"], r
    return {tuple(g["key"].values()): g["values"] for g in r["data"]["groups"]}


def test_isinscope_is_true_for_the_grouped_column(model):
    rows = _grouped(model, "I_reg,I_kf,I_rep", "S.Region")
    assert len(rows) == 4
    for key, vals in rows.items():
        assert vals["I_reg"] is True, key        # grouped: in scope
        assert vals["I_kf"] is True, key         # KEEPFILTERS keeps it
        assert vals["I_rep"] is False, key       # a replacing filter does not


def test_marked_date_table_rule_reaches_the_grouped_tool(model):
    rows = _grouped(model, "DM_le,DU_le,DM_ytd", "DM.Year,DM.Month")
    # Desktop: 'DM'[Date] <= DATE(2024,2,15) on the MARKED table clears Month
    assert [rows[(2024, m)]["DM_le"] for m in (1, 2, 3)] == [616, 616, 616]
    assert [rows[(2024, m)]["DM_ytd"] for m in (1, 2, 3)] == [496, 931, 1427]


def test_unmarked_table_still_intersects_in_the_grouped_tool(model):
    rows = _grouped(model, "DU_le", "DU.Year,DU.Month")
    assert [rows[(2024, m)]["DU_le"] for m in (1, 2, 3)] == [496, 120, None]
