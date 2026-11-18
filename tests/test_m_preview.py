"""preview_step: a query's value at one step, as Power Query's editor previews it, without changing the model."""
import json
import os
import tempfile
import uuid

import pytest

from pbix_mcp.m import CellError
from pbix_mcp.refresh import preview_step

QUERY = ('let\n    Source = #table(type table [a = Int64.Type, b = text], {{1, "x"}, {2, "y"}, {3, "z"}}),\n'
         '    Kept = Table.SelectColumns(Source, {"a"}),\n'
         '    Doubled = Table.AddColumn(Kept, "c", each [a] * 2, Int64.Type)\nin\n    Doubled')


@pytest.fixture
def model():
    from pbix_mcp import server
    from pbix_mcp.builder import PBIXBuilder
    b = PBIXBuilder("Preview")
    b.add_table("Sales", [{"name": "a", "data_type": "Int64"}], rows=[{"a": 7}])
    b.add_table("Other", [{"name": "n", "data_type": "Int64"}], rows=[{"n": 1}])
    b.add_measure("Sales", "Total", "SUM('Sales'[a])")
    path = os.path.join(tempfile.mkdtemp(), "preview.pbix")
    with open(path, "wb") as f:
        f.write(b.build())
    alias = f"preview-{uuid.uuid4().hex[:12]}"
    assert json.loads(server.pbix_open(path, alias))["success"]
    assert json.loads(server.pbix_set_partition_m(alias, "Other", '#table({"n"}, {{10}, {20}})'))["success"]
    yield alias
    server.pbix_close(alias, force=True)


def rows(p):
    return [[c for c in r] for r in p.rows]


def test_each_step(model):
    p = preview_step(model, QUERY, "Source")
    assert p.kind == "table" and [n for n, _ in p.columns] == ["a", "b"] and rows(p) == [[1, "x"], [2, "y"], [3, "z"]]
    assert p.columns[0][1].facet == "Int64.Type" and p.columns[1][1].kind == "text"
    p = preview_step(model, QUERY, "Kept")
    assert rows(p) == [[1], [2], [3]]
    p = preview_step(model, QUERY, "Doubled")
    assert [n for n, _ in p.columns] == ["a", "c"] and rows(p) == [[1, 2], [2, 4], [3, 6]]
    assert rows(preview_step(model, QUERY)) == rows(p)           # no step: the whole query


def test_the_models_other_queries_by_name(model):
    p = preview_step(model, "let S = Other, T = 1 + 1 in T", "S")
    assert rows(p) == [[10], [20]]
    v = preview_step(model, "let S = Other, T = 1 + 1 in T", "T")
    assert (v.kind, v.value) == ("value", 2)


def test_the_first_rows_and_whether_there_are_more(model):
    q = 'let S = #table({"n"}, {{1}, {2}, {3}, {4}, {5}}) in S'
    p = preview_step(model, q, "S", limit=3)
    assert (rows(p), p.more) == ([[1], [2], [3]], True)
    assert preview_step(model, q, "S", limit=5).more is False


def test_an_error_step_and_an_error_cell(model):
    p = preview_step(model, 'let S = error "boom" in S', "S")
    assert (p.kind, p.reason, p.message) == ("error", "Expression.Error", "boom")
    p = preview_step(model, 'let S = #table({"a"}, {{1}, {0}}), T = Table.AddColumn(S, "q", each if [a] = 0 then error "bad" else [a]) in T', "T")
    assert p.kind == "table" and p.rows[0] == [1, 1] and isinstance(p.rows[1][1], CellError)
    assert p.rows[1][1].error.message == "bad"


def test_functions_not_implemented_yet_are_named(model):
    p = preview_step(model, 'let S = Other, T = Table.SelectRows(S, each [n] > 10), U = Table.Sort(T, {"n"}) in U', "U")
    assert (p.kind, p.names) == ("unsupported", ["Table.SelectRows", "Table.Sort"])
    assert preview_step(model, 'let S = Other, T = Table.SelectRows(S, each [n] > 10) in T', "S").kind == "table"


def test_no_such_step(model):
    p = preview_step(model, QUERY, "Missing")
    assert (p.kind, p.message) == ("error", "The query has no step named 'Missing'.")
    assert preview_step(model, "1 + 1", "S").kind == "error"           # no let at all


def test_a_file_the_caller_doesnt_map(model):
    p = preview_step(model, 'let S = Csv.Document(File.Contents("C:\\src\\x.csv")) in S', "S")
    assert (p.kind, p.path) == ("unmapped", "C:\\src\\x.csv")


def test_the_model_is_unchanged(model):
    from pbix_mcp import server
    preview_step(model, QUERY, "Doubled")
    out = json.loads(server.pbix_evaluate_dax(model, "Total"))
    assert [(r["name"], r["value"]) for r in out["results"]] == [("Total", 7)]
