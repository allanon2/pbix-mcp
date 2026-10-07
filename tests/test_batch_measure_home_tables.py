"""evaluate_measures_batch resolves a bare [Column] through the measure's home table.

pbix_evaluate_dax_grouped and _per_dimension evaluate measures the bucketing fast
path cannot handle through evaluate_measures_batch, which never received
measure_tables, so a measure such as AVERAGE([ProductRevenue]) -- whose column
name several tables share -- was BLANK for every group, with success: true.
pbix_evaluate_dax resolved it. On Microsoft's MIT Revenue Opportunities sample
grouped by Account[Region], Avg Revenue and Factored Revenue were None in all
groups; they now equal pbix_evaluate_dax per group (Central: 4,306,652.857 and
153,419,464), which Power BI Desktop 2.157 matched cell by cell.
"""
from __future__ import annotations

from pbix_mcp.dax import engine as de

TABLES = {
    "Fact A": {"columns": ["Region", "Revenue"], "rows": [["N", 10.0], ["S", 20.0]]},
    "Fact B": {"columns": ["Region", "Revenue"], "rows": [["N", 1000.0]]},
}
MEASURES = {"Avg A": "AVERAGE([Revenue])", "Sum B": "SUM([Revenue])"}
HOME = {"Avg A": "Fact A", "Sum B": "Fact B"}


def test_bare_columns_resolve_through_the_home_table():
    out = de.evaluate_measures_batch(["Avg A", "Sum B"], TABLES, MEASURES, {}, measure_tables=HOME)
    assert out == {"Avg A": 15.0, "Sum B": 1000.0}


def test_batch_and_smart_agree():
    batch = de.evaluate_measures_batch(["Avg A", "Sum B"], TABLES, MEASURES, {"Fact A.Region": ["N"]},
                                       measure_tables=HOME)
    smart = de.evaluate_measures_smart(["Avg A", "Sum B"], TABLES, MEASURES, {"Fact A.Region": ["N"]},
                                       simulate_row_context=False, measure_tables=HOME)
    assert batch == smart
