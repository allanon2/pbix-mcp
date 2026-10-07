"""The outline card: written where Power BI reads it, refused where it does
not exist.

Until 0.9.107 `{"outline": {...}}` wrote ONE selector-less entry on every
visual — `outline.color` everywhere but shape/basicShape, `outline.lineColor`
on shape — and always answered success. Three oracles disagree with that:

  * the report theme schema shipped inside Power BI Desktop (v5.71) declares
    an outline card on exactly seven visual types — shape, actionButton,
    cardVisual, listSlicer, advancedSlicerVisual, bookmarkNavigator,
    pageNavigator — and names the stroke colour `lineColor` on every one;
  * Desktop's own visual code reads `outline.show` from the selector-less
    entry, but lineColor / weight / transparency per STATE, from the entry
    whose selector is {"id": "default"};
  * Desktop-authored files follow that split (actionButton: show
    selector-less 231 times, lineColor 48 / weight 49 on {"id": "default"},
    never the other way round) and never contain `outline.color`.

Rendered in Desktop: the old single entry drew the default grey stroke at the
default width on all seven (colour AND weight dropped — shape included, so its
#47 lineColor never took effect either); the split draws the requested stroke
on all seven. Every other visual has no outline card at all — tables keep
theirs on grid.outlineColor, a multiRowCard on card.outlineColor, a slicer on
general.outlineColor — so there the write rendered nothing.
"""
import json
import re

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit

OUTLINE_VISUALS = ("shape", "actionButton", "cardVisual", "listSlicer",
                   "advancedSlicerVisual", "bookmarkNavigator", "pageNavigator")
RED = "#E00000"
DEFAULT_STATE = '{"id": "default"}'


@pytest.fixture()
def report(tmp_path):
    alias = "ol_" + tmp_path.name[-6:]
    p = str(tmp_path / "t.pbix")
    assert json.loads(S.pbix_create(p, alias, json.dumps([{
        "name": "T", "columns": [{"name": "V", "data_type": "Double"}],
        "rows": [{"V": 1.0}]}])))["success"]
    yield alias, tmp_path
    for a in (alias, alias + "_r"):
        S._open_files.pop(a, None)
        S._dax_cache.pop(a, None)


def _add(alias, vt):
    assert json.loads(S.pbix_add_visual(
        alias, 0, vt, 20, 20, 200, 120, ""))["success"]
    lay = S._get_layout(S._open_files[alias]["work_dir"])
    return len(lay["sections"][0]["visualContainers"]) - 1


def _fmt(alias, vi, payload):
    return json.loads(S.pbix_format_visual(alias, 0, vi, json.dumps(payload)))


def _objects(alias, vi):
    lay = S._get_layout(S._open_files[alias]["work_dir"])
    cfg = json.loads(lay["sections"][0]["visualContainers"][vi]["config"])
    return cfg["singleVisual"].get("objects") or {}


def _reopened_objects(alias, tmp, vi):
    out = str(tmp / "saved.pbix")
    assert json.loads(S.pbix_save(alias, output_path=out,
                                  overwrite=True))["success"]
    S.pbix_close(alias)
    a2 = alias + "_r"
    assert json.loads(S.pbix_open(out, a2))["success"]
    raw = json.loads(json.loads(S.pbix_get_layout_raw(a2))["message"])
    cfg = json.loads(raw["sections"][0]["visualContainers"][vi]["config"])
    return cfg["singleVisual"].get("objects") or {}


def _val(prop):
    if "solid" in prop:
        return prop["solid"]["color"]["expr"]["Literal"]["Value"]
    return prop["expr"]["Literal"]["Value"]


def _by_selector(entries):
    return {json.dumps(e.get("selector"), sort_keys=True):
            {k: _val(v) for k, v in (e.get("properties") or {}).items()}
            for e in entries}


class TestVisualsWithTheCardGetDesktopsLayout:

    @pytest.mark.parametrize("vt", OUTLINE_VISUALS)
    def test_show_selectorless_stroke_on_default_state(self, report, vt):
        """The pin: through save -> reopen, `show` alone on the
        selector-less entry and the whole stroke on {"id": "default"} — the
        only layout Desktop draws the stroke from."""
        alias, tmp = report
        vi = _add(alias, vt)
        r = _fmt(alias, vi, {"outline": {
            "show": True, "color": RED, "weight": 6, "transparency": 20}})
        assert r["success"], r
        assert not r.get("warnings"), r.get("warnings")
        ent = _by_selector(_reopened_objects(alias, tmp, vi)["outline"])
        assert ent == {
            "null": {"show": "true"},
            DEFAULT_STATE: {"lineColor": f"'{RED}'", "weight": "6D",
                            "transparency": "20D"},
        }

    @pytest.mark.parametrize("vt", OUTLINE_VISUALS)
    def test_never_writes_outline_color(self, report, vt):
        # `outline.color` is declared on no visual type and appears in no
        # Desktop-authored file
        alias, _tmp = report
        vi = _add(alias, vt)
        assert _fmt(alias, vi, {"outline": {"color": RED}})["success"]
        for e in _objects(alias, vi)["outline"]:
            assert "color" not in e["properties"]

    def test_line_color_is_accepted_and_wins_over_color(self, report):
        alias, _tmp = report
        vi = _add(alias, "actionButton")
        r = _fmt(alias, vi, {"outline": {"lineColor": "#00AA00",
                                         "color": RED}})
        assert r["success"], r
        ent = _by_selector(_objects(alias, vi)["outline"])
        assert ent[DEFAULT_STATE]["lineColor"] == "'#00AA00'"
        # the key it beat is reported, not silently consumed (#67)
        assert r["data"]["ignored"]["properties"] == {"outline": ["color"]}

    def test_second_call_merges_into_the_same_entries(self, report):
        alias, _tmp = report
        vi = _add(alias, "shape")
        assert _fmt(alias, vi, {"outline": {"show": True, "color": RED,
                                            "weight": 6}})["success"]
        assert _fmt(alias, vi, {"outline": {"lineColor": "#00AA00"}}
                    )["success"]
        entries = _objects(alias, vi)["outline"]
        assert len(entries) == 2
        ent = _by_selector(entries)
        assert ent[DEFAULT_STATE] == {"lineColor": "'#00AA00'",
                                      "weight": "6D"}

    def test_non_integral_weight_keeps_its_decimals(self):
        out = S._build_format_objects(
            {"outline": {"weight": 1.5}}, visual_type="actionButton")
        (entry,) = out["_objects"]["outline"]
        assert entry["selector"] == {"id": "default"}
        assert _val(entry["properties"]["weight"]) == "1.5D"

    def test_show_alone_writes_only_the_selectorless_entry(self):
        out = S._build_format_objects(
            {"outline": {"show": False}}, visual_type="cardVisual")
        assert out["_objects"]["outline"] == [
            {"properties": {"show": {"expr": {"Literal": {"Value": "false"}}}}}]


class TestVisualsWithoutTheCard:

    @pytest.mark.parametrize("vt,pointer", [
        ("tableEx", "grid.outlineColor"),
        ("pivotTable", "grid.outlineColor"),
        ("multiRowCard", "card.outlineColor"),
        ("slicer", "general.outlineColor"),
        ("columnChart", '"border"'),
        ("card", '"border"'),
        ("lineChart", '"border"'),
    ])
    def test_outline_alone_is_refused_with_where_it_lives(
            self, report, vt, pointer):
        alias, _tmp = report
        vi = _add(alias, vt)
        r = _fmt(alias, vi, {"outline": {"show": True, "color": RED}})
        assert not r["success"], r
        assert f"outline: not a card a '{vt}' has" in r["message"]
        warnings = r.get("warnings") or []
        assert len(warnings) == 1 and pointer in warnings[0], warnings
        assert "outline" not in _objects(alias, vi)

    def test_beside_another_card_the_other_applies(self, report):
        alias, tmp = report
        vi = _add(alias, "tableEx")
        r = _fmt(alias, vi, {"title": {"show": True, "text": "T"},
                             "outline": {"color": RED}})
        assert r["success"], r
        assert r["data"]["ignored"] == {"cards": ["outline"],
                                        "properties": {}}
        assert "ignored: outline" in r["message"]
        # one specific warning — not the hint plus a generic duplicate
        warnings = r.get("warnings") or []
        assert len(warnings) == 1 and "grid.outlineColor" in warnings[0]
        assert "outline" not in _reopened_objects(alias, tmp, vi)

    @pytest.mark.parametrize("vt", ["tableEx", "pivotTable", "table",
                                    "matrix", "multiRowCard", "slicer"])
    def test_every_pointer_names_a_key_the_mapper_writes(self, vt):
        """Ratchet: following the warning must not lead to another ignored
        key. The suggested payload is taken from the hint text itself."""
        hint = S._OUTLINE_ELSEWHERE[vt]
        payload = json.loads(re.search(r"send (\{.*\})", hint).group(1)
                             .replace("...", f'"{RED}"'))
        (card, inner), = payload.items()
        (prop, _), = inner.items()
        out = S._build_format_objects(payload, visual_type=vt)
        assert prop in out["_objects"][card][0]["properties"]
        assert not out["_ignored_cards"] and not out["_ignored_props"]

    @pytest.mark.parametrize("vt,card", [("multiRowCard", "card"),
                                         ("slicer", "general")])
    def test_outline_colour_and_weight_on_their_own_cards(self, report, vt,
                                                          card):
        # card.outlineColor (theme schema) and general.outlineColor /
        # outlineWeight (13 / 10 in Desktop-authored slicers, weight "ND")
        alias, tmp = report
        vi = _add(alias, vt)
        r = _fmt(alias, vi, {card: {"outlineColor": RED, "outlineWeight": 2}})
        assert r["success"], r
        assert r["data"]["ignored"] == {"cards": [], "properties": {}}
        props = _by_selector(_reopened_objects(alias, tmp, vi)[card])["null"]
        assert props == {"outlineColor": f"'{RED}'", "outlineWeight": "2D"}

    def test_custom_visual_is_not_told_it_lacks_the_card(self, report):
        """A custom visual defines its own cards; the theme schema says
        nothing about it, so neither a guess nor a 'has no outline' claim."""
        alias, _tmp = report
        guid = "vendorVisual0123456789ABCDEF0123456789AB"
        vi = _add(alias, guid)
        w = S._open_files[alias]["work_dir"]
        lay = S._get_layout(w)
        lay["publicCustomVisuals"] = [guid]
        S._set_layout(w, lay)
        r = _fmt(alias, vi, {"title": {"show": True, "text": "T"},
                             "outline": {"color": RED}})
        assert r["success"], r
        warnings = " ".join(r.get("warnings") or [])
        assert "is a custom visual" in warnings
        assert "has no outline card" not in warnings
        assert "outline" not in _objects(alias, vi)


class TestBasicShape:
    """The legacy basicShape draws its stroke from the selector-less `line`
    card and declares no outline card at all (Desktop's capabilities: line
    {lineColor, transparency, weight, roundEdge})."""

    def test_stroke_goes_to_the_line_card(self, report):
        alias, tmp = report
        vi = _add(alias, "basicShape")
        r = _fmt(alias, vi, {"outline": {"color": RED, "weight": 3}})
        assert r["success"], r
        line = _by_selector(_reopened_objects(alias, tmp, vi)["line"])
        assert line == {"null": {"lineColor": f"'{RED}'", "weight": "3L"}}

    def test_show_is_reported_not_written(self, report):
        # it used to land on outline.show, which basicShape never reads
        alias, _tmp = report
        vi = _add(alias, "basicShape")
        r = _fmt(alias, vi, {"outline": {"show": True, "color": RED}})
        assert r["success"], r
        assert r["data"]["ignored"]["properties"] == {"outline": ["show"]}
        assert "outline" not in _objects(alias, vi)


def test_caller_payload_is_left_untouched():
    payload = {"outline": {"show": True, "color": RED, "weight": 6}}
    snapshot = json.loads(json.dumps(payload))
    for vt in ("actionButton", "basicShape", "tableEx"):
        S._build_format_objects(payload, visual_type=vt)
    assert payload == snapshot


def test_docstring_names_the_seven_outline_visuals():
    doc = S.pbix_format_visual.__doc__ or ""
    section = doc[doc.index("outline: {"):doc.index("card: {")]
    for vt in OUTLINE_VISUALS:
        assert vt in section
    assert set(OUTLINE_VISUALS) == set(S._OUTLINE_CARD_VISUALS)
