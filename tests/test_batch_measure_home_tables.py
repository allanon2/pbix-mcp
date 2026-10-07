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


def test_both_tools_resolve_bare_columns_like_desktop(tmp_path):
    """Through the tools themselves (#89). Three tables own a Revenue column;
    each measure's bare [Revenue] is its home table's. Power BI Desktop 2.152
    (SUMMARIZECOLUMNS over ADOMD): N 20 / 1000 / 5 / 50, S 20 / 2000 / 5 / 100.
    0.9.114 answered None for every measure in both tools."""
    import json

    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    b = PBIXBuilder("homecols")
    b.add_table("RD", [{"name": "Region", "data_type": "String"}],
                rows=[{"Region": "N"}, {"Region": "S"}])
    for t, rows in (("FA", [("N", 10.0), ("S", 20.0), ("N", 30.0)]),
                    ("FB", [("N", 1000.0), ("S", 2000.0)])):
        b.add_table(t, [{"name": "Region", "data_type": "String"},
                        {"name": "Revenue", "data_type": "Double"}],
                    rows=[{"Region": r, "Revenue": v} for r, v in rows])
        b.add_relationship(t, "Region", "RD", "Region")
    b.add_table("FC", [{"name": "Revenue", "data_type": "Double"}], rows=[{"Revenue": 5.0}])
    b.add_measure("FA", "Avg A", "AVERAGE([Revenue])")
    b.add_measure("FB", "Sum B", "SUM([Revenue])")
    b.add_measure("FC", "Max C", "MAX([Revenue])")
    b.add_measure("FA", "Ratio", "DIVIDE([Sum B], [Avg A])")
    b.add_page("Page 1")
    path = str(tmp_path / "homecols.pbix")
    b.save(path)
    alias = "home89_" + tmp_path.name[-6:]
    assert json.loads(S.pbix_open(path, alias))["success"]
    try:
        names = "Avg A,Sum B,Max C,Ratio"
        want = {"N": [20.0, 1000.0, 5.0, 50.0], "S": [20.0, 2000.0, 5.0, 100.0]}
        g = json.loads(S.pbix_evaluate_dax_grouped(alias, names, "RD.Region"))
        got = {x["key"]["Region"]: [x["values"][m] for m in names.split(",")]
               for x in g["data"]["groups"]}
        assert got == want
        text = json.loads(S.pbix_evaluate_dax_per_dimension(alias, names, "RD.Region", "{}"))["message"]
        rows = {cells[0]: cells[1:] for cells in (line.split() for line in text.splitlines()[3:])
                if len(cells) > 1}
        assert rows == {"N": ["20.00", "1,000.00", "5.00", "50.00"],
                        "S": ["20.00", "2,000.00", "5.00", "100.00"]}
    finally:
        S._open_files.pop(alias, None)
        S._dax_cache.pop(alias, None)
