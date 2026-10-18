"""Issue #119: a ThemeDataColor's ColorId indexes the colour picker's theme
row -- pure white, pure black, then the first eight data colours.

_resolve_theme_color read dataColors[ColorId], so pbix_extract_colors reported
every "White" as the theme's first data colour, and pbix_recolor rewrote white
and grey fills to the new primary when it remapped that colour (Desktop then
painted a recoloured Matrix Bubble Chart's cells magenta).

Re-derived clean-room on 2026-10-19 (docs/reverse-engineering/experiments/
2026-10-19-clean-room-remediation.md): the community Desktop-saved files use
ColorId 0..9 only (1,365 references), 0 only darkened and 1 only lightened;
and OpenBI's Desktop probe 33 measured the colours below under a theme whose
background and foreground were other colours. Earlier versions of this test
held vectors computed from Desktop's application script; they are gone.
"""
from __future__ import annotations

import json

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit

PAL = ["#118DFF", "#12239E", "#E66C37", "#6B007B", "#E044A7", "#744EC2", "#D9B300", "#D64550",
       "#197278", "#1AAB40"]


@pytest.mark.parametrize("cid,want", [(0, "#FFFFFF"), (1, "#000000")]
                         + [(i + 2, PAL[i]) for i in range(8)] + [(10, None), (11, None)])
def test_colorid_indexes_the_picker_row(cid, want):
    assert S._resolve_theme_color(PAL, cid, 0) == want


def test_a_short_palette_has_fewer_picker_colours():
    assert S._resolve_theme_color(PAL[:3], 4, 0) == "#E66C37"
    assert S._resolve_theme_color(PAL[:3], 5, 0) is None
    assert S._resolve_theme_color([], 2, 0) is None
    assert S._resolve_theme_color([], 0, -0.1) == "#E6E6E6"     # white needs no theme


# OpenBI's Desktop probe 33 (its ledger F123): shapes under a theme whose
# data colours start #E81123, #00B294, as Desktop drew them --
# (palette, ColorId, Percent, colour on screen)
PROBE_33 = ["#E81123", "#00B294"]
PROBE_SHADES = [
    (0, 0, "#FFFFFF"), (0, -0.1, "#E6E6E6"), (1, 0, "#000000"), (1, 0.1, "#1A1A1A"),
    (2, 0, "#E81123"), (2, -0.1, "#D10F20"), (3, 0, "#00B294"),
]


@pytest.mark.parametrize("cid,pct,want", PROBE_SHADES)
def test_shades_as_desktop_drew_them(cid, pct, want):
    assert S._resolve_theme_color(PROBE_33, cid, pct) == want


def test_a_shade_rounds_a_half_up():
    """229.5 -> 230 (white at -10%), 25.5 -> 26 (black at +10%), 31.5 -> 32
    (#E81123's blue at -10%): probe 33's three halves."""
    assert S._shade_color("#FFFFFF", -0.1) == "#E6E6E6"
    assert S._shade_color("#000000", 0.1) == "#1A1A1A"
    assert S._shade_color("#E81123", -0.1) == "#D10F20"


def _report(tmp_path, refs):
    """A report themed PAL[:3] with one shape per (ColorId, Percent) ref, as
    its background colour."""
    alias = "tdc_" + tmp_path.name[-6:]
    p = str(tmp_path / "t.pbix")
    assert json.loads(S.pbix_create(p, alias, json.dumps([{
        "name": "T", "columns": [{"name": "V", "data_type": "Double"}], "rows": [{"V": 1.0}]}])))["success"]
    assert json.loads(S.pbix_set_theme(alias, json.dumps({"name": "t119", "dataColors": PAL[:3]})))["success"]
    for i, _ in enumerate(refs):
        assert json.loads(S.pbix_add_visual(alias, 0, "shape", 20 + 210 * i, 20, 200, 120, ""))["success"]
    layout = json.loads(json.loads(S.pbix_get_layout_raw(alias))["message"])
    for vc, (cid, pct) in zip(layout["sections"][0]["visualContainers"], refs):
        cfg = json.loads(vc["config"])
        cfg.setdefault("singleVisual", {}).setdefault("vcObjects", {})["background"] = [{"properties": {
            "color": {"solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": cid, "Percent": pct}}}}}}}]
        vc["config"] = json.dumps(cfg)
    assert json.loads(S.pbix_set_layout_raw(alias, json.dumps(layout)))["success"]
    return alias


def _backgrounds(alias):
    layout = json.loads(json.loads(S.pbix_get_layout_raw(alias))["message"])
    out = []
    for vc in layout["sections"][0]["visualContainers"]:
        expr = json.loads(vc["config"])["singleVisual"]["vcObjects"]["background"][0][
            "properties"]["color"]["solid"]["color"]["expr"]
        out.append(expr.get("ThemeDataColor") or expr.get("Literal"))
    return out


def test_extract_colors_reports_what_desktop_draws(tmp_path):
    alias = _report(tmp_path, [(0, -0.1), (1, 0), (2, 0), (12, 0)])
    try:
        msg = json.loads(S.pbix_extract_colors(alias))["message"]
    finally:
        S.pbix_close(alias)
    by_hex = {ln.split()[0]: ln for ln in msg.splitlines()[1:] if ln.strip().startswith("#")}
    assert "[ThemeDataColor:0,-0.1]" in by_hex["#E6E6E6"]       # 0.9.120: #0F7EE5
    assert "[ThemeDataColor:1,0.0]" in by_hex["#000000"]
    assert "[ThemeDataColor:2,0.0]" in by_hex["#118DFF"]
    unresolved = [ln for ln in msg.splitlines() if "unresolved ThemeDataColor" in ln]
    assert unresolved and "[ThemeDataColor:12,0.0]" in unresolved[0]


def test_recolor_leaves_white_and_black_alone(tmp_path):
    alias = _report(tmp_path, [(0, -0.1), (1, 0), (2, 0), (2, 0.4), (12, 0)])
    try:
        assert json.loads(S.pbix_recolor(alias, json.dumps({"#118DFF": "#C2185B"})))["success"]
        got = _backgrounds(alias)
    finally:
        S.pbix_close(alias)
    assert got[0] == {"ColorId": 0, "Percent": -0.1}             # 0.9.120: '#C2185B'
    assert got[1] == {"ColorId": 1, "Percent": 0}
    assert got[2] == {"Value": "'#C2185B'"}                      # the remapped data colour
    assert got[3] == {"Value": "'#C2185B'"}
    assert got[4] == {"ColorId": 12, "Percent": 0}               # no such colour: left alone
