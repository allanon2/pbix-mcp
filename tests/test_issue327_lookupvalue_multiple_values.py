"""#327: LOOKUPVALUE with several matching rows of different
results raises "A table of multiple values was supplied where a single
value was expected." -- or returns its alternateResult when one is given.

The engine returned the first matching row's result. Microsoft's
LOOKUPVALUE reference documents the error and the alternateResult; Power BI
Desktop 2.152 answers both, and IFERROR catches the error. Rows that all
hold one result return it.

Expected values: Power BI Desktop 2.152 over ADOMD, batteries
F:/_desktop_oracle/build_b257b.py (group E; F:/_tmp_pbix/open2/b252-order53)
and build_b257.py (l_lookup_id; F:/_tmp_pbix/open1/b252), by probe id. The
Norwegian message "Det ble oppgitt en tabell med flere verdier der en
enkelt verdi var ventet." is Desktop's text of the English message above.
TS = (1, p), (1, q), (2, r); TK[K] = 2**53 - 1 .. 2**53 + 4 labelled a .. f,
where the Double 2**53.0 finds 2**53 and 2**53 + 1 (an Int64 that meets a
Double compares as one, #325).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit

P = 2 ** 53
TABLES = {
    "TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"]]},
    "TK": {"columns": ["L", "K"], "rows": [["a", P - 1], ["b", P], ["c", P + 1], ["d", P + 2]]},
}
MESSAGE = "A table of multiple values was supplied where a single value was expected."


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


def _error(expr):
    """The engine's error for the measure (eval_errors), None when it answered."""
    eng = de.DAXEngine()
    eng.evaluate_measure("m", de.DAXContext(TABLES, {"m": expr}))
    return eng.eval_errors.get("m")


@pytest.mark.parametrize("probe,expr", [
    ("lv_dup_err", "LOOKUPVALUE(TS[L], TS[G], 1)"),
    ("l_lookup_id", "LOOKUPVALUE(TK[L], TK[K], 9007199254740992.0)"),
])
def test_several_results_are_an_error(probe, expr):
    assert MESSAGE in (_error(expr) or "")
    with pytest.raises(DAXEvaluationError, match="A table of multiple values was supplied"):
        de.DAXEngine()._eval_expr(expr, de.DAXContext(TABLES, {}))


@pytest.mark.parametrize("probe,expr,want", [
    ("lv_dup_alt", 'LOOKUPVALUE(TS[L], TS[G], 1, "alt")', "alt"),
    ("lv_dup_same", "LOOKUPVALUE(TS[G], TS[G], 1)", 1),
    ("lv_iferror", 'IFERROR(LOOKUPVALUE(TS[L], TS[G], 1), "caught")', "caught"),
    # one matching row, as before (build_b257.py)
    ("l_lookup_ii", "LOOKUPVALUE(TK[L], TK[K], 9007199254740993)", "c"),
])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want



def _many_tables(per=500, groups=4):
    return {"L": {"columns": ["grp", "grp2", "cat"],
                  "rows": [[i % groups, i % groups, "c%d" % (i % groups)] for i in range(groups * per)]},
            "S": {"columns": ["g"], "rows": [[i % groups] for i in range(200)]}}


def test_many_agreeing_rows_return_their_value():
    """lv_dup_same's rule over 500 matching rows a key: each key's rows all
    hold c<key>, so LEN of each is 2 and the sum over 200 rows 400 (exact
    arithmetic); one row of another value makes it the error, or the
    alternateResult."""
    tables = _many_tables()
    assert de.evaluate_measure("m", tables, {"m": "SUMX(S, LEN(LOOKUPVALUE(L[cat], L[grp], S[g])))"}) == 400
    other = _many_tables()                      # a new model: tables are not edited in place
    other["L"]["rows"].append([1, 1, "other"])
    assert de.evaluate_measure("m", other, {"m": 'LOOKUPVALUE(L[cat], L[grp], 1, "alt")'}) == "alt"
    assert de.evaluate_measure("m", other, {"m": 'LOOKUPVALUE(L[cat], L[grp], 2, "alt")'}) == "c2"
    assert de.evaluate_measure("m", other, {"m": 'LOOKUPVALUE(L[cat], L[grp], 1, L[cat], "c1")'}) == "c1"
    assert de.evaluate_measure("m", other, {"m": 'LOOKUPVALUE(L[cat], L[grp], 1, L[grp2], 1, "alt")'}) == "alt"


@pytest.mark.parametrize("expr", [
    "SUMX(S, LEN(LOOKUPVALUE(L[cat], L[grp], S[g])))",
    # two search columns
    "SUMX(S, LEN(LOOKUPVALUE(L[cat], L[grp], S[g], L[grp2], S[g])))",
])
def test_the_check_reads_no_row_by_identity_when_rows_agree(monkeypatch, expr):
    """The agreement check collects the distinct cells in C and memoizes its
    verdict per searched values: the first cut called
    _value_keys on the result of every matching row, every call -- 21 times
    slower at 1,000 rows a key. 200 lookups over 500 agreeing rows a key
    read no result cell through it (the integer calls are the search
    columns' index, built once)."""
    calls = []
    real = de._value_keys
    monkeypatch.setattr(de, "_value_keys", lambda v, d=True: calls.append(v) or real(v, d))
    assert de.evaluate_measure("m", _many_tables(), {"m": expr}) == 400
    assert sum(1 for v in calls if isinstance(v, str)) < 20


def test_text_differing_in_case_only_is_one_value():
    """"p" and "P" are one value to DAX's case-insensitive text comparison
    (issue #107; VertiPaq keeps one of them), so they agree."""
    tables = {"T": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "P"]]}}
    assert de.evaluate_measure("m", tables, {"m": "LOOKUPVALUE(T[L], T[G], 1)"}) in ("p", "P")
