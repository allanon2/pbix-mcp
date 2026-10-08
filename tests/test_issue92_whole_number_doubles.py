"""Issue #92: whole-number doubles were written as Python's float repr.

#84 fixed the container cards, but every other D literal -- font sizes on the
title, subtitle, legend, data labels and both axes, the background's
transparency, and every other float-valued property -- still came out as
'13.0D'. In 37 Desktop-authored reports in the local corpus, Desktop writes
6,204 whole-number D literals and every one is bare ('13D', none '13.0D'):
1,421 font sizes and 684 transparencies among them. The literal writer now
spells a whole-number double that way everywhere; fractions keep theirs.
"""
import json

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit


@pytest.fixture()
def report(tmp_path):
    alias = "wd_" + tmp_path.name[-6:]
    p = str(tmp_path / "t.pbix")
    assert json.loads(S.pbix_create(p, alias, json.dumps([{
        "name": "T", "columns": [{"name": "V", "data_type": "Double"}],
        "rows": [{"V": 1.0}]}])))["success"]
    assert json.loads(S.pbix_add_visual(alias, 0, "clusteredColumnChart", 20, 20, 300, 200, ""))["success"]
    yield alias, tmp_path
    for a in (alias, alias + "_r"):
        S._open_files.pop(a, None)
        S._dax_cache.pop(a, None)


def _reopened(alias, tmp):
    out = str(tmp / "saved.pbix")
    assert json.loads(S.pbix_save(alias, output_path=out, overwrite=True))["success"]
    S.pbix_close(alias)
    assert json.loads(S.pbix_open(out, alias + "_r"))["success"]
    raw = json.loads(json.loads(S.pbix_get_layout_raw(alias + "_r"))["message"])
    sv = json.loads(raw["sections"][0]["visualContainers"][0]["config"])["singleVisual"]

    def lit(bucket, card, prop):
        return sv[bucket][card][0]["properties"][prop]["expr"]["Literal"]["Value"]
    return lit


def test_font_sizes_and_transparency_are_bare_like_desktop(report):
    alias, tmp = report
    r = json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps({
        "title": {"show": True, "text": "T", "fontSize": 13},
        "subtitle": {"show": True, "text": "S", "fontSize": 10},
        "background": {"show": True, "color": "#FFFFFF", "transparency": 20},
        "legend": {"show": True, "fontSize": 10},
        "dataLabels": {"show": True, "fontSize": 9},
        "categoryAxis": {"show": True, "fontSize": 8},
        "valueAxis": {"show": True, "fontSize": 8}})))
    assert r["success"], r
    lit = _reopened(alias, tmp)
    # 0.9.115: '13.0D', '10.0D', '20.0D', '10.0D', '9.0D', '8.0D', '8.0D'
    assert lit("vcObjects", "title", "fontSize") == "13D"
    assert lit("vcObjects", "subTitle", "fontSize") == "10D"
    assert lit("vcObjects", "background", "transparency") == "20D"
    assert lit("objects", "legend", "fontSize") == "10D"
    assert lit("objects", "labels", "fontSize") == "9D"
    assert lit("objects", "categoryAxis", "fontSize") == "8D"
    assert lit("objects", "valueAxis", "fontSize") == "8D"


def test_a_fraction_keeps_its_fraction(report):
    alias, tmp = report
    assert json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps({
        "title": {"show": True, "text": "T", "fontSize": 10.5}})))["success"]
    assert _reopened(alias, tmp)("vcObjects", "title", "fontSize") == "10.5D"


@pytest.mark.parametrize("value, want", [(13.0, "13D"), (0.0, "0D"), (-2.0, "-2D"),
                                         (10.5, "10.5D"), (0.25, "0.25D")])
def test_the_literal_writer(value, want):
    assert S._pbi_lit(value) == {"expr": {"Literal": {"Value": want}}}
