"""Issue #43 follow-up: the case-fold must be VISIBLE, not silent.

VertiPaq's string store is case-insensitive, so the encoder folds values that
differ only by case onto one dictionary entry, keeping the first spelling seen
(without that fold Desktop refuses the whole model — "A duplicate value has been
detected in the Unique Value store"). The fold is correct and matches Desktop's
own import behaviour, but it REWRITES the caller's data: 'VAN DER SAR' and
'van der SAR' both store as the first spelling and DISTINCTCOUNT drops with it.
Silently altering supplied values is the failure mode this project's warning
channel exists to prevent, so the build says so.
"""
import json
import warnings

import pytest

from pbix_mcp import server as S

pytestmark = pytest.mark.unit


def _create(path, alias, rows, col="Player Name"):
    tables = [{"name": "Players",
               "columns": [{"name": col, "data_type": "String"},
                           {"name": "V", "data_type": "Int64"}],
               "rows": rows}]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resp = json.loads(S.pbix_create(path, alias,
                                        tables_json=json.dumps(tables)))
    msgs = [str(w.message) for w in caught]
    return resp, msgs


def _case_warnings(msgs):
    return [m for m in msgs if "differing only by case" in m]


def test_case_collision_is_reported(tmp_path):
    resp, msgs = _create(
        str(tmp_path / "c.pbix"), "cfw1",
        [{"Player Name": "VAN DER SAR", "V": 1},
         {"Player Name": "van der SAR", "V": 2},
         {"Player Name": "PELE", "V": 3}])
    try:
        assert resp["success"], resp          # the fold keeps the file loadable
        hits = _case_warnings(msgs)
        assert hits, f"no case-fold warning raised; got {msgs}"
        w = hits[0]
        assert "Players" in w and "Player Name" in w
        assert "VAN DER SAR" in w and "van der SAR" in w
    finally:
        S.pbix_close("cfw1")


def test_no_warning_without_a_collision(tmp_path):
    """Negative control — the flagged pair is the only difference."""
    resp, msgs = _create(
        str(tmp_path / "ok.pbix"), "cfw2",
        [{"Player Name": "VAN DER SAR", "V": 1},
         {"Player Name": "MARADONA", "V": 2},
         {"Player Name": "PELE", "V": 3}])
    try:
        assert resp["success"], resp
        assert not _case_warnings(msgs), msgs
    finally:
        S.pbix_close("cfw2")


def test_fold_still_applies_and_file_readable(tmp_path):
    """The warning does not change the (required) folding behaviour."""
    path = str(tmp_path / "f.pbix")
    resp, _ = _create(path, "cfw3",
                      [{"Player Name": "abc", "V": 1},
                       {"Player Name": "ABC", "V": 2},
                       {"Player Name": "Abc", "V": 4}])
    try:
        assert resp["success"], resp
        q = json.loads(S.pbix_query_table("cfw3", table_name="Players"))
        # All three rows survive; the STORED spelling is the first one seen.
        assert q["message"].count("abc") == 3, q["message"]
        assert "ABC" not in q["message"] and "Abc" not in q["message"]
    finally:
        S.pbix_close("cfw3")


def test_non_string_columns_are_not_scanned(tmp_path):
    """Ints/None must not trip the string-collision scan."""
    tables = [{"name": "T",
               "columns": [{"name": "N", "data_type": "Int64"},
                           {"name": "S", "data_type": "String"}],
               "rows": [{"N": 1, "S": None}, {"N": 2, "S": "x"}]}]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resp = json.loads(S.pbix_create(str(tmp_path / "n.pbix"), "cfw4",
                                        tables_json=json.dumps(tables)))
    try:
        assert resp["success"], resp
        assert not _case_warnings([str(w.message) for w in caught])
    finally:
        S.pbix_close("cfw4")
