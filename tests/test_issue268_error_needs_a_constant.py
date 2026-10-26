"""#268: ERROR() with an argument that is not a constant --
Power BI Desktop refuses the query, whether the ERROR is reached or not.

The engine evaluated ERROR's argument when the call ran and raised its text
as the message, and an ERROR in a branch never taken did nothing. Desktop
answers "Function 'ERROR' expects argument number 1 to be a constant" (its
Norwegian "Funksjonen ERROR forventer at argumentnummeret 1 er konstant.",
in English) for ERROR(FORMAT(...)) in an untaken IF branch, in a row
expression whose branch is never or sometimes taken, and for ERROR("a" &
"b"). A text literal still raises its own text; a number literal is
refused too (ERROR(5), build_b255c.py y13), and IFERROR does not catch the
refusal (y14).

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v",
[probe]) per probe -- F:/_desktop_oracle/build_b255.py (e13; e9 for the
literal: message "x"), build_b255b.py (e16, e17, e18, e20) and
build_b255c.py (y13, y14), over TM(n, d, z) = (7, 2, 1), (7, 0, 0), (-7, 2,
1), (7, BLANK, 0).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"TM": {"columns": ["n", "d", "z"], "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]}}
HOME = {"m_zero": "COUNTROWS(ALL(TM)) - 4"}


def _error(dax, measures=None):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {**HOME, **(measures or {}), "p": dax}))
    assert got is None
    return eng.eval_errors.get("p")


@pytest.mark.parametrize("expr", [
    'SUMX(TM, IF(TM[d] = 99 && ERROR(FORMAT(TM[n], "0")), 1))',      # e13
    'IF([m_zero] = 1, ERROR(FORMAT([m_zero], "0")), 0)',             # e16: never taken
    'SUMX(TM, IF(TM[d] = 99, ERROR(FORMAT(TM[n], "0"))))',           # e17: never taken
    'ERROR("a" & "b")',                                               # e18
    'SUMX(TM, IF(TM[d] = 0, ERROR(FORMAT(TM[n], "0"))))',            # e20: taken
    'IF([m_zero] = 1, ERROR(5), 0)',                                  # b255c y13: a number
    'IFERROR(ERROR("a" & "b"), -1)',                                  # b255c y14: not caught
], ids=["e13", "e16", "e17", "e18", "e20", "y13", "y14"])
def test_refused_wherever_it_stands(expr):
    assert "expects argument number 1 to be a constant" in _error(expr)


def test_a_literal_raises_its_text():
    """B255 e9: SUMX(TM, IF(TM[d] = 99 && ERROR("x"), 1)) fails with "x"."""
    err = _error('SUMX(TM, IF(TM[d] = 99 && ERROR("x"), 1))')
    assert err.endswith("x") and "constant" not in err
