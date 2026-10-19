"""Refreshing tables of an open model (pbix_mcp.refresh): the partition's M is evaluated again, its rows load
by the model's column types, calculated columns and the calculated tables built on a refreshed table are
recomputed, and the DAX engine answers from the new rows. Models are built by this package's builder, whose
tables carry Desktop's "Enter data" M."""
import json
import os
import tempfile

import pytest

from pbix_mcp import server
from pbix_mcp.builder import PBIXBuilder, _build_enter_data_m
from pbix_mcp.refresh import RefreshError, _dax_table_names, refresh_tables, unchanged

COLUMNS = [{"name": "Region", "data_type": "String"}, {"name": "Amount", "data_type": "Int64"}]
ROWS = [{"Region": "East", "Amount": 10}, {"Region": "West", "Amount": 5}, {"Region": "East", "Amount": 1}]


@pytest.fixture
def model():
    """An open model: table Sales (Enter-data M) and the measure Total = SUM(Sales[Amount])."""
    b = PBIXBuilder("Refresh")
    b.add_table("Sales", COLUMNS, rows=ROWS)
    b.add_measure("Sales", "Total", "SUM('Sales'[Amount])")
    d = tempfile.mkdtemp()
    path = os.path.join(d, "refresh.pbix")
    with open(path, "wb") as f:
        f.write(b.build())
    alias = f"refresh-{id(path)}"
    assert '"success":true' in server.pbix_open(path, alias)
    yield alias
    server.pbix_close(alias)


def value(alias: str, measure: str = "Total") -> object:
    out = json.loads(server.pbix_evaluate_dax(alias, measure))
    assert out["success"], out
    (result,) = out["results"]
    assert result["status"] == "ok", result
    return result["value"]


def snapshot(alias: str) -> dict:
    return {k: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]}
            for k, v in server._get_dax_context(alias)["tables"].items()}


def set_rows(alias: str, rows: list[dict]) -> None:
    out = json.loads(server.pbix_set_partition_m(alias, "Sales", _build_enter_data_m(COLUMNS, rows)))
    assert out["success"], out


def test_unchanged_sources_change_nothing(model):
    before = snapshot(model)
    result = refresh_tables(model, ["Sales"])
    assert result.tables == {"Sales": 3}
    assert all(unchanged(model, before).values())


def test_new_rows_reach_the_engine(model):
    assert value(model) == 16
    set_rows(model, [{"Region": "North", "Amount": 100}, {"Region": "East", "Amount": 2}])
    assert value(model) == 16                     # the M changed, the data hasn't been refreshed
    refresh_tables(model, ["Sales"])
    assert value(model) == 102
    assert sorted(r[0] for r in server._get_dax_context(model)["tables"]["Sales"]["rows"]) == ["East", "North"]


def test_calculated_columns_are_recomputed(model):
    out = server.pbix_datamodel_add_calculated_column(model, "Sales", "Double", "'Sales'[Amount] * 2", "Int64")
    assert '"success":true' in out, out
    set_rows(model, [{"Region": "North", "Amount": 7}])
    refresh_tables(model, ["Sales"])
    t = server._get_dax_context(model)["tables"]["Sales"]
    assert t["columns"][-1] == "Double" and [r[-1] for r in t["rows"]] == [14]


def test_calculated_tables_on_a_refreshed_table_are_recomputed(model):
    out = server.pbix_datamodel_add_calculated_table(model, "Big", "FILTER('Sales', 'Sales'[Amount] > 4)")
    assert '"success":true' in out, out
    before = snapshot(model)
    assert len(before["Big"]["rows"]) == 2
    set_rows(model, [{"Region": "North", "Amount": 100}, {"Region": "East", "Amount": 2}, {"Region": "W", "Amount": 9}])
    result = refresh_tables(model, ["Sales"])
    assert result.calculated_tables == ["Big"]
    big = server._get_dax_context(model)["tables"]["Big"]
    assert sorted(r[big["columns"].index("Amount")] for r in big["rows"]) == [9, 100]


def test_a_failed_refresh_leaves_the_model_as_it_was(model):
    before = snapshot(model)
    server.pbix_set_partition_m(model, "Sales", 'let x = error "source gone" in x')
    with pytest.raises(RefreshError) as e:
        refresh_tables(model, ["Sales"])
    assert e.value.table == "Sales" and "source gone" in e.value.message
    assert all(unchanged(model, before).values()) and value(model) == 16


def test_a_column_the_query_no_longer_makes_fails_the_refresh(model):
    server.pbix_set_partition_m(model, "Sales", '#table({"Region"}, {{"East"}})')
    with pytest.raises(RefreshError, match="Amount"):
        refresh_tables(model, ["Sales"])


def test_names_outside_the_evaluator_fail_the_refresh(model):
    server.pbix_set_partition_m(model, "Sales", 'Excel.Workbook(File.Contents("C:\\\\x.xlsx"))')
    with pytest.raises(RefreshError, match="wasn't recognized"):
        refresh_tables(model, ["Sales"])


def test_a_table_without_m_is_refused(model):
    with pytest.raises(RefreshError, match="no M partition"):
        refresh_tables(model, ["Nope"])


class TestTableNames:
    KNOWN = {"Sales", "Fact A", "Date"}

    def test_quoted_and_bare(self):
        assert _dax_table_names("CALENDAR(MIN('Fact A'[When]), MAX(Sales[When]))", self.KNOWN) == {"Fact A", "Sales"}

    def test_strings_comments_and_columns_are_not_tables(self):
        e = '"Sales" & [Date] // Sales\n /* \'Fact A\' */ -- Sales'
        assert _dax_table_names(e, self.KNOWN) == set()

    def test_case_and_escapes(self):
        assert _dax_table_names("COUNTROWS(sales) + COUNTROWS('Fact A''s')", self.KNOWN | {"Fact A's"}) == {"Sales", "Fact A's"}
