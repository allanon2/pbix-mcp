"""Issue #75: the builder's warnings never reached an MCP caller.

`PBIXBuilder.build()` reports every non-critical pre-build issue through
Python's `warnings` module. No MCP tool forwarded them, so a caller over MCP
(OpenBI's bridge included) got `success: true, warnings: []` while:

  * values differing only by case were folded onto one spelling (#43) — the
    0.9.105 fix that made the fold "no longer silent" was only true for
    Python-API callers;
  * a row field that is not a column was dropped — and when it was a
    misspelt column name, that column was left blank;
  * a rebuild skipped a user hierarchy whose level column had gone;
  * a relationship joined columns of different types, or had orphan keys.

The builder now records each warning with its kind and the tables it is
about (`build_warnings`); pbix_create, every rebuild-path tool and TMDL import
forward them into the response. A rebuild forwards only warnings about the
tables the call touched, so a standing condition elsewhere is not repeated on
every unrelated edit.
"""
import json
import os
import warnings

import pytest

from pbix_mcp import server as S
from pbix_mcp.builder import PBIXBuilder

pytestmark = pytest.mark.unit

COLS = [{"name": "N", "data_type": "String"}, {"name": "V", "data_type": "Double"}]
FOLD_ROWS = [{"N": "abc", "V": 1.0}, {"N": "ABC", "V": 2.0}, {"N": "Abc", "V": 3.0}]
TYPO_ROWS = [{"N": "x", "V": 1.0, "Vlaue": 5.0}]
FOLD = "differing only by case"
TYPO = "not columns"


@pytest.fixture()
def tmp_alias(tmp_path):
    made = []

    def make(stem):
        alias = f"i75_{stem}_{tmp_path.name[-6:]}"
        made.append(alias)
        return alias, str(tmp_path / f"{stem}.pbix")

    yield make
    for a in made:
        S._open_files.pop(a, None)
        S._dax_cache.pop(a, None)


def _call(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # the Python stream is not the point
        return json.loads(fn(*args, **kwargs))


def _warns(r, needle):
    return [w for w in (r.get("warnings") or []) if needle in w]


def _create(tmp_alias, stem, tables, **kw):
    alias, path = tmp_alias(stem)
    r = _call(S.pbix_create, path, alias, json.dumps(tables), **kw)
    assert r["success"], r
    return alias, r


TOOLS = ["pbix_create", "pbix_set_table_data", "pbix_update_table_rows",
         "pbix_append_table_rows"]


def _run_tool(tool, tmp_alias, rows):
    if tool == "pbix_create":
        return _create(tmp_alias, "c", [{"name": "T", "columns": COLS,
                                         "rows": rows}])[1]
    alias, _r = _create(tmp_alias, "c", [{"name": "T", "columns": COLS,
                                          "rows": [{"N": "seed", "V": 0.0}]}])
    if tool == "pbix_set_table_data":
        return _call(S.pbix_set_table_data, alias, "T",
                     json.dumps({"columns": COLS, "rows": rows}))
    if tool == "pbix_update_table_rows":
        return _call(S.pbix_update_table_rows, alias, "T", json.dumps(rows))
    return _call(S.pbix_append_table_rows, alias, "T", json.dumps(rows))


@pytest.mark.parametrize("tool", TOOLS)
def test_case_fold_reaches_the_response(tool, tmp_alias):
    """The #43 fold over MCP: success, and the response says so."""
    r = _run_tool(tool, tmp_alias, FOLD_ROWS)
    assert r["success"], r
    hits = _warns(r, FOLD)
    assert len(hits) == 1, r.get("warnings")
    assert "Table 'T' column 'N'" in hits[0]


@pytest.mark.parametrize("tool", TOOLS)
def test_unknown_row_field_reaches_the_response(tool, tmp_alias):
    r = _run_tool(tool, tmp_alias, TYPO_ROWS)
    assert r["success"], r
    hits = _warns(r, TYPO)
    assert len(hits) == 1, r.get("warnings")
    assert "'Vlaue'" in hits[0] and "not stored" in hits[0]


def test_unknown_field_is_one_line_per_table(tmp_alias):
    # a systematic typo used to emit one warning PER ROW
    rows = [{"N": f"n{i}", "V": float(i), "Vlaue": 1.0} for i in range(500)]
    _alias, r = _create(tmp_alias, "many", [{"name": "T", "columns": COLS,
                                             "rows": rows}])
    hits = _warns(r, TYPO)
    assert len(hits) == 1, len(hits)
    assert "500 row(s)" in hits[0] and "first at row 0" in hits[0]


def test_standing_conditions_are_not_repeated_on_unrelated_edits(tmp_alias):
    tables = [
        {"name": "Fact", "columns": [{"name": "K", "data_type": "Int64"},
                                     {"name": "V", "data_type": "Double"}],
         "rows": [{"K": 1, "V": 1.0}, {"K": 7, "V": 2.0}]},
        {"name": "Dim", "columns": [{"name": "K", "data_type": "Int64"}],
         "rows": [{"K": 1}]},
        {"name": "Other", "columns": [{"name": "X", "data_type": "String"}],
         "rows": [{"X": "a"}]},
    ]
    rel = [{"from_table": "Fact", "from_column": "K",
            "to_table": "Dim", "to_column": "K"}]
    alias, r = _create(tmp_alias, "rel", tables,
                       relationships_json=json.dumps(rel))
    assert _warns(r, "orphan FK"), r.get("warnings")   # the caller's input
    # an edit to a table the relationship does not involve: not repeated
    r = _call(S.pbix_set_table_data, alias, "Other", json.dumps(
        {"columns": tables[2]["columns"], "rows": [{"X": "b"}]}))
    assert r["success"] and not r.get("warnings"), r.get("warnings")
    # an edit to Fact that keeps an orphan: reported again, it is this call's
    r = _call(S.pbix_set_table_data, alias, "Fact", json.dumps(
        {"columns": tables[0]["columns"],
         "rows": [{"K": 1, "V": 1.0}, {"K": 9, "V": 3.0}]}))
    hits = _warns(r, "orphan FK")
    assert len(hits) == 1 and "{9}" in hits[0], r.get("warnings")


def test_hierarchy_dropped_by_a_rebuild_is_reported(tmp_alias):
    cols = [{"name": "Country", "data_type": "String"},
            {"name": "City", "data_type": "String"},
            {"name": "V", "data_type": "Double"}]
    alias, _r = _create(tmp_alias, "hier", [{"name": "Geo", "columns": cols,
        "rows": [{"Country": "NO", "City": "Oslo", "V": 1.0}]}])
    assert _call(S.pbix_add_hierarchy, alias, "Geo", "Place", json.dumps(
        [{"name": "Country", "column": "Country"},
         {"name": "City", "column": "City"}]))["success"]
    r = _call(S.pbix_set_table_data, alias, "Geo", json.dumps(
        {"columns": [cols[0], cols[2]], "rows": [{"Country": "NO", "V": 2.0}]}))
    assert r["success"], r
    hits = _warns(r, "Hierarchy 'Place'")
    assert len(hits) == 1 and "skipping" in hits[0], r.get("warnings")


def test_tmdl_import_reports_build_warnings_but_not_empty_tables(
        tmp_alias, tmp_path):
    tables = [
        {"name": "Fact", "columns": [{"name": "K", "data_type": "Int64"}],
         "rows": [{"K": 1}]},
        {"name": "Dim", "columns": [{"name": "K", "data_type": "String"}],
         "rows": [{"K": "1"}]},
    ]
    rel = [{"from_table": "Fact", "from_column": "K",
            "to_table": "Dim", "to_column": "K"}]
    alias, _r = _create(tmp_alias, "tm", tables,
                        relationships_json=json.dumps(rel))
    out = str(tmp_path / "tmdl")
    assert _call(S.pbix_export_tmdl, alias, out)["success"]
    S.pbix_close(alias)
    alias2, path2 = tmp_alias("tm2")
    r = _call(S.pbix_import_tmdl, out, path2, alias2)
    assert r["success"], r
    assert _warns(r, "data type mismatch"), r.get("warnings")
    # a schema-only import leaves every table empty by design
    assert not _warns(r, "empty table"), r.get("warnings")


def test_measures_only_table_does_not_warn(tmp_alias):
    # control: a measure container has no columns and legitimately no rows
    _alias, r = _create(
        tmp_alias, "meas",
        [{"name": "T", "columns": COLS, "rows": [{"N": "a", "V": 1.0}]},
         {"name": "_Measures", "columns": [], "rows": []}],
        measures_json=json.dumps([{"table": "_Measures", "name": "Total",
                                   "expression": "SUM(T[V])"}]))
    assert not r.get("warnings"), r.get("warnings")


class TestBuilderRecordsWarnings:

    def _builder(self, rows, dim_rows=None):
        b = PBIXBuilder("t")
        b.add_table("T", COLS, rows=rows)
        if dim_rows is not None:
            b.add_table("D", [{"name": "N", "data_type": "String"}],
                        rows=dim_rows)
            b.add_relationship("T", "N", "D", "N")
        return b

    def test_each_warning_carries_kind_and_tables(self):
        b = self._builder(FOLD_ROWS + TYPO_ROWS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            b.build()
        kinds = {w["kind"]: w for w in b.build_warnings}
        assert set(kinds) == {"case_fold", "unknown_fields"}
        assert all(w["tables"] == ("T",) for w in kinds.values())
        assert kinds["case_fold"]["message"].startswith("PBIX pre-build: ")

    def test_orphan_list_is_capped(self):
        rows = [{"N": f"k{i:02d}", "V": 1.0} for i in range(50)]
        b = self._builder(rows, dim_rows=[{"N": "none"}])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            b.build()
        (orph,) = [w for w in b.build_warnings if w["kind"] == "orphan_keys"]
        assert "50 orphan FK value(s)" in orph["message"]
        assert "(+40 more)" in orph["message"]
        assert orph["tables"] == ("T", "D")

    def test_python_api_still_warns(self):
        # control: Python callers keep their UserWarning
        b = self._builder(FOLD_ROWS)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            b.build()
        assert any(FOLD in str(w.message) for w in caught)

    def test_a_second_build_starts_clean(self):
        b = self._builder(FOLD_ROWS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            b.build()
            b.build()
        assert len(b.build_warnings) == 1


def test_no_stale_warning_leaks_into_the_next_call(tmp_alias):
    """The pending channel is drained by the response it belongs to."""
    alias, r = _create(tmp_alias, "leak", [{"name": "T", "columns": COLS,
                                            "rows": FOLD_ROWS}])
    assert _warns(r, FOLD)
    r2 = _call(S.pbix_list_tables, alias)
    assert not _warns(r2, FOLD), r2.get("warnings")
    assert os.path.exists(S._open_files[alias]["work_dir"])
