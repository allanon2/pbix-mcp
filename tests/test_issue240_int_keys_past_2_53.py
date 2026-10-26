"""Issue #240: an Int64 key past 2**53 matches its own value only.

float(9007199254740993) is 9007199254740992.0: past 2**53 a float no longer
holds every integer. The engine keyed a filter's numbers by their float
(_value_keys, _filter_value_keys), spelled an int key's float alternates
into the value index (_index_spellings, _join_key_aliases) and compared two
numbers as floats (_compare), so on an Int64 column holding
9007199254740992 and 9007199254740993 a filter on either selected both. An
int now stays an int -- Python compares and hashes an int and a float by
their exact values, so 2 is still 2.0 -- and gets a float spelling only
when that float is exactly the int.

Expected values: Power BI Desktop 2.152 over ADOMD (battery
F:/_desktop_oracle/build_b234.py, table TI with K = 9007199254740992 and
9007199254740993, A = 1 and 2; measures i_pred, i_filter, i_values; the last
two already matched). The other filter forms must agree with FILTER's
row-by-row answer, which compares Python numbers exactly and matched
Desktop; they are not measured one by one.

An int past 2**53 against a Double cell holding its rounded float does NOT
follow the exact comparison: Desktop converts an Int64 that meets a Double
to that Double (#325; build_b257.py l_lookup_di, the Double
column TD[D] searched with 9007199254740993 finds its 2**53.0, and
build_b257b.py c_dcol_eq_i, CALCULATE(..., TD[D] = 9007199254740993)
keeps it). test_numbers_a_float_holds_still_match_across_storage and
test_spelling_helpers assert that now; between two Int64 values every
assertion here stands.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

B = 2 ** 53                      # 9007199254740992, a float holds it exactly
TABLES = {
    "TI": {"columns": ["K", "A"], "rows": [[B, 1], [B + 1, 2]]},
    "D": {"columns": ["K", "N"], "rows": [[B, "a"], [B + 1, "b"], [B + 2, "c"]]},
    "F": {"columns": ["K", "A"], "rows": [[B, 1], [B + 1, 2], [B + 2, 4], [B + 1, 8]]},
    # a Double column: its float of 2**53 is that integer exactly
    "FD": {"columns": ["K", "A"], "rows": [[float(B), 16], [2024.0, 32]]},
}
RELS = [{"FromTable": "F", "FromColumn": "K", "ToTable": "D", "ToColumn": "K", "IsActive": 1}]


def _ev(expr, filter_context=None):
    return de.evaluate_measure("m", TABLES, {"m": expr}, filter_context=filter_context,
                               relationships=RELS)


DESKTOP = [
    ("i_pred", "CALCULATE(SUM(TI[A]), TI[K] = 9007199254740993)", 2),
    ("i_filter", "CALCULATE(SUM(TI[A]), FILTER(TI, TI[K] = 9007199254740993))", 2),
    ("i_values", "COUNTROWS(VALUES(TI[K]))", 2),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


@pytest.mark.parametrize("expr,want", [
    ("CALCULATE(SUM(TI[A]), TI[K] = 9007199254740992)", 1),
    ("CALCULATE(SUM(TI[A]), TI[K] <> 9007199254740993)", 1),
    ("CALCULATE(SUM(TI[A]), TI[K] IN {9007199254740993})", 2),
    ("CALCULATE(SUM(TI[A]), NOT TI[K] IN {9007199254740993})", 1),
    ("CALCULATE(SUM(TI[A]), TREATAS({9007199254740993}, TI[K]))", 2),
    ("CALCULATE(SUM(TI[A]), KEEPFILTERS(TI[K] = 9007199254740993))", 2),
    ("CALCULATE(SUM(TI[A]), TI[K] > 9007199254740992)", 2),
    ("COUNTROWS(FILTER(TI, TI[K] IN {9007199254740993}))", 1),
    ("LOOKUPVALUE(TI[A], TI[K], 9007199254740993)", 2),
    ('SWITCH(9007199254740993, 9007199254740992, "lo", 9007199254740993, "hi")', "hi"),
    ('COUNTROWS(DISTINCT(SELECTCOLUMNS(TI, "k", TI[K])))', 2),
    ("COUNTROWS(EXCEPT(VALUES(TI[K]), {9007199254740992}))", 1),
    ("DISTINCTCOUNT(TI[K])", 2),
])
def test_every_filter_form_agrees_with_filter(expr, want):
    assert _ev(expr) == want


def test_a_relationship_joins_each_key_to_its_own_rows():
    assert _ev('CALCULATE(SUM(F[A]), D[N] = "b")') == 2 + 8
    assert _ev('CALCULATE(SUM(F[A]), D[N] = "a")') == 1
    assert _ev('CALCULATE(SUM(F[A]), D[N] = "c")') == 4      # 2**53 + 2: a float holds it
    assert _ev("CALCULATE(SUM(F[A]), D[K] = 9007199254740993)") == 2 + 8
    assert _ev('SUMX(F, IF(RELATED(D[N]) = "b", F[A]))') == 2 + 8


def test_a_caller_filter_selects_its_own_key():
    assert _ev("SUM(TI[A])", {"TI.K": [B + 1]}) == 2
    assert _ev("SUM(TI[A])", {"TI.K": [str(B + 1)]}) == 2
    assert _ev("SUM(TI[A])", {"TI.K": [B]}) == 1


def test_numbers_a_float_holds_still_match_across_storage():
    assert _ev("CALCULATE(SUM(FD[A]), FD[K] = 9007199254740992)") == 16
    assert _ev("CALCULATE(SUM(FD[A]), FD[K] = 2024)") == 32           # issue #39
    # an Int64 that meets a Double compares as that Double, so 2**53 + 1
    # finds the Double cell 2**53.0 (Desktop, build_b257b.py c_dcol_eq_i "x",
    # build_b257.py l_lookup_di "x"; #325). It answered BLANK.
    assert _ev("CALCULATE(SUM(FD[A]), FD[K] = 9007199254740993)") == 16


def test_spelling_helpers():
    assert str(B) not in de._index_spellings(B + 1)
    assert set(de._index_spellings(B + 1)) == {str(B + 1)}
    assert set(de._index_spellings(str(B + 1))) == {str(B + 1)}
    assert set(de._index_spellings(B)) == {str(B), str(float(B))}
    assert set(de._index_spellings(2024)) == {"2024", "2024.0"}
    assert set(de._index_spellings("2024.0")) == {"2024.0", "2024"}
    assert de._join_key_aliases(B + 1) == {str(B + 1)}
    assert de._join_key_aliases(B) == {str(B), str(float(B))}
    assert de._value_keys(B + 1) != de._value_keys(B)
    # the first key is the value's identity; a whole Double past 2**53 also
    # answers to the Int64 filter values that meet it (#325)
    assert de._value_keys(B)[0] == de._value_keys(float(B))[0]
    assert ("dbl", float(B)) in de._value_keys(float(B))
    assert de._value_keys(2) == de._value_keys(2.0)
    assert ("num", B) not in de._filter_value_keys(B + 1)
    assert ("text", str(B + 1)) in de._filter_value_keys(B + 1)
    assert ("num", B + 1) in de._filter_value_keys(str(B + 1))
    assert de._exact_int(str(B + 1), float(B + 1)) == B + 1
    assert de._exact_int("12", 12.0) is None
    assert de._exact_int("1e16", 1e16) == 10 ** 16           # its float holds it: no change
    assert de._exact_int(str(B + 1) + ".0", float(B + 1)) == B + 1
    assert de._exact_int("9.007199254740993E15", float(B + 1)) == B + 1
    assert de._exact_int(str(B + 1) + ".5", float(B + 1)) is None
    assert set(de._index_spellings(str(B + 1) + ".0")) == {str(B + 1) + ".0", str(B + 1)}
    assert ("num", B + 1) in de._filter_value_keys(str(B + 1) + ".0")


def test_a_decimal_text_past_2_53_is_its_exact_integer():
    """'9007199254740993.0' spells 9007199254740993; int() refuses it, and
    its float is 9007199254740992.0, the other key."""
    for text in (str(B + 1) + ".0", "9.007199254740993E15"):
        assert _ev("SUM(TI[A])", {"TI.K": [text]}) == 2
        assert _ev(f'CALCULATE(SUM(TI[A]), TREATAS({{"{text}"}}, TI[K]))') == 2


def test_exact_int_is_asked_only_past_2_53(monkeypatch):
    """Within 2**53 a float holds every integer, so the spellings and the
    filter keys need no exact reading: _exact_int is not called (it cost
    about 30% of an uncached _index_spellings call)."""
    calls = []
    real = de._exact_int

    def counting(v, fv):
        calls.append(v)
        return real(v, fv)

    monkeypatch.setattr(de, "_exact_int", counting)
    for v in (2024, 2024.0, "2024", "2024.5", -7, 2.5, B - 1, -(B - 1)):
        de._index_spellings_uncached(v)
        de._filter_value_keys_uncached(str(v))
    assert calls == []
    de._index_spellings_uncached(B + 1)
    de._index_spellings_uncached(-B)
    de._filter_value_keys_uncached(str(B + 1))
    assert calls == [B + 1, -B, str(B + 1)]
