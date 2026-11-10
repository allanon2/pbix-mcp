"""recompute_volatile: what's calculated from TODAY(), NOW(), UTCTODAY() or UTCNOW() is recomputed as of now, with
everything calculated over it, as Desktop does on opening a file (a calculated table saved one day answers, opened a
later day, as of that day; pivot-studio #316). The clock is pinned by replacing the engine's functions."""
import json
import os
import tempfile
import uuid
from datetime import datetime

import pytest

from pbix_mcp import server
from pbix_mcp.builder import PBIXBuilder
from pbix_mcp.dax.engine import DAXEngine
from pbix_mcp.refresh import RefreshError, _volatile_nodes, _Metadata, recompute_volatile

COLUMNS = [{"name": "Region", "data_type": "String"}, {"name": "Amount", "data_type": "Int64"}]
ROWS = [{"Region": "East", "Amount": 10}, {"Region": "West", "Amount": 5}]
SAVED = datetime(2023, 11, 29)      # the day the "file" was saved: what the calculations hold before a recompute
OPENED = datetime(2026, 11, 4)      # the day it's opened


@pytest.fixture
def clock(monkeypatch):
    """Set ``clock.now`` to move every DAX clock function."""
    class Clock:
        now = SAVED
    c = Clock()
    monkeypatch.setattr(DAXEngine, "_fn_now", lambda self, a, ctx: c.now)
    monkeypatch.setattr(DAXEngine, "_fn_today", lambda self, a, ctx: datetime(c.now.year, c.now.month, c.now.day))
    monkeypatch.setattr(DAXEngine, "_fn_utcnow", lambda self, a, ctx: c.now)
    monkeypatch.setattr(DAXEngine, "_fn_utctoday", lambda self, a, ctx: datetime(c.now.year, c.now.month, c.now.day))
    return c


@pytest.fixture
def model(clock):
    """An open model: Sales (Enter data) and the measures Total = SUM(Sales[Amount]) and Today = TODAY()."""
    b = PBIXBuilder("Volatile")
    b.add_table("Sales", COLUMNS, rows=ROWS)
    b.add_measure("Sales", "Total", "SUM('Sales'[Amount])")
    b.add_measure("Sales", "Today", "TODAY()")
    path = os.path.join(tempfile.mkdtemp(), "volatile.pbix")
    with open(path, "wb") as f:
        f.write(b.build())
    alias = f"volatile-{uuid.uuid4().hex[:12]}"
    assert json.loads(server.pbix_open(path, alias))["success"]
    yield alias
    server.pbix_close(alias, force=True)


def add_table(alias: str, name: str, expr: str) -> None:
    out = json.loads(server.pbix_datamodel_add_calculated_table(alias, name, expr))
    assert out["success"], out


def add_column(alias: str, table: str, name: str, expr: str, data_type: str) -> None:
    out = json.loads(server.pbix_datamodel_add_calculated_column(alias, table, name, expr, data_type))
    assert out["success"], out


def column(alias: str, table: str, name: str) -> list:
    t = server._get_dax_context(alias)["tables"][table]
    return [r[t["columns"].index(name)] for r in t["rows"]]


def days(values: list) -> list:
    return [v.date().isoformat() if hasattr(v, "date") else v for v in values]


def test_a_table_from_today_is_recomputed_as_of_now(model, clock):
    add_table(model, "Stamp", 'ROW("Day", TODAY())')
    assert days(column(model, "Stamp", "Day")) == ["2023-11-29"]
    clock.now = OPENED
    result = recompute_volatile(model)
    assert result.calculated_tables == ["Stamp"]
    assert days(column(model, "Stamp", "Day")) == ["2026-11-04"]


@pytest.mark.parametrize("fn", ["NOW", "UTCNOW", "UTCTODAY", "today"])
def test_each_clock_function_counts(model, clock, fn):
    add_table(model, "Stamp", f'ROW("Day", {fn} ())')
    clock.now = OPENED
    assert recompute_volatile(model).calculated_tables == ["Stamp"]
    assert days(column(model, "Stamp", "Day")) == ["2026-11-04"]


def test_through_a_measure(model, clock):
    add_table(model, "Stamp", 'ROW("Day", [Today])')
    clock.now = OPENED
    assert recompute_volatile(model).calculated_tables == ["Stamp"]
    assert days(column(model, "Stamp", "Day")) == ["2026-11-04"]


def test_what_is_calculated_over_it_follows(model, clock):
    add_table(model, "Stamp", 'ROW("Day", TODAY())')
    add_table(model, "Year", 'ROW("Y", YEAR(MAX(\'Stamp\'[Day])))')
    assert column(model, "Year", "Y") == [2023]
    clock.now = OPENED
    result = recompute_volatile(model)
    assert result.calculated_tables == ["Stamp", "Year"]
    assert column(model, "Year", "Y") == [2026]


def test_a_calculated_column_from_today(model, clock):
    add_column(model, "Sales", "Age", "INT(TODAY() - DATE(2023, 1, 1))", "Int64")
    assert column(model, "Sales", "Age") == [332, 332]
    clock.now = OPENED
    assert recompute_volatile(model).calculated_columns == ["Sales"]
    assert column(model, "Sales", "Age") == [1403, 1403]
    assert column(model, "Sales", "Amount") == [10, 5]


def test_nothing_volatile_changes_nothing(model, clock):
    add_table(model, "Big", "FILTER('Sales', 'Sales'[Amount] > 4)")
    add_column(model, "Sales", "Twice", "'Sales'[Amount] * 2", "Int64")
    before = server._get_dax_context(model)
    clock.now = OPENED
    result = recompute_volatile(model)
    assert result.calculated_tables == [] and result.calculated_columns == []
    assert server._get_dax_context(model) is before


def test_a_name_in_a_string_or_comment_or_column_is_not_a_call(model):
    add_table(model, "Words", 'ROW("Today", "TODAY()") // TODAY()\n/* NOW() */')
    add_column(model, "Sales", "UTCNOW", "'Sales'[Amount]", "Int64")
    add_table(model, "Cols", "ROW(\"X\", MAX('Sales'[UTCNOW]))")
    meta = _Metadata(server._ensure_open(model))
    assert _volatile_nodes(meta, server._get_dax_context(model)) == []


def test_a_failure_keeps_the_saved_rows(model, clock, monkeypatch):
    add_table(model, "Stamp", 'ROW("Day", TODAY())')
    before = server._get_dax_context(model)
    clock.now = OPENED

    def broken(self, a, ctx):
        raise ValueError("clock gone")
    monkeypatch.setattr(DAXEngine, "_fn_today", broken)
    with pytest.raises(RefreshError) as e:
        recompute_volatile(model)
    assert e.value.table == "Stamp"
    assert server._get_dax_context(model) is before
    assert days(column(model, "Stamp", "Day")) == ["2023-11-29"]
