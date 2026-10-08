"""Issue #105 (PR #96 by @allanon2): pbix_evaluate_dax names, per measure,
the unsupported DAX functions its evaluation depended on.

They were reported once per call, so a caller evaluating several measures at
once could not tell which result to distrust -- the engine reads an
unsupported call as BLANK and carries on, so NOSUCHFN(1) + SUM(..) still
returns a plausible total -- and every BLANK in the call was labelled
"unsupported", a legitimately empty measure included.
"""
from __future__ import annotations

import json

import pytest

from pbix_mcp import server as S
from pbix_mcp.builder import PBIXBuilder

pytestmark = pytest.mark.unit


@pytest.fixture()
def alias(tmp_path):
    b = PBIXBuilder("i105")
    b.add_table("T", [{"name": "k", "data_type": "Int64"}, {"name": "v", "data_type": "Double"}],
                rows=[{"k": 1, "v": 2.0}, {"k": 2, "v": 3.0}])
    b.add_measure("T", "Unknown", "NOSUCHFN(1) + SUM(T[v])")
    b.add_measure("T", "Through", "[Unknown] * 2")
    b.add_measure("T", "Twice", "[Unknown] + [Unknown]")
    b.add_measure("T", "Empty", "CALCULATE(SUM(T[v]), T[k] = 99)")
    b.add_measure("T", "Blank unknown", "NOSUCHFN(1)")
    b.add_measure("T", "Plain", "SUM(T[v])")
    p = tmp_path / "i105.pbix"
    b.save(str(p))
    a = "i105_" + tmp_path.name[-6:]
    assert json.loads(S.pbix_open(str(p), a))["success"]
    yield a
    S.pbix_close(a)


def _eval(alias, measures):
    r = json.loads(S.pbix_evaluate_dax(alias, measures, apply_default_filters=False))
    return {x["name"]: x for x in r["results"]}, r.get("warnings") or []


def test_each_result_names_its_own_unsupported_functions(alias):
    res, warnings = _eval(alias, "Unknown,Through,Twice,Empty,Blank unknown,Plain")
    assert res["Unknown"]["status"] == "ok" and res["Unknown"]["value"] == 5.0
    assert res["Unknown"]["unsupported_functions"] == ["NOSUCHFN"]        # 0.9.117: absent
    assert res["Through"]["unsupported_functions"] == ["NOSUCHFN"]        # through a reference
    assert res["Twice"]["unsupported_functions"] == ["NOSUCHFN"]          # second one from the cache
    assert res["Blank unknown"]["status"] == "unsupported"
    assert res["Blank unknown"]["unsupported_functions"] == ["NOSUCHFN"]
    assert res["Empty"]["status"] == "blank"                              # 0.9.117: "unsupported"
    assert "unsupported_functions" not in res["Empty"]
    assert "unsupported_functions" not in res["Plain"]
    assert any("NOSUCHFN" in w for w in warnings)                         # the call-wide warning stays


def test_a_clean_call_after_a_dirty_one_reports_nothing(alias):
    _eval(alias, "Unknown")
    res, warnings = _eval(alias, "Plain,Empty")
    assert res["Empty"]["status"] == "blank"
    assert "unsupported_functions" not in res["Plain"] and not warnings
