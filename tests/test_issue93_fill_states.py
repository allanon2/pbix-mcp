"""Issue #93: the fill card on shape / actionButton / page and bookmark
navigators is per interaction state, like their outline (#74).

pbix_format_visual wrote {"fill": {color, transparency, show}} as one
selector-less entry. Desktop reads fillColor and transparency only from the
state entries ({"id": "default"}, hover, ...): Power BI Desktop 2.152 painted
the shape its theme's default blue and left the button and navigator
unfilled; with the split, all render the requested #F2C80F. Desktop's report
theme schema declares a fill card on exactly these four visuals, and
Desktop-authored files never put the colour selector-less (shape fillColor 50
on {"id": "default"}, actionButton 36, bookmarkNavigator 1).
"""
import json

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit

STATE_VISUALS = ("shape", "actionButton", "pageNavigator", "bookmarkNavigator")
YELLOW = "#F2C80F"


@pytest.fixture()
def report(tmp_path):
    alias = "fs_" + tmp_path.name[-6:]
    p = str(tmp_path / "t.pbix")
    assert json.loads(S.pbix_create(p, alias, json.dumps([{
        "name": "T", "columns": [{"name": "V", "data_type": "Double"}],
        "rows": [{"V": 1.0}]}])))["success"]
    yield alias, tmp_path
    for a in (alias, alias + "_r"):
        S._open_files.pop(a, None)
        S._dax_cache.pop(a, None)


def _fill_after_roundtrip(alias, tmp, vt, fill):
    assert json.loads(S.pbix_add_visual(alias, 0, vt, 20, 20, 200, 120, ""))["success"]
    r = json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps({"fill": fill})))
    assert r["success"], r
    out = str(tmp / "saved.pbix")
    assert json.loads(S.pbix_save(alias, output_path=out, overwrite=True))["success"]
    S.pbix_close(alias)
    assert json.loads(S.pbix_open(out, alias + "_r"))["success"]
    raw = json.loads(json.loads(S.pbix_get_layout_raw(alias + "_r"))["message"])
    sv = json.loads(raw["sections"][0]["visualContainers"][0]["config"])["singleVisual"]
    entries = sv["objects"]["fill"]

    def val(p):
        return (p["solid"]["color"]["expr"]["Literal"]["Value"] if "solid" in p
                else p["expr"]["Literal"]["Value"])
    return r, [(json.dumps(e.get("selector"), sort_keys=True),
                {k: val(v) for k, v in e["properties"].items()}) for e in entries]


@pytest.mark.parametrize("vt", STATE_VISUALS)
def test_state_visuals_get_desktops_split(report, vt):
    alias, tmp = report
    _r, entries = _fill_after_roundtrip(alias, tmp, vt, {"show": True, "color": YELLOW,
                                                          "transparency": 25})
    # 0.9.116: one selector-less entry holding all three
    assert entries == [("null", {"show": "true"}),
                       ('{"id": "default"}', {"fillColor": f"'{YELLOW}'",
                                              "transparency": "25D"})]


def test_basic_shape_keeps_one_selectorless_entry(report):
    # the legacy visual has no states; its fill is read selector-less
    alias, tmp = report
    _r, entries = _fill_after_roundtrip(alias, tmp, "basicShape", {"show": True, "color": YELLOW})
    assert entries == [("null", {"fillColor": f"'{YELLOW}'", "show": "true"})]


def test_power_bi_name_wins_and_the_beaten_key_is_reported(report):
    alias, tmp = report
    r, entries = _fill_after_roundtrip(alias, tmp, "shape", {"fillColor": YELLOW,
                                                             "color": "#000000"})
    assert entries == [('{"id": "default"}', {"fillColor": f"'{YELLOW}'"})]
    assert any("color" in w for w in r.get("warnings") or []), r.get("warnings")
