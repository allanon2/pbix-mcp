"""Issue #66: `pbix_format_visual` named the cards it applied but not WHERE.

The report was that the four cards `title` / `background` / `border` /
`padding` "never reach the saved file". They do — they reach
`singleVisual.vcObjects`. The reporter verified against
`singleVisual.objects` alone, saw an empty dict, and reasonably concluded
the write had been dropped. That misdiagnosis is the defect worth fixing:
a success message naming specific keys invites a caller to trust it, and
nothing in the response said which bucket to look in.

Power BI splits visual formatting across two buckets and the split is
Desktop's, not ours. Censused across 167 corpus reports:

    card             objects   vcObjects
    title                  0         876
    background             1         935
    border                 0         265
    padding               83         276
    labels               167           0
    categoryLabels       176           0
    visualHeader           0         350

So these tests pin two things: that every reported card really is written
(in whichever bucket Desktop uses), and that the response says which.
"""
import json

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit

#: The reporter's exact payload.
REPRO = {
    "title": {"show": False},
    "background": {"show": True, "color": "#FFFFFF", "transparency": 0},
    "border": {"show": True, "color": "#E3E9EF", "width": 1, "radius": 4},
    "padding": {"top": 6, "bottom": 6, "left": 12, "right": 12},
}
CONTAINER_CARDS = {"title", "background", "border", "padding"}
DATA_CARDS = {"labels", "categoryLabels"}


@pytest.fixture()
def carded(tmp_path):
    alias = "i66_" + tmp_path.name[-6:]
    p = str(tmp_path / "seed.pbix")
    assert json.loads(S.pbix_create(p, alias, json.dumps([{
        "name": "T", "columns": [{"name": "V", "data_type": "Double"}],
        "rows": [{"V": 1.0}]}])))["success"]
    assert json.loads(S.pbix_add_visual(
        alias, 0, "card", 40, 40, 280, 156, ""))["success"]
    yield alias, p, tmp_path
    for a in (alias, alias + "_r"):
        S._open_files.pop(a, None)
        S._dax_cache.pop(a, None)


def _reopen_single_visual(alias, p, tmp_path):
    out = str(tmp_path / "saved.pbix")
    assert json.loads(S.pbix_save(
        alias, output_path=out, overwrite=True))["success"]
    S.pbix_close(alias)
    a2 = alias + "_r"
    assert json.loads(S.pbix_open(out, a2))["success"]
    raw = json.loads(json.loads(S.pbix_get_layout_raw(a2))["message"])
    return json.loads(raw["sections"][0]["visualContainers"][0][
        "config"])["singleVisual"]


class TestEveryReportedCardIsActuallyWritten:
    """The claim the message makes must hold across BOTH buckets."""

    def test_repro_payload_all_four_persist(self, carded):
        alias, p, tmp = carded
        r = json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps(REPRO)))
        assert r["success"], r
        sv = _reopen_single_visual(alias, p, tmp)
        written = set(sv.get("objects") or {}) | set(sv.get("vcObjects") or {})
        assert CONTAINER_CARDS <= written, (
            f"reported but missing: {sorted(CONTAINER_CARDS - written)}")

    def test_container_cards_land_in_vcobjects(self, carded):
        """Where Desktop puts them: title 876/0, background 935/1,
        border 265/0, padding 276/83 in favour of vcObjects."""
        alias, p, tmp = carded
        assert json.loads(S.pbix_format_visual(
            alias, 0, 0, json.dumps(REPRO)))["success"]
        sv = _reopen_single_visual(alias, p, tmp)
        assert CONTAINER_CARDS <= set(sv.get("vcObjects") or {})

    def test_data_cards_land_in_objects(self, carded):
        """The other half of the split — labels 0/167, categoryLabels
        0/176 the other way."""
        alias, p, tmp = carded
        assert json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps({
            "labels": {"color": "#111111", "fontSize": 28},
            "categoryLabels": {"color": "#666666", "fontSize": 11},
        })))["success"]
        sv = _reopen_single_visual(alias, p, tmp)
        assert DATA_CARDS <= set(sv.get("objects") or {})

    def test_mixed_call_writes_every_card(self, carded):
        """The reporter's localising case: six cards sent, all six written,
        split across the two buckets — none dropped."""
        alias, p, tmp = carded
        sent = dict(REPRO)
        sent.update({"labels": {"fontSize": 28},
                     "categoryLabels": {"fontSize": 11}})
        assert json.loads(S.pbix_format_visual(
            alias, 0, 0, json.dumps(sent)))["success"]
        sv = _reopen_single_visual(alias, p, tmp)
        written = set(sv.get("objects") or {}) | set(sv.get("vcObjects") or {})
        assert set(sent) <= written, sorted(set(sent) - written)


class TestResponseSaysWhichBucket:
    """The actual fix: make the success claim checkable."""

    def test_data_names_both_buckets(self, carded):
        alias, _p, _t = carded
        sent = dict(REPRO)
        sent["labels"] = {"fontSize": 28}
        r = json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps(sent)))
        assert r["success"], r
        data = r["data"]
        assert set(data["vcObjects"]) == CONTAINER_CARDS
        assert data["objects"] == ["labels"]
        # and `applied` still lists everything, for callers reading it
        assert set(data["applied"]) == set(sent)

    def test_message_names_the_buckets(self, carded):
        alias, _p, _t = carded
        r = json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps(REPRO)))
        msg = r["message"]
        assert "vcObjects:" in msg, msg
        # a caller reading the message now learns where to verify
        for card in CONTAINER_CARDS:
            assert card in msg

    def test_data_matches_what_the_file_holds(self, carded):
        """The response and the saved file must not be able to disagree —
        that disagreement is the whole issue."""
        alias, p, tmp = carded
        sent = dict(REPRO)
        sent["categoryLabels"] = {"fontSize": 11}
        r = json.loads(S.pbix_format_visual(alias, 0, 0, json.dumps(sent)))
        data = r["data"]
        sv = _reopen_single_visual(alias, p, tmp)
        assert set(data["objects"]) <= set(sv.get("objects") or {})
        assert set(data["vcObjects"]) <= set(sv.get("vcObjects") or {})
