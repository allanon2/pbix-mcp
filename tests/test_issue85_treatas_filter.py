"""Issue #85: TREATAS as a CALCULATE filter argument was ignored.

_fn_treatas returns a ('__TREATAS__', (table, column), values) marker, and
CALCULATE's TREATAS branch looked for a dict nothing produces -- so every
CALCULATE(e, TREATAS(...)) applied no filter and returned the unfiltered value.
Expected values are Power BI Desktop 2.152's, over ADOMD.
"""
from __future__ import annotations

import pytest

from pbix_mcp import server as S
from tests.test_issue82_blank_member import build_blank_member_model, same, scalar

pytestmark = pytest.mark.unit

TREATAS = {'c_treat': ('CALCULATE([Total], TREATAS({BLANK()}, BD[Name]))', 20),
 't_x': ('CALCULATE([Total], TREATAS({"X"}, BD[Name]))', 33),
 't_xy': ('CALCULATE([Total], TREATAS({"X", "Y"}, BD[Name]))', 35),
 't_missing': ('CALCULATE([Total], TREATAS({"Q"}, BD[Name]))', None),
 't_lineage': ('CALCULATE([Total], TREATAS(VALUES(SD[Label]), BD[Name]))', 20),
 't_replace': ('CALCULATE(CALCULATE([Total], TREATAS({"X", "Y"}, BD[Name])), BD[Name] = "Y")',
               35),
 't_keep': ('CALCULATE(CALCULATE([Total], KEEPFILTERS(TREATAS({"X", "Y"}, BD[Name]))), '
            'BD[Name] = "Y")',
            2),
 't_empty': ('CALCULATE([Total], TREATAS(FILTER(VALUES(BD[Name]), FALSE()), BD[Name]))', None),
 't_key': ('CALCULATE([Total], TREATAS({10, 30}, BD[Key]))', 41),
 't_fact': ('CALCULATE([Total], TREATAS({99}, BF[Key]))', 4),
 't_ct': ('COUNTROWS(CALCULATETABLE(VALUES(BD[Name]), TREATAS({"X", "Z"}, BD[Name])))', 2),
 't_two': ('CALCULATE([Total], TREATAS({"X"}, BD[Name]), TREATAS({"X", "Y"}, BD[Name]))', 33)}


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("i85") / "treatas.pbix")
    alias = build_blank_member_model(path, {n: e for n, (e, _v) in TREATAS.items()})
    yield alias
    S._open_files.pop(alias, None)
    S._dax_cache.pop(alias, None)


@pytest.mark.parametrize("name", list(TREATAS))
def test_treatas_matches_desktop(model, name):
    expr, want = TREATAS[name]
    got = scalar(model, name)
    assert same(got, want), f"{expr}: Desktop {want!r}, engine {got!r}"
