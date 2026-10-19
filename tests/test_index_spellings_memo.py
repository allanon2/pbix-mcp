"""The index-map spellings of an In-set filter value are memoized by value
(issue #204; PR #201, by @allanon2): they depend on the value alone, and a
relationship carries a dimension's filter to its fact table as hundreds of
text keys on every call, each trying a date parse and a float(). The memo keys by type as
well as value (True == 1 == 1.0 in Python, each with its own spellings) and a
float by its repr (-0.0 == 0.0)."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

VALUES = ["opp-0001", "2024", "2024.0", " 7 ", "2024-01-31", "2024-01-31 00:00:00", "2024-01-31 12:30:00",
          "None", "", 2024, 2024.0, 2024.5, 0, 1, 0.0, -0.0, True, False, float("inf"), float("nan"),
          datetime(2024, 1, 31), datetime(2024, 1, 31, 12, 30), date(2024, 1, 31), None]


@pytest.mark.parametrize("v", VALUES, ids=repr)
def test_the_memo_answers_what_the_spellings_are(v):
    want = de._index_spellings_uncached(v)
    assert de._index_spellings(v) == want
    assert de._index_spellings(v) == want


def test_values_python_calls_equal_keep_their_own_spellings():
    de._INDEX_SPELLINGS.clear()
    assert de._index_spellings(1) == ("1", "1.0", "1")
    assert de._index_spellings(True) == ("True",)       # True never aliases 1 (#39)
    assert de._index_spellings(1.0) == ("1.0", "1.0", "1")
    assert de._index_spellings(0.0) == ("0.0", "0.0", "0")
    assert de._index_spellings(-0.0) == ("-0.0", "-0.0", "0")


def test_an_unhashable_value_is_spelled_without_the_memo():
    assert de._index_spellings([1]) == de._index_spellings_uncached([1])


def test_the_memo_starts_again_past_its_bound(monkeypatch):
    de._INDEX_SPELLINGS.clear()
    calls = []
    real = de._index_spellings_uncached
    monkeypatch.setattr(de, "_index_spellings_uncached", lambda v: calls.append(v) or real(v))
    monkeypatch.setattr(de, "_INDEX_SPELLINGS_MAX", 3)
    for v in ["a", "b", "a", "b", "c"]:
        de._index_spellings(v)
    assert calls == ["a", "b", "c"]
    de._index_spellings("d")                             # three held: starts again
    assert calls == ["a", "b", "c", "d"] and len(de._INDEX_SPELLINGS) == 1


def test_a_dimension_filter_through_a_relationship_finds_the_same_rows():
    tables = {"D": {"columns": ["K", "Name"], "rows": [["k1", "a"], ["k2", "b"], ["2024", "c"]]},
              "F": {"columns": ["K", "V"], "rows": [["k1", 1], ["k2", 2], ["k1", 4], ["2024", 8], [2024, 16]]}}
    rels = [{"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "K", "IsActive": 1}]
    for _ in range(2):                                   # cold, then from the memo
        ctx = de.DAXContext(tables, {"M": "SUM(F[V])"}, None, None, {"D.Name": ["a", "c"]}, rels)
        assert de.DAXEngine().evaluate_measure("M", ctx) == 1 + 4 + 8 + 16
