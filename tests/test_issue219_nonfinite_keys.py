"""Issue #219: a NaN or infinite relationship key no longer blanks the model.

_join_key_aliases tested a float key with ``v == int(v)``; int() raises on
NaN (ValueError) and on infinity (OverflowError). blank_row_tables reaches it
for every relationship of the model, through _has_unmatched_keys and
_key_aliases, and caches nothing it failed on, so ONE such key on the one side
of ANY relationship made every cross-filtered measure of the model BLANK --
also across relationships with clean keys. A non-finite key is now spelled
as itself ('nan', 'inf', '-inf'), as _jstr spells it on the many side.

Expected values: the sums of the rows each filter keeps, worked out by hand,
and the engine's answers on the same model with the bad key replaced by a
finite key no fact row holds -- a one-side key that joins nothing changes no
answer. Where a non-finite key on the MANY side lands (on the one side's
row of the same spelling or on the blank row) has no Desktop probe: only that
the row lands on exactly one of them is asserted.

Spelled 'nan', a fact row's NaN joins the one side's NaN row for the filters
a relationship carries, also when the two sides hold two NaN objects, as
decoded columns do. RELATED looks a key up by value (_rel_key), and a NaN
equals no other NaN object: from a fact NaN it reaches the one side's row
only when both hold the same object. The two disagree; neither is asserted,
since Desktop's answer for a NaN key is unprobed -- the tests check that
every path answers."""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

NAN, INF = float("nan"), float("inf")
BAD = [NAN, INF, -INF]
IDS = ["nan", "inf", "-inf"]


def _model(one_key, fact_key=None):
    d = {"columns": ["Key", "Cat"], "rows": [[1.0, "a"], [2.0, "a"], [one_key, "b"]]}
    f = {"columns": ["K", "Amt"], "rows": [[1.0, 10.0], [2.0, 5.0], [3.0, 1.0]]
         + ([[fact_key, 1000.0]] if fact_key is not None else [])}
    return {"D": d, "F": f}


def _rels(both=False):
    rel = {"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "Key", "IsActive": 1,
           "FromCardinality": 2, "ToCardinality": 1}
    if both:
        rel["CrossFilteringBehavior"] = 2
    return [rel]


MEASURES = {
    "a": 'CALCULATE(SUM(F[Amt]), D[Cat] = "a")',
    "b": 'CALCULATE(SUM(F[Amt]), D[Cat] = "b")',
    "blank": "CALCULATE(SUM(F[Amt]), ISBLANK(D[Cat]))",
    "keys": "COUNTROWS(VALUES(D[Key]))",
    "cats": "COUNTROWS(VALUES(D[Cat]))",
    "related": 'SUMX(F, IF(RELATED(D[Cat]) = "a", F[Amt], 0))',
    "related_b": 'SUMX(F, IF(RELATED(D[Cat]) = "b", F[Amt], 0))',
    "relatedtable": "SUMX(D, COUNTROWS(RELATEDTABLE(F)))",
    "dims": "CALCULATE(COUNTROWS(D), F[Amt] > 0)",
}


def _all(tables, rels):
    return {m: de.evaluate_measure(m, tables, MEASURES, relationships=rels) for m in MEASURES}


@pytest.mark.parametrize("bad", BAD, ids=IDS)
def test_a_non_finite_key_is_spelled_as_itself(bad):
    assert de._join_key_aliases(bad) == {repr(bad)}
    assert {"2", "2.0"} <= de._join_key_aliases(2.0)          # a whole float keeps its int spelling


@pytest.mark.parametrize("both", [False, True], ids=["single", "both-ways"])
@pytest.mark.parametrize("bad", BAD, ids=IDS)
def test_a_non_finite_one_side_key_changes_no_answer(bad, both):
    got = _all(_model(bad), _rels(both))
    # D[Cat] = "a" keeps keys 1 and 2: 10 + 5; key 3 matches no D row and
    # lands on the blank row; the bad key's row "b" joins nothing
    assert (got["a"], got["b"], got["blank"]) == (15.0, None, 1.0)
    assert got == _all(_model(99.0), _rels(both))


# (one-side key, fact key): one NaN object on both sides, or two, as two
# decoded columns hold them
LANDING = [(NAN, NAN), (NAN, float("nan")), (INF, INF), (-INF, -INF)]


@pytest.mark.parametrize("one,fact", LANDING, ids=["nan", "nan-two-objects", "inf", "-inf"])
def test_a_non_finite_key_lands_on_one_row(one, fact):
    got = _all(_model(one, fact), _rels())
    assert got["a"] == 15.0
    # the fact row of key `fact` reaches the "b" row or the blank row, once
    assert (got["b"] or 0) + (got["blank"] or 0) == 1001.0


def test_one_bad_key_leaves_other_relationships_alone():
    tables = {"D1": {"columns": ["Key", "Cat"], "rows": [[1.0, "a"], [NAN, "b"]]},
              "F1": {"columns": ["K", "Amt"], "rows": [[1.0, 10]]},
              "D2": {"columns": ["Key", "Cat"], "rows": [[1, "x"], [2, "y"]]},
              "F2": {"columns": ["K", "Amt"], "rows": [[1, 5], [2, 7], [1, 11]]}}
    rels = [{"FromTable": "F1", "FromColumn": "K", "ToTable": "D1", "ToColumn": "Key", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1},
            {"FromTable": "F2", "FromColumn": "K", "ToTable": "D2", "ToColumn": "Key", "IsActive": 1,
             "FromCardinality": 2, "ToCardinality": 1}]
    measures = {"S2": "SUM(F2[Amt])"}
    got = de.evaluate_measures_batch(["S2"], tables, measures, {"D2.Cat": ["x"]}, relationships=rels)
    assert got["S2"] == 16                                    # 5 + 11, as without the D1 relationship
    ctx = de.DAXContext(tables, measures, None, None, {"D2.Cat": ["x"]}, rels)
    assert ctx.blank_row_tables() == frozenset()              # every F key has its D row
