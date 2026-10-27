"""Refreshing tables of an open model (pbix_mcp.refresh): the partition's M is evaluated again, its rows load
by the model's column types, calculated columns and the calculated tables built on a refreshed table are
recomputed, and the DAX engine answers from the new rows. Models are built by this package's builder, whose
tables carry Desktop's "Enter data" M."""
import json
import os
import tempfile
import uuid

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
    alias = f"refresh-{uuid.uuid4().hex[:12]}"
    assert '"success":true' in server.pbix_open(path, alias)
    yield alias
    server.pbix_close(alias, force=True)


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
    server.pbix_set_partition_m(model, "Sales", 'Csv.Document(Web.Contents("https://example.com/x.csv"))')
    with pytest.raises(RefreshError, match="wasn't recognized"):
        refresh_tables(model, ["Sales"])


def test_a_table_without_m_is_refused(model):
    with pytest.raises(RefreshError, match="no M partition"):
        refresh_tables(model, ["Nope"])


REG_COLUMNS = [{"name": "Region", "data_type": "String"}, {"name": "Mgr", "data_type": "String"}]
REG_ROWS = [{"Region": "East", "Mgr": "Ann"}, {"Region": "West", "Mgr": "Bo"}]


@pytest.fixture
def star():
    """Sales (many) -> Reg (one) on Region, the measure Total, both tables Enter-data M."""
    b = PBIXBuilder("Star")
    b.add_table("Sales", COLUMNS, rows=ROWS)
    b.add_table("Reg", REG_COLUMNS, rows=REG_ROWS)
    b.add_relationship("Sales", "Region", "Reg", "Region")
    b.add_measure("Sales", "Total", "SUM('Sales'[Amount])")
    path = os.path.join(tempfile.mkdtemp(), "star.pbix")
    with open(path, "wb") as f:
        f.write(b.build())
    alias = f"star-{uuid.uuid4().hex[:12]}"
    assert json.loads(server.pbix_open(path, alias))["success"]
    yield alias
    server.pbix_close(alias, force=True)


def set_m(alias: str, table: str, m: str) -> None:
    out = json.loads(server.pbix_set_partition_m(alias, table, m))
    assert out["success"], out


def rows_of(alias: str, table: str, column: str) -> list:
    t = server._get_dax_context(alias)["tables"][table]
    return sorted((r[t["columns"].index(column)] for r in t["rows"]), key=lambda v: (v is None, str(v)))


@pytest.fixture
def lookup_column(monkeypatch):
    """Declare Sales[Mgr] = LOOKUPVALUE('Reg'[Mgr], ...) in the refresh's view of the model: pbix-mcp's authoring
    tool won't make a cross-table calculated column, and Desktop-saved models have them."""
    from pbix_mcp import refresh as r

    class WithLookup(r._Metadata):
        def calculated_columns(self, table):
            extra = [("Mgr", "LOOKUPVALUE('Reg'[Mgr], 'Reg'[Region], 'Sales'[Region])", 2)] if table == "Sales" else []
            return super().calculated_columns(table) + extra

        def tables_with_calculated_columns(self):
            return sorted(set(super().tables_with_calculated_columns()) | {"Sales"})

        def column_order(self, table):
            return super().column_order(table) + (["Mgr"] if table == "Sales" else [])
    monkeypatch.setattr(r, "_Metadata", WithLookup)


class TestReview:
    """Cases from an independent review of the first version."""

    def test_the_refresh_survives_another_models_cache_drop_and_a_save(self, model, star):
        set_rows(model, [{"Region": "North", "Amount": 100}])
        refresh_tables(model, ["Sales"])
        set_m(star, "Reg", _build_enter_data_m(REG_COLUMNS, REG_ROWS))   # clears every model's context cache
        assert value(model) == 100
        server._dax_cache.clear()
        assert value(model) == 100

    def test_an_edit_of_the_model_itself_drops_the_refresh(self, model):
        set_rows(model, [{"Region": "North", "Amount": 100}])
        refresh_tables(model, ["Sales"])
        set_rows(model, [{"Region": "North", "Amount": 5}])     # a new M: the old refresh no longer describes it
        assert value(model) == 16

    def test_a_calc_table_reached_through_a_measure_is_recomputed(self, star):
        out = server.pbix_datamodel_add_calculated_table(star, "ByReg", "ADDCOLUMNS(VALUES('Reg'[Region]), \"T\", [Total])")
        assert json.loads(out)["success"], out
        set_rows(star, [{"Region": "East", "Amount": 1000}])
        result = refresh_tables(star, ["Sales"])
        assert "ByReg" in result.calculated_tables
        assert [x for x in rows_of(star, "ByReg", "T") if x is not None] == [1000]

    def test_a_lookup_reads_the_refreshed_table_whatever_the_order(self, star, lookup_column):
        set_m(star, "Reg", _build_enter_data_m(REG_COLUMNS, [{"Region": "East", "Mgr": "Cy"}, {"Region": "West", "Mgr": "Di"}]))
        for order in (["Sales", "Reg"], ["Reg", "Sales"]):
            refresh_tables(star, order)
            assert rows_of(star, "Sales", "Mgr") == ["Cy", "Cy", "Di"], order

    def test_a_calc_column_on_a_table_not_refreshed_follows(self, star, lookup_column):
        set_m(star, "Reg", _build_enter_data_m(REG_COLUMNS, [{"Region": "East", "Mgr": "Cy"}, {"Region": "West", "Mgr": "Di"}]))
        result = refresh_tables(star, ["Reg"])
        assert "Sales" in result.calculated_columns
        assert rows_of(star, "Sales", "Mgr") == ["Cy", "Cy", "Di"]

    def test_summarize_and_addcolumns_calc_tables(self, star):
        for name, dax in (("S", "SUMMARIZE('Sales', 'Sales'[Region], \"Amt\", SUM('Sales'[Amount]))"),
                          ("A", "ADDCOLUMNS(VALUES('Sales'[Region]), \"N\", 1)")):
            out = server.pbix_datamodel_add_calculated_table(star, name, dax)
            assert json.loads(out)["success"], out
        set_rows(star, [{"Region": "West", "Amount": 7}, {"Region": "West", "Amount": 3}])
        result = refresh_tables(star, ["Sales"])
        assert {"S", "A"} <= set(result.calculated_tables)
        assert rows_of(star, "S", "Amt") == [10] and rows_of(star, "A", "Region") == ["West"]

    def test_an_unsupported_function_in_a_calc_table_refuses_the_refresh(self, star):
        out = server.pbix_datamodel_add_calculated_table(star, "Odd", "SELECTCOLUMNS('Sales', \"R\", 'Sales'[Region])")
        assert json.loads(out)["success"], out
        from pbix_mcp.refresh import _Metadata
        info = server._ensure_open(star)
        meta = _Metadata(info)
        meta.calculated_tables["Odd"] = "SELECTCOLUMNS('Sales', \"R\", NOSUCHFN('Sales'[Region]))"
        from pbix_mcp import refresh as r
        orig = r._Metadata
        r._Metadata = lambda info: meta   # the same model, its expression replaced
        try:
            with pytest.raises(RefreshError, match="unsupported"):
                refresh_tables(star, ["Sales"])
        finally:
            r._Metadata = orig

    def test_a_query_that_names_another_query(self, star):
        set_m(star, "Sales", 'let t = Table.TransformColumnTypes(#table({"Region", "Amount"}, '
                             '{{"East", "4"}}), {{"Amount", Int64.Type}}) in Table.Combine({t, t})')
        refresh_tables(star, ["Sales"])
        assert rows_of(star, "Sales", "Amount") == [4, 4]
        set_m(star, "Reg", 'Table.AddColumn(Table.SelectColumns(Sales, {"Region"}), "Mgr", each "M" & [Region])')
        refresh_tables(star, ["Reg"])
        assert rows_of(star, "Reg", "Mgr") == ["MEast", "MEast"]


class TestTableNames:
    KNOWN = {"Sales", "Fact A", "Date"}

    def test_quoted_and_bare(self):
        assert _dax_table_names("CALENDAR(MIN('Fact A'[When]), MAX(Sales[When]))", self.KNOWN) == {"Fact A", "Sales"}

    def test_strings_comments_and_columns_are_not_tables(self):
        e = '"Sales" & [Date] // Sales\n /* \'Fact A\' */ -- Sales'
        assert _dax_table_names(e, self.KNOWN) == set()

    def test_bracket_escapes(self):
        assert _dax_table_names('[x]] "y] & Sales', self.KNOWN) == {"Sales"}

    def test_a_function_named_like_a_table_is_not_one(self):
        assert _dax_table_names("Date(2020, 1, 1) + Date", self.KNOWN) == {"Date"}
        assert _dax_table_names("DATE (2020, 1, 1)", self.KNOWN) == set()

    def test_case_and_escapes(self):
        assert _dax_table_names("COUNTROWS(sales) + COUNTROWS('Fact A''s')", self.KNOWN | {"Fact A's"}) == {"Sales", "Fact A's"}
