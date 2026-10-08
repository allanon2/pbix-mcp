"""Issue #112: a PBIR report's visual groups survive the classic layout.

_pbir_visual_to_container carried neither a visual's parentGroupName nor a
group's visualGroup, so every grouped visual read back as top-level with its
GROUP-RELATIVE position, and every group as a visual with an empty
singleVisual. Classic, as Desktop writes it (138 groups in the local corpus):
a group is config.singleVisualGroup {displayName, groupMode: 0, ...} with no
singleVisual; a child is config.parentGroupName, positioned relative to the
group. PBIR names the mode ("ScaleMode"); Desktop's enum is ScaleMode = 0,
ScrollMode = 1. The writer round-trips both.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit

GROUP = {"name": "grp1", "position": {"x": 300, "y": 500, "z": 12000, "height": 320, "width": 600},
         "visualGroup": {"displayName": "Group 1", "groupMode": "ScaleMode"}}
CHILD = {"name": "kid1", "position": {"x": 10, "y": 20, "z": 0, "height": 100, "width": 200},
         "parentGroupName": "grp1",
         "visual": {"visualType": "card", "query": {"queryState": {"Values": {"projections": [{
             "field": {"Measure": {"Expression": {"SourceRef": {"Entity": "Sales"}}, "Property": "Total"}},
             "queryRef": "Sales.Total"}]}}}}}
TOP = {"name": "top1", "position": {"x": 5, "y": 6, "z": 1, "height": 50, "width": 60},
       "visual": {"visualType": "textbox"}}
HIDDEN_SCROLL = {"name": "grp2", "position": {"x": 0, "y": 0, "z": 1, "height": 10, "width": 10},
                 "visualGroup": {"displayName": "G2", "groupMode": "ScrollMode"}, "isHidden": True}


def _pbix(tmp_path, visuals):
    path = str(tmp_path / "grouped.pbix")
    with zipfile.ZipFile(path, "w") as z:
        d = "Report/definition"
        z.writestr(f"{d}/version.json", json.dumps({"version": "2.0.0"}))
        z.writestr(f"{d}/report.json", json.dumps({"$schema": "report/2.0.0"}))
        z.writestr(f"{d}/pages/pages.json", json.dumps({"pageOrder": ["p1"], "activePageName": "p1"}))
        z.writestr(f"{d}/pages/p1/page.json", json.dumps(
            {"name": "p1", "displayName": "Page 1", "displayOption": "FitToPage", "width": 1280,
             "height": 720}))
        for v in visuals:
            z.writestr(f"{d}/pages/p1/visuals/{v['name']}/visual.json", json.dumps(v))
        z.writestr("Version", "1.28")
    return path


def _open(path, alias):
    assert json.loads(S.pbix_open(path, alias))["success"]
    lay = json.loads(json.loads(S.pbix_get_layout_raw(alias))["message"])
    return {json.loads(vc["config"])["name"]: (vc, json.loads(vc["config"]))
            for vc in lay["sections"][0]["visualContainers"]}


def _visuals(path):
    z = zipfile.ZipFile(path)
    return {json.loads(z.read(n))["name"]: json.loads(z.read(n)) for n in z.namelist()
            if n.endswith("visual.json")}


def test_groups_and_children_read_as_classic(tmp_path):
    alias = "g112a_" + tmp_path.name[-6:]
    by = _open(_pbix(tmp_path, [GROUP, CHILD, TOP, HIDDEN_SCROLL]), alias)
    try:
        _vc, g = by["grp1"]
        assert g["singleVisualGroup"] == {"displayName": "Group 1", "groupMode": 0}   # 0.9.119: absent
        assert "singleVisual" not in g
        vc, k = by["kid1"]
        assert k["parentGroupName"] == "grp1" and (vc["x"], vc["y"]) == (10, 20)     # group-relative
        assert "parentGroupName" not in by["top1"][1]
        assert by["grp2"][1]["singleVisualGroup"] == {"displayName": "G2", "groupMode": 1, "isHidden": True}
        pos = json.loads(S.pbix_get_visual_positions(alias, 0))["message"]
        assert "at (310,520)" in pos and "[child of group]" in pos                   # absolute on the page
    finally:
        S.pbix_close(alias)


def test_a_save_without_edits_keeps_every_visual_json(tmp_path):
    """Guard: the group fields read in must not count as edits on the way out."""
    src = _pbix(tmp_path, [GROUP, CHILD, TOP, HIDDEN_SCROLL])
    alias = "g112b_" + tmp_path.name[-6:]
    _open(src, alias)
    out = str(tmp_path / "saved.pbix")
    assert json.loads(S.pbix_save(alias, output_path=out, overwrite=True))["success"]
    S.pbix_close(alias)
    assert _visuals(out) == _visuals(src)


def test_group_edits_are_written_the_pbir_way(tmp_path):
    src = _pbix(tmp_path, [GROUP, CHILD, TOP])
    alias = "g112c_" + tmp_path.name[-6:]
    by = _open(src, alias)
    names = list(by)
    try:
        g = dict(by["grp1"][1])
        g["singleVisualGroup"] = {"displayName": "Renamed", "groupMode": 1}
        assert json.loads(S.pbix_update_visual_json(alias, 0, names.index("grp1"), json.dumps(g)))["success"]
        k = dict(by["kid1"][1])
        k.pop("parentGroupName")
        assert json.loads(S.pbix_update_visual_json(alias, 0, names.index("kid1"), json.dumps(k)))["success"]
        t = dict(by["top1"][1], parentGroupName="grp1")
        assert json.loads(S.pbix_update_visual_json(alias, 0, names.index("top1"), json.dumps(t)))["success"]
        out = str(tmp_path / "edited.pbix")
        assert json.loads(S.pbix_save(alias, output_path=out, overwrite=True))["success"]
    finally:
        S.pbix_close(alias)
    v = _visuals(out)
    assert v["grp1"]["visualGroup"] == {"displayName": "Renamed", "groupMode": "ScrollMode"}
    assert "visual" not in v["grp1"]                      # a group has no visual block
    assert "parentGroupName" not in v["kid1"]
    assert v["top1"]["parentGroupName"] == "grp1"


def test_a_group_is_hidden_and_shown_through_its_config(tmp_path):
    src = _pbix(tmp_path, [GROUP, CHILD, HIDDEN_SCROLL])
    alias = "g112d_" + tmp_path.name[-6:]
    by = _open(src, alias)
    names = list(by)
    try:
        for name, hide in (("grp1", True), ("grp2", False)):
            cfg = dict(by[name][1])
            svg = {k: v for k, v in cfg["singleVisualGroup"].items() if k != "isHidden"}
            cfg["singleVisualGroup"] = dict(svg, isHidden=True) if hide else svg
            r = json.loads(S.pbix_update_visual_json(alias, 0, names.index(name), json.dumps(cfg)))
            assert r["success"]
        out = str(tmp_path / "hidden.pbix")
        assert json.loads(S.pbix_save(alias, output_path=out, overwrite=True))["success"]
    finally:
        S.pbix_close(alias)
    v = _visuals(out)
    assert v["grp1"]["isHidden"] is True and "isHidden" not in v["grp1"]["visualGroup"]
    assert "isHidden" not in v["grp2"]
    assert v["grp2"]["visualGroup"] == {"displayName": "G2", "groupMode": "ScrollMode"}
