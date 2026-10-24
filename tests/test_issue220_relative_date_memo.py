"""Issue #220: a relative_date filter without an anchor is memoized per day.

``{"relative_date": {"last": 2, "unit": "day"}}`` ends today
(_relative_date_bounds reads date.today()), but its signature was the spec
alone, and the memos keyed by it -- the model-wide rows of a predicate ('d'),
the cross-filter memo, the measure memo of a query cache -- outlive the day:
a server process that ran past midnight kept serving the first day's rows.
The signature of such a filter now carries today's date; an anchored window
does not move and keeps one signature.

The tests move the engine's clock: its module-level ``date`` is swapped for a
subclass whose today() they set (and which still answers isinstance for every
date), so 0.9.145 runs them unchanged. Expected values: the rows of the
window, worked out by hand, and the engine's answers on a fresh model."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

_REAL_DATE = date


class _ClockMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATE)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _REAL_DATE)


class _ClockDate(_REAL_DATE, metaclass=_ClockMeta):
    now = _REAL_DATE(2024, 1, 10)

    @classmethod
    def today(cls):
        return _ClockDate.now


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(de, "date", _ClockDate)
    monkeypatch.setattr(_ClockDate, "now", _REAL_DATE(2024, 1, 10))

    def set_day(d):
        _ClockDate.now = d
    return set_day


DAYS = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(20)]     # 1-20 January
LAST2 = {"relative_date": {"last": 2, "unit": "day"}}
RELS = [{"FromTable": "S", "FromColumn": "Date", "ToTable": "D", "ToColumn": "Date", "IsActive": 1,
         "FromCardinality": 2, "ToCardinality": 1}]
MEASURES = {"M": "SUM(S[Amt])"}


def _model():
    return {"S": {"columns": ["Date", "Amt"], "rows": [[d, d.day] for d in DAYS]},
            "D": {"columns": ["Date"], "rows": [[d] for d in DAYS]}}


# today -> SUM(S[Amt]) over the window [today - 2 days, today]
TIMELINE = [(_REAL_DATE(2024, 1, 10), 8 + 9 + 10),
            (_REAL_DATE(2024, 1, 1), 1),                  # 30 Dec - 1 Jan: only the 1st
            (_REAL_DATE(2024, 1, 25), None),              # past the data
            (_REAL_DATE(2024, 1, 20), 18 + 19 + 20)]


@pytest.mark.parametrize("key", ["S.Date", "D.Date"], ids=["direct", "across-relationship"])
def test_the_window_moves_with_the_day_on_one_model(clock, key):
    tables = _model()
    for today, want in TIMELINE:
        clock(today)
        fresh = de.evaluate_measure("M", _model(), MEASURES, {key: dict(LAST2)}, relationships=RELS)
        got = de.evaluate_measure("M", tables, MEASURES, {key: dict(LAST2)}, relationships=RELS)
        assert fresh == want, today
        assert got == want, today


def test_a_query_cache_kept_past_midnight(clock):
    tables, cache = _model(), {}
    for today, want in TIMELINE:
        clock(today)
        got = de.evaluate_measures_batch(["M"], tables, MEASURES, {"S.Date": dict(LAST2)},
                                         relationships=RELS, cache=cache)["M"]
        assert got == want, today


def test_only_a_window_without_an_anchor_signs_the_day(clock):
    sig = de.DAXContext._filter_sig
    anchored = {"relative_date": {"last": 2, "unit": "day", "anchor": "2024-01-05"}}
    nested = {"all": [{"op": ">", "value": 0}, LAST2]}
    clock(_REAL_DATE(2024, 1, 10))
    before = (sig(LAST2), sig(anchored), sig(nested), sig({"op": ">", "value": 0}))
    clock(_REAL_DATE(2024, 1, 11))
    after = (sig(LAST2), sig(anchored), sig(nested), sig({"op": ">", "value": 0}))
    assert before[0] != after[0] and before[2] != after[2]
    assert before[1] == after[1] and before[3] == after[3]


def test_an_anchored_window_does_not_move(clock):
    anchored = {"relative_date": {"last": 2, "unit": "day", "anchor": "2024-01-05"}}
    tables = _model()
    for today, _want in TIMELINE:
        clock(today)
        assert de.evaluate_measure("M", tables, MEASURES, {"S.Date": dict(anchored)},
                                   relationships=RELS) == 3 + 4 + 5
