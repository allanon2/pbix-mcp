"""pbix_evaluate_dax names the unsupported functions each measure depended on.

The engine reported them once per call (a warning string listing every function
hit by any measure), so a caller evaluating several measures together couldn't
tell which result to distrust. Each result now carries `unsupported_functions`:
the functions its own evaluation hit, including through measures it references,
also when that referenced measure's value came from the measure cache.
"""
from __future__ import annotations

import json

from pbix_mcp import server
from pbix_mcp.builder import PBIXBuilder


def _model(tmp_path):
    b = PBIXBuilder("Unsupported")
    b.add_table("T", [{"name": "k", "data_type": "Int64"}, {"name": "v", "data_type": "Double"}],
                rows=[{"k": 1, "v": 2.0}, {"k": 2, "v": 3.0}])
    b.add_measure("T", "Uses unknown", "NOSUCHFUNCTION(1) + SUM(T[v])")
    b.add_measure("T", "Plain", "SUM(T[v])")
    b.add_measure("T", "Through it", "[Uses unknown] * 2")
    p = tmp_path / "unsupported.pbix"
    b.save(str(p))
    return p


def test_each_result_names_its_own_unsupported_functions(tmp_path):
    server.pbix_open(str(_model(tmp_path)), alias="unsup")
    try:
        r = json.loads(server.pbix_evaluate_dax("unsup", "Uses unknown,Plain,Through it", apply_default_filters=False))
    finally:
        server.pbix_close("unsup")
    by = {x["name"]: x for x in r["results"]}
    assert by["Uses unknown"]["unsupported_functions"] == ["NOSUCHFUNCTION"]
    assert by["Through it"]["unsupported_functions"] == ["NOSUCHFUNCTION"]   # via the cached [Uses unknown]
    assert "unsupported_functions" not in by["Plain"]
    assert by["Plain"]["value"] == 5.0
    assert any("NOSUCHFUNCTION" in w for w in r.get("warnings", []))         # the call-wide warning stays
