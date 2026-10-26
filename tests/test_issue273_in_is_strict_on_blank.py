"""#273: IN matched BLANK against 0 (and a value against
BLANK); it compares strictly.

_eval_in tested membership with the = operator's comparison, where BLANK = 0
is TRUE, so a row whose TM[d] is BLANK was in TM[d] IN {0, 2}. Microsoft
Learn (CONTAINSROW): "Unlike the = operator, the IN operator and the
CONTAINSROW function perform strict comparison. For example, the BLANK value
does not match 0."

Expected values: Power BI Desktop 2.152 over ADOMD, F:/_desktop_oracle/
build_b255b.py k15 (CALCULATE(COUNTROWS(TM), TM[d] IN {0, 2} && TM[n] = 7) is
2), over TM(n, d, z) = (7, 2, 1), (7, 0, 0), (-7, 2, 1), (7, BLANK, 0); the
scalar cases follow the documented rule.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]}}


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    assert not eng.eval_errors
    return got


@pytest.mark.parametrize("expr,want", [
    ("CALCULATE(COUNTROWS(TM), TM[d] IN {0, 2} && TM[n] = 7)", 2),     # k15 (0.9.147: 4)
    ("IF(BLANK() IN {0, 2}, 1, 0)", 0),                                # build_b255c.py k26
    ("COUNTROWS(FILTER(TM, TM[d] IN {0, 2}))", 3),
    ("COUNTROWS(FILTER(TM, TM[d] IN {BLANK()}))", 1),
    ("IF(BLANK() IN {0, 2}, 1, 0)", 0),
    ("IF(0 IN {BLANK()}, 1, 0)", 0),
    ("IF(BLANK() IN {BLANK(), 2}, 1, 0)", 1),
    ("IF(0 IN {0, 2}, 1, 0)", 1),
])
def test_blank_matches_only_blank(expr, want):
    assert _engine(expr) == want
