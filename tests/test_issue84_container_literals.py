"""Issue #84: container formatting numbers were written in a spelling Desktop
never uses.

pbix_format_visual wrote padding and spacing as Int64 literals, truncating the
value (7.5 -> '7L'), and border / dropShadow numbers as Python's float repr
('1.0D'). In 36 Desktop-authored files of the local corpus those properties
are integral D literals -- padding 892 of 892, spacing 226 of 226, border 76
of 76, dropShadow 164 of 169 (the five L literals all in one file) -- e.g.
'0D', '10D', '90D'. OpenBI's census of 25 files found padding '0D' 296 times
per side and no L literal at all (its doc 45).
"""
import json

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit


@pytest.fixture()
def report(tmp_path):
    alias = "cl_" + tmp_path.name[-6:]
    p = str(tmp_path / "t.pbix")
    assert json.loads(S.pbix_create(p, alias, json.dumps([{
        "name": "T", "columns": [{"name": "V", "data_type": "Double"}],
        "rows": [{"V": 1.0}]}])))["success"]
    assert json.loads(S.pbix_add_visual(alias, 0, "card", 20, 20, 200, 120, ""))["success"]
    yield alias, tmp_path
    for a in (alias, alias + "_r"):
        S._open_files.pop(a, None)
        S._dax_cache.pop(a, None)


def _reopened_vc(alias, tmp):
    out = str(tmp / "saved.pbix")
    assert json.loads(S.pbix_save(alias, output_path=out, overwrite=True))["success"]
    S.pbix_close(alias)
    a2 = alias + "_r"
    assert json.loads(S.pbix_open(out, a2))["success"]
    raw = json.loads(json.loads(S.pbix_get_layout_raw(a2))["message"])
    cfg = json.loads(raw["sections"][0]["visualContainers"][0]["config"])
    vc = cfg["singleVisual"]["vcObjects"]
    return {card: {k: v["expr"]["Literal"]["Value"]
                   for k, v in vc[card][0]["properties"].items() if "expr" in v}
            for card in vc}


def test_padding_and_spacing_are_desktop_d_literals(report):
    alias, tmp = report
    r = json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps({
        "padding": {"top": 7.5, "bottom": 8, "left": 12, "right": 12},
        "spacing": {"belowTitle": 8, "belowSubTitle": 10, "belowTitleArea": 10,
                    "vertical": 2}})))
    assert r["success"], r
    vc = _reopened_vc(alias, tmp)
    # 0.9.112: '7L' (the .5 silently dropped), '8L', '12L', '12L'
    assert vc["padding"] == {"top": "7.5D", "bottom": "8D", "left": "12D", "right": "12D"}
    assert vc["spacing"] == {"customizeSpacing": "true", "spaceBelowTitle": "8D",
                             "spaceBelowSubTitle": "10D", "spaceBelowTitleArea": "10D",
                             "verticalSpacing": "2D"}


def test_a_single_padding_number_sets_all_four_sides(report):
    alias, tmp = report
    assert json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps({"padding": 0})))["success"]
    assert _reopened_vc(alias, tmp)["padding"] == {
        "top": "0D", "bottom": "0D", "left": "0D", "right": "0D"}


def test_border_and_drop_shadow_numbers_have_no_fraction(report):
    alias, tmp = report
    assert json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps({
        "border": {"show": True, "radius": 0, "width": 1},
        "dropShadow": {"show": True, "angle": 90, "blur": 4, "distance": 3,
                       "spread": 0, "transparency": 85}})))["success"]
    vc = _reopened_vc(alias, tmp)
    # 0.9.112: '0.0D', '1.0D', '90.0D', ...
    assert vc["border"] == {"show": "true", "radius": "0D", "width": "1D"}
    assert vc["dropShadow"] == {"show": "true", "angle": "90D", "shadowBlur": "4D",
                                "shadowDistance": "3D", "shadowSpread": "0D",
                                "transparency": "85D"}
