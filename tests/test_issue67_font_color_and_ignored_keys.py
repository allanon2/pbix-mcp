"""Issue #67: `fontColor` was dropped on title / subtitle / legend while the
call reported success — and, found while fixing it, the legend wrote a colour
property no built-in legend reads.

Oracle: the report theme schema that ships INSIDE Power BI Desktop
(desktop.reportThemeSchema.json, v5.71) — Microsoft's own declaration of each
card's formatting vocabulary:

    commonCards.title.fontColor        title text colour
    commonCards.subTitle.fontColor     subtitle text colour
    <22 visual types>.legend.labelColor   legend text colour — NO visual
                                          type declares a legend fontColor

So title/subtitle wrote the right property but only read it from `color`;
the legend read `color` and wrote `fontColor`, which nothing reads, so the
legend text colour never applied through ANY key. The corpus agrees: 341
title cards carry fontColor; legends carry labelColor.

The systemic half: the format mapper now records which keys it actually
consumed, and every key it did not is named in the message, in `warnings`,
and in `data.ignored` — so a dropped key can never read as success (#64,
#66, #67 were all this shape).
"""
import json
import re

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit


@pytest.fixture()
def report(tmp_path):
    alias = "i67_" + tmp_path.name[-6:]
    p = str(tmp_path / "t.pbix")
    assert json.loads(S.pbix_create(p, alias, json.dumps([{
        "name": "T", "columns": [{"name": "V", "data_type": "Double"}],
        "rows": [{"V": 1.0}]}])))["success"]
    assert json.loads(S.pbix_add_visual(
        alias, 0, "card", 40, 40, 280, 156, ""))["success"]
    assert json.loads(S.pbix_add_visual(
        alias, 0, "clusteredColumnChart", 340, 40, 400, 300, ""))["success"]
    yield alias, p, tmp_path
    for a in (alias, alias + "_r"):
        S._open_files.pop(a, None)
        S._dax_cache.pop(a, None)


def _fmt(alias, vi, payload):
    r = json.loads(S.pbix_format_visual(alias, 0, vi, json.dumps(payload)))
    assert r["success"], r
    return r


def _saved_sv(alias, p, tmp, vi):
    out = str(tmp / "saved.pbix")
    assert json.loads(S.pbix_save(alias, output_path=out,
                                  overwrite=True))["success"]
    S.pbix_close(alias)
    a2 = alias + "_r"
    assert json.loads(S.pbix_open(out, a2))["success"]
    raw = json.loads(json.loads(S.pbix_get_layout_raw(a2))["message"])
    return json.loads(raw["sections"][0]["visualContainers"][vi][
        "config"])["singleVisual"]


def _colour(props, name):
    v = props.get(name)
    return (v or {}).get("solid", {}).get("color", {}).get(
        "expr", {}).get("Literal", {}).get("Value")


class TestTitleAndSubtitleAcceptPowerBIName:
    """The reporter's measured table, each row through save -> reopen."""

    def test_title_fontcolor_is_written(self, report):
        alias, p, tmp = report
        _fmt(alias, 0, {"title": {"show": True, "fontColor": "#FF0000"}})
        props = _saved_sv(alias, p, tmp, 0)["vcObjects"]["title"][0][
            "properties"]
        assert _colour(props, "fontColor") == "'#FF0000'"

    def test_title_documented_color_still_works(self, report):
        alias, p, tmp = report
        _fmt(alias, 0, {"title": {"show": True, "color": "#00AA00"}})
        props = _saved_sv(alias, p, tmp, 0)["vcObjects"]["title"][0][
            "properties"]
        assert _colour(props, "fontColor") == "'#00AA00'"

    def test_fontcolor_overwrites_an_earlier_colour(self, report):
        """Row 3: the stale '#00AA00' survived a later fontColor write."""
        alias, p, tmp = report
        _fmt(alias, 0, {"title": {"show": True, "color": "#00AA00"}})
        _fmt(alias, 0, {"title": {"show": True, "fontColor": "#123456"}})
        props = _saved_sv(alias, p, tmp, 0)["vcObjects"]["title"][0][
            "properties"]
        assert _colour(props, "fontColor") == "'#123456'"

    def test_subtitle_fontcolor_is_written(self, report):
        alias, p, tmp = report
        _fmt(alias, 0, {"subtitle": {"show": True, "fontColor": "#654321"}})
        props = _saved_sv(alias, p, tmp, 0)["vcObjects"]["subTitle"][0][
            "properties"]
        assert _colour(props, "fontColor") == "'#654321'"

    def test_native_name_wins_and_the_loser_is_reported(self, report):
        """Both sent: the Power BI name wins, and the alias it beat is
        reported as not written instead of vanishing silently."""
        alias, _p, _t = report
        r = _fmt(alias, 0, {"title": {"color": "#111111",
                                      "fontColor": "#222222"}})
        assert r["data"]["ignored"]["properties"] == {"title": ["color"]}
        w = S._open_files[alias]["work_dir"]
        sv = json.loads(S._get_layout(w)["sections"][0]["visualContainers"][
            0]["config"])["singleVisual"]
        assert _colour(sv["vcObjects"]["title"][0]["properties"],
                       "fontColor") == "'#222222'"


class TestLegendWritesLabelColor:
    @pytest.mark.parametrize("key", ["color", "fontColor", "labelColor"])
    def test_every_spelling_lands_as_labelcolor(self, report, key):
        alias, p, tmp = report
        r = _fmt(alias, 1, {"legend": {"show": True, key: "#123456"}})
        assert not r.get("warnings"), r
        props = _saved_sv(alias, p, tmp, 1)["objects"]["legend"][0][
            "properties"]
        assert _colour(props, "labelColor") == "'#123456'"
        # the property no built-in legend reads must never be written
        assert "fontColor" not in props

    def test_native_labelcolor_takes_precedence(self, report):
        alias, _p, _t = report
        r = _fmt(alias, 1, {"legend": {"labelColor": "#AAAAAA",
                                       "fontColor": "#BBBBBB",
                                       "color": "#CCCCCC"}})
        w = S._open_files[alias]["work_dir"]
        sv = json.loads(S._get_layout(w)["sections"][0]["visualContainers"][
            1]["config"])["singleVisual"]
        assert _colour(sv["objects"]["legend"][0]["properties"],
                       "labelColor") == "'#AAAAAA'"
        assert sorted(r["data"]["ignored"]["properties"]["legend"]) == [
            "color", "fontColor"]


class TestIgnoredKeysAreReported:
    """The systemic fix: success can no longer hide a dropped key."""

    def test_unread_key_in_a_known_card(self, report):
        alias, _p, _t = report
        r = _fmt(alias, 0, {"title": {"show": True, "fontColour": "#123456"}})
        assert r["data"]["ignored"] == {"cards": [],
                                        "properties": {"title": ["fontColour"]}}
        assert "ignored: title.fontColour" in r["message"]
        assert any("fontColour" in w for w in r["warnings"]), r["warnings"]

    def test_unknown_card_beside_a_known_one(self, report):
        """Previously only reported when NOTHING applied (#51); applying one
        card silenced every unknown sibling."""
        alias, _p, _t = report
        r = _fmt(alias, 0, {"title": {"show": True}, "notACard": {"x": 1}})
        assert r["data"]["ignored"]["cards"] == ["notACard"]
        assert "ignored: notACard" in r["message"]

    def test_text_card_on_a_non_button_is_reported(self, report):
        """The text card only applies to actionButton; on anything else it
        was tested, marked handled, and dropped."""
        alias, _p, _t = report
        r = _fmt(alias, 0, {"title": {"show": True}, "text": {"text": "hi"}})
        assert r["data"]["ignored"]["cards"] == ["text"]

    def test_text_card_on_a_button_is_not_reported(self, report):
        alias, _p, _t = report
        assert json.loads(S.pbix_add_visual(
            alias, 0, "actionButton", 40, 300, 200, 60, ""))["success"]
        r = _fmt(alias, 2, {"text": {"text": "Go", "show": True}})
        assert r["data"]["ignored"] == {"cards": [], "properties": {}}

    def test_clean_payload_reports_nothing(self, report):
        """No false positives: a realistic multi-card payload, every key
        read, must report nothing ignored and add no warnings."""
        alias, _p, _t = report
        r = _fmt(alias, 1, {
            "title": {"text": "Sales", "show": True, "fontSize": 14,
                      "fontColor": "#222222", "bold": True,
                      "alignment": "center"},
            "background": {"show": True, "color": "#FFFFFF",
                           "transparency": 0},
            "border": {"show": True, "color": "#E3E9EF", "radius": 4},
            "padding": {"top": 6, "bottom": 6, "left": 12, "right": 12},
            "legend": {"show": True, "position": "top", "fontSize": 10,
                       "labelColor": "#333333"},
            "dataLabels": {"show": True, "fontSize": 9, "color": "#444444"},
            "categoryAxis": {"show": True, "fontSize": 9, "color": "#555555"},
            "valueAxis": {"show": True, "displayUnits": "thousands"},
        })
        assert r["data"]["ignored"] == {"cards": [], "properties": {}}
        assert not r.get("warnings"), r["warnings"]
        assert "ignored" not in r["message"]

    def test_callers_dict_is_not_mutated(self):
        """Tracking happens on copies; the caller's payload is untouched."""
        payload = {"title": {"show": True, "fontColor": "#123456"}}
        before = json.dumps(payload, sort_keys=True)
        S._build_format_objects(payload, visual_type="card")
        assert json.dumps(payload, sort_keys=True) == before
        assert type(payload) is dict and type(payload["title"]) is dict


class TestPageToolSameContract:
    """#64 promised both formatting tools share one contract."""

    def test_unread_key_inside_a_page_card(self, report):
        alias, _p, _t = report
        r = json.loads(S.pbix_format_page(alias, 0, json.dumps(
            {"background": {"color": "#F2F6F6", "colour": "#000000"}})))
        assert r["success"], r
        assert r["data"]["ignored_properties"] == {"background": ["colour"]}
        assert "background.colour" in r["message"]
        assert r.get("warnings")

    def test_ignored_keeps_its_original_shape(self, report):
        """Existing capability probes read data["ignored"] as the list of
        unknown top-level cards; that must not change."""
        alias, _p, _t = report
        r = json.loads(S.pbix_format_page(alias, 0, json.dumps(
            {"background": "#F2F6F6", "notAPageCard": {}})))
        assert r["data"]["ignored"] == ["notAPageCard"]


def test_every_documented_key_is_read():
    """Ratchet: every key the pbix_format_visual docstring documents must be
    consumed by the mapper on at least one visual type — so the contract a
    caller reads cannot drift from what the code does."""
    doc = S.pbix_format_visual.__doc__ or ""
    flat = re.sub(r"\n\s+(?=[\w|,\s]*[,}])", " ", doc)
    entries = re.findall(r"^\s*(\w+):\s*\{([^}]*)\}", flat, re.M)
    assert len(entries) >= 20, "docstring card list not found"

    def value_for(key):
        k = key.lower()
        if "color" in k or k == "background":
            return "#123456"
        if k in ("alignment",):
            return "center"
        if k in ("position",):
            return "top"
        if "displayunits" in k:
            return "thousands"
        if k in ("text", "title", "sectitle", "secaxistitle", "fontfamily",
                 "axistype", "style", "preset", "name", "layout"):
            return "x"
        if k.startswith(("show", "is")) or k in (
                "bold", "italic", "titlewrap", "heading", "gridlineshow",
                "invertaxis", "concatenatelabels", "switchaxisposition",
                "alignzeros", "secshow", "secshowaxistitle", "styleheader",
                "styletotal", "ignorepadding", "lockaspect"):
            return True
        return 10

    never_read = []
    for card, body in entries:
        keys = [a.strip() for part in body.split(",") for a in part.split("|")
                if re.fullmatch(r"[A-Za-z_]\w*", a.strip())]
        for key in keys:
            for vt in ("", "tableEx", "columnChart", "card", "shape",
                       "basicShape", "actionButton"):
                try:
                    out = S._build_format_objects(
                        {card: {key: value_for(key)}}, visual_type=vt)
                except Exception:
                    continue
                if (key not in out["_ignored_props"].get(card, [])
                        and card not in out["_ignored_cards"]):
                    break
            else:
                never_read.append(f"{card}.{key}")
    assert not never_read, f"documented but never read: {never_read}"
