"""#274: a chain of comparisons groups from left to right.

Microsoft Learn, DAX operators: the comparison operators =, ==, <, >, <=, >=
and <> share one precedence, and "If the operators have equal precedence
value, they are ordered from left to right." So 0 = 0 = TRUE() is (0 = 0) =
TRUE(), TRUE (Power BI Desktop 2.152, build_b255d.py d1). IN binds tighter
than they do: Desktop fails IF(1 = 1 IN {TRUE()}, 1, 0), 1 = (1 IN {TRUE()}),
on comparing an Integer with TRUE (d11), where Learn's shared row would read
(1 = 1) IN {TRUE()}, 1.

Every reader of a comparison tried the operators in a fixed order and took
the first that split the text in exactly two: 0 = 0 = TRUE() split in three
and fell through to BLANK (IF(..., 1, 0) was 0), 1 < 2 = TRUE() was read as
1 < (2 = TRUE()), and CALCULATE(COUNTROWS(TM), TM[d] < 2 = TRUE()) filtered
TM[d] < (2 = TRUE()).

Expected values: exact arithmetic under the documented grouping over TM(n, d,
z) = (7, 2, 1), (7, 0, 0), (-7, 2, 1), (7, BLANK, 0), where BLANK = 0 is TRUE
(Learn: "All comparison operators except == treat BLANK as equal to number
0") and text compares ignoring case (issue #107), and Power BI Desktop 2.152
over ADOMD, F:/_desktop_oracle/build_b255d.py d1-d11 (quoted by id).
Comparing TRUE / FALSE with a number is an error (#279: d5,
d11; build_b133.py true_eq_1).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]}}


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    return ("ERROR", eng.eval_errors["p"]) if "p" in eng.eval_errors else (got, None)


@pytest.mark.parametrize("expr,want", [
    ("IF(0 = 0 = TRUE(), 1, 0)", 1),                     # (TRUE) = TRUE
    ("IF(0 = 1 = FALSE(), 1, 0)", 1),                    # (FALSE) = FALSE
    ("IF(1 < 2 = TRUE(), 1, 0)", 1),                     # (TRUE) = TRUE
    ("IF(1 = 2 <> FALSE(), 1, 0)", 0),                   # (FALSE) <> FALSE
    ('IF("a" = "A" = TRUE(), 1, 0)', 1),                 # (TRUE) = TRUE
    ("IF(1 = 1 = TRUE() = TRUE(), 1, 0)", 1),            # ((TRUE) = TRUE) = TRUE
    ("IF(2 > 1 == TRUE(), 1, 0)", 1),                    # (TRUE) == TRUE
])
def test_scalar_chains(expr, want):
    assert _engine(expr) == (want, None)


@pytest.mark.parametrize("expr,want", [
    # (TM[d] = 0) = TRUE(): d = 0 and BLANK
    ("CALCULATE(COUNTROWS(TM), TM[d] = 0 = TRUE())", 2),
    # (TM[d] < 2) = TRUE(): d = 0 and BLANK (BLANK < 2)
    ("CALCULATE(COUNTROWS(TM), TM[d] < 2 = TRUE())", 2),
    # (TM[d] = 2) = TRUE(): the two d = 2 rows
    ("COUNTROWS(FILTER(TM, TM[d] = 2 = TRUE()))", 2),
    # (TM[n] = 7) = FALSE(): the -7 row
    ("COUNTROWS(FILTER(TM, TM[n] = 7 = FALSE()))", 1),
    ("SUMX(TM, IF(TM[z] = 1 = TRUE(), TM[n]))", 0),      # 7 + -7
])
def test_chains_over_rows(expr, want):
    assert _engine(expr) == (want, None)


@pytest.mark.parametrize("expr,want", [
    # IN shares the row: 2 IN {1, 2} = TRUE() is (2 IN {1, 2}) = TRUE(). The
    # IN was split first, with the set {1, 2} = TRUE(), which failed as a
    # table of several values compared as a value (#275).
    ("IF(2 IN {1, 2} = TRUE(), 1, 0)", 1),
    ("IF(3 IN {1, 2} = FALSE(), 1, 0)", 1),
    ("IF(2 IN {1, 2} <> FALSE(), 1, 0)", 1),
    ("IF(COUNTROWS(TM) IN {4, 5} = TRUE(), 1, 0)", 1),
    ("IF(2 IN VALUES(TM[d]) = TRUE(), 1, 0)", 1),
    ("SWITCH(TRUE(), 2 IN {1, 2} = TRUE(), 1, 0)", 1),
    # IN strict (BLANK is not 0, #273): d = 2, 0, 2
    ("SUMX(TM, IF(TM[d] IN {0, 2} = TRUE(), 1, 0))", 3),
    ("COUNTROWS(FILTER(TM, TM[d] IN {0, 2} = TRUE()))", 3),
    ("CALCULATE(COUNTROWS(TM), TM[d] IN {0, 2} = TRUE())", 3),
])
def test_chains_with_in(expr, want):
    assert _engine(expr) == (want, None)


@pytest.mark.parametrize("expr", [
    "IF(1 = 1 IN {TRUE()}, 1, 0)",     # build_b255d.py d11: 1 = (1 IN {TRUE()})
    "IF(1 = 2 IN {FALSE()}, 1, 0)",    # the same shape: 1 = (2 IN {FALSE()})
])
def test_in_binds_tighter_than_comparisons(expr):
    """Desktop: "Function CONTAINSROW does not support comparing values of
    type True/False with values of type Integer." (d11, in English); the
    engine read (1 = 1) IN {TRUE()} and answered 1."""
    got, err = _engine(expr)
    assert got == "ERROR"
    assert "CONTAINSROW" in err and "True/False" in err and "Integer" in err


def test_in_then_comparison_matches_desktop():
    """build_b255d.py d9 (1) and d10 (3): IN applied first."""
    assert _engine("IF(2 IN {1, 2} = TRUE(), 1, 0)") == (1, None)
    assert _engine("CALCULATE(COUNTROWS(TM), TM[d] IN {0, 2} = TRUE())") == (3, None)


def test_a_chain_comparing_true_with_a_number_fails():
    """build_b255d.py d5: IF(2 = 2 = 2, 1, 0) is (2 = 2) = 2, TRUE against an
    Integer: "DAX comparison operations do not support comparing values of
    type True/False with values of type Integer." (#279; the
    engine answered 0)."""
    got, err = _engine("IF(2 = 2 = 2, 1, 0)")
    assert got == "ERROR"
    assert "True/False" in err and "Integer" in err


def test_one_comparison_is_unchanged():
    """A single comparison of each operator, by exact arithmetic over TM."""
    assert _engine("COUNTROWS(FILTER(TM, TM[d] <> 0))") == (2, None)     # BLANK <> 0 is FALSE
    assert _engine("COUNTROWS(FILTER(TM, TM[d] == 0))") == (1, None)
    assert _engine("COUNTROWS(FILTER(TM, TM[n] >= 7))") == (3, None)
    assert _engine("COUNTROWS(FILTER(TM, TM[n] <= -7))") == (1, None)
    assert _engine("CALCULATE(COUNTROWS(TM), TM[d] > 0)") == (2, None)
