"""#277: DIVIDE's date alternate result is a number.

Power BI Desktop 2.152 (F:/_desktop_oracle/build_b255c.py) answers DIVIDE(1, 0,
DATE(2020, 1, 1)) with 43831 (y18), the date's serial number; the engine
returned the date. TRUE() stays TRUE (y17), as text stays text (build_b139.py:
DIVIDE(3, 0, "7") is "7").
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"T": {"columns": ["v"], "rows": [[1]]}}


def _engine(dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    return ("ERROR", eng.eval_errors["p"]) if "p" in eng.eval_errors else (got, None)


def test_y18_a_date_is_its_serial_number():
    got, err = _engine("DIVIDE(1, 0, DATE(2020, 1, 1))")
    assert err is None and isinstance(got, float) and got == 43831


def test_y17_true_stays_true():
    assert _engine("DIVIDE(1, 0, TRUE())") == (True, None)


def test_the_alternate_is_unused_when_the_division_works():
    assert _engine("DIVIDE(4, 2, DATE(2020, 1, 1))") == (2, None)
