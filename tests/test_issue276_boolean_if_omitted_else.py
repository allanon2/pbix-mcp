"""#276: the omitted value-if-false of an IF whose
value-if-true is TRUE / FALSE is FALSE, not BLANK.

Power BI Desktop 2.152 (F:/_desktop_oracle/build_b255c.py), 0.9.147 / 0.9.148
before this fix in brackets:

* IF(ISBLANK(IF(FALSE(), FALSE())), 1, 0) is 0 (y7) [1];
* COUNTROWS(FILTER(TM, ISBLANK(IF(TM[z] = 0, FALSE())))) is BLANK (y6) [2];
* SUMX(TM, IF(ISBLANK(IF(TM[z] = 0, FALSE())), 1, 0)) is 0 (y8) [2];
* COUNTROWS(FILTER(TM, COALESCE(IF(TM[z] = 0, FALSE()), TRUE()))) is BLANK
  (y9) [2]: the z = 1 rows' FALSE is no BLANK for COALESCE;
* which settles build_b255b.py y2, COUNTROWS(FILTER(TM, COALESCE(IF(TM[z] = 0,
  FALSE()), MOD(TM[n], TM[d]) = 1))): BLANK [2] -- COALESCE stops at the FALSE
  and never reaches MOD.

An IF over numbers keeps its BLANK (IF(FALSE(), 1) is BLANK, Microsoft Learn).
TM(n, d, z) = (7, 2, 1), (7, 0, 0), (-7, 2, 1), (7, BLANK, 0).
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
    ("COUNTROWS(FILTER(TM, ISBLANK(IF(TM[z] = 0, FALSE()))))", None),                    # y6
    ("IF(ISBLANK(IF(FALSE(), FALSE())), 1, 0)", 0),                                      # y7
    ("SUMX(TM, IF(ISBLANK(IF(TM[z] = 0, FALSE())), 1, 0))", 0),                          # y8
    ("COUNTROWS(FILTER(TM, COALESCE(IF(TM[z] = 0, FALSE()), TRUE())))", None),           # y9
    ("COUNTROWS(FILTER(TM, COALESCE(IF(TM[z] = 0, FALSE()), MOD(TM[n], TM[d]) = 1)))", None),  # b255b y2
], ids=["y6", "y7", "y8", "y9", "b255b_y2"])
def test_matches_desktop(expr, want):
    assert _engine(expr) == (want, None)


def test_a_number_if_keeps_its_blank():
    """Microsoft Learn (IF): "If omitted, BLANK is returned"."""
    assert _engine("IF(FALSE(), 1)") == (None, None)
    assert _engine('IF(FALSE(), "x")') == (None, None)
