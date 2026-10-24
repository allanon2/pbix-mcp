"""Issue #215: the model-wide filter-index cache was fingerprinted by the
identity of the tables mapping and each table's ROW count only.

The load-time calculated-column pass (calc_tables._evaluate_calculated_columns)
appends each column to the model's own table dicts in place, row counts
unchanged, so an entry made while a column did not exist yet went on being
served after it did. On 0.9.145, SUM(A[V]) under B[Y] = 40, asked while B[Y]
was missing (the filter ignored: 36.0, every row), answered 36.0 again after
B[Y] = B[W] * 2 was appended, instead of 12.0 -- the rows of B[k] = 2, whose
Y is 40, which a fresh mapping of the same data answers.

The fix puts each table's column count in the fingerprint. A mapping whose
shape changes drops the entries of its earlier shapes instead of letting
FIFO evict the other open models' caches to make room: on 0.9.145 the four
calculated tables a load adds to one mapping took all four slots.

Expected values are hand-computed from the data below and equal what the
engine answers on a fresh mapping.
"""
from __future__ import annotations

import sqlite3

import pytest

from pbix_mcp.dax import calc_tables as ct
from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

RELS = [{"FromTable": "A", "FromColumn": "k", "ToTable": "B", "ToColumn": "k", "IsActive": 1,
         "CrossFilteringBehavior": 1, "FromCardinality": 2, "ToCardinality": 1}]
MEASURES = {"N": "SUM(A[V])", "TotalY": "SUM(B[Y])", "MaxY": "MAX(B[Y])"}


def model():
    # A[V] = 0..8; A[k] = 1, 2, 3 repeating, so B[k] = 2 owns V = 1, 4, 7 (12.0)
    return {"A": {"columns": ["k", "V"], "rows": [[i % 3 + 1, float(i)] for i in range(9)]},
            "B": {"columns": ["k", "W"], "rows": [[1, 10.0], [2, 20.0], [3, 30.0]]}}


def append_y(tables):
    """What the calculated-column pass does: B[Y] = B[W] * 2, in place."""
    tables["B"]["columns"].append("Y")
    for r in tables["B"]["rows"]:
        r.append(r[1] * 2)


def n_under_y40(tables):
    return de.evaluate_measure("N", tables, MEASURES, {"B.Y": [40.0]}, relationships=RELS)


def test_a_filter_on_a_column_appended_in_place_is_applied():
    tables = model()
    assert n_under_y40(tables) == 36.0            # B[Y] missing: the filter keeps every row
    append_y(tables)
    assert n_under_y40(tables) == 12.0
    fresh = model()
    append_y(fresh)
    assert n_under_y40(fresh) == 12.0


def _metadata(tmp_path, calc_columns):
    path = tmp_path / "meta.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE [Table] (ID INTEGER, Name TEXT, ModelID INTEGER)")
    con.execute("CREATE TABLE [Column] (ID INTEGER PRIMARY KEY, ExplicitName TEXT, "
                "Expression TEXT, TableID INTEGER)")
    con.execute("INSERT INTO [Table] VALUES (1, 'A', 1), (2, 'B', 1)")
    con.executemany("INSERT INTO [Column] (ExplicitName, Expression, TableID) VALUES (?, ?, ?)",
                    calc_columns)
    con.commit()
    con.close()
    return path.read_bytes()


def test_a_measure_asked_before_the_calculated_column_pass_sees_its_columns(tmp_path):
    tables = model()
    assert n_under_y40(tables) == 36.0
    assert de.evaluate_measure("TotalY", tables, MEASURES, relationships=RELS) is None
    ct._evaluate_calculated_columns(tables, _metadata(tmp_path, [
        ("Share", "DIVIDE(A[V], SUM(B[Y]))", 1),     # listed before B[Y] exists
        ("Y", "B[W] * 2", 2)]), RELS)
    assert tables["B"]["columns"] == ["k", "W", "Y"]
    assert n_under_y40(tables) == 12.0
    assert de.evaluate_measure("TotalY", tables, MEASURES, relationships=RELS) == 120.0
    assert de.evaluate_measure("MaxY", tables, MEASURES, relationships=RELS) == 60.0
    assert de.evaluate_measure("TotalY", tables, MEASURES, {"B.k": [2]}, relationships=RELS) == 40.0


def test_an_appended_column_gives_the_mapping_a_new_cache_and_keeps_the_others():
    other = model()
    n_under_y40(other)
    others_cache = de._shared_filter_cache(other)
    tables = model()
    n_under_y40(tables)
    before = de._shared_filter_cache(tables)
    append_y(tables)
    after = de._shared_filter_cache(tables)
    assert after is not before
    assert [c for c in de._SHARED_FILTER_CACHES.values() if c["__tables_ref__"] is tables] == [after]
    assert de._shared_filter_cache(other) is others_cache


def test_adding_calculated_tables_to_one_mapping_keeps_the_other_models_caches():
    """load_calculated_tables evaluates each calculated table over the mapping
    the earlier ones were added to."""
    other = model()
    n_under_y40(other)
    others_cache = de._shared_filter_cache(other)
    tables = model()
    for i in range(de._SHARED_FILTER_CACHE_MAX):
        res, err = ct.evaluate_calc_table_expression(
            "FILTER('A', 'A'[V] > %d)" % i, tables, {}, RELS)
        assert not err and len(res["rows"]) == 8 - i
        tables[f"Calc{i}"] = res
    assert len([c for c in de._SHARED_FILTER_CACHES.values() if c["__tables_ref__"] is tables]) == 1
    assert de._shared_filter_cache(other) is others_cache
    assert n_under_y40(other) == 36.0
