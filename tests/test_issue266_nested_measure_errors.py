"""#266: a referenced measure's error was degraded to BLANK;
Power BI Desktop fails the measure that references it.

evaluate_measure returned None for a nested measure (depth > 1) that raised,
recording the error only under the nested measure's name, so the caller went
on with a BLANK: VAR x = [Bad] RETURN x answered BLANK and SUMX(TM, [m_and])
2. Desktop fails the caller with the nested measure's error; IFERROR /
ISERROR around the reference still catch it, and an unused reference is
never evaluated.

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v",
[probe]) per probe -- F:/_desktop_oracle/build_b134.py (measure_used:
"Calculation error in measure 'T'[Bad]: Cannot convert value 'x' of type Text
to type Number"; measure_unused 10, isblank_unused 11, iferror_measure_err 13,
iserror_measure_err 1) and build_b255.py (x18, f33, i12: "Calculation error in
measure 'TM'[m_mod]: Division by zero ... 'MOD'"). The B255 probes also need
issue #249's eager logic.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

ERROR = "ERROR"
T_TABLES = {"T": {"columns": ["c", "v"], "rows": [["a", 10.0], ["b", 20.0], ["c", 30.0]]}}
T_MEASURES = {"Bad": '"x" * 2', "Blank": "BLANK()"}
TM_TABLES = {"TM": {"columns": ["n", "d", "z"],
                    "rows": [[7, 2, 1], [7, 0, 0], [-7, 2, 1], [7, None, 0]]}}
TM_MEASURES = {
    "m_mod": "MOD(SELECTEDVALUE(TM[n]), SELECTEDVALUE(TM[d]))",
    "m_and": "IF(SELECTEDVALUE(TM[d]) <> 0 && [m_mod] = 1, 1)",
}


def _engine(tables, measures, dax):
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(tables, {**measures, "p": dax}))
    if "p" in eng.eval_errors:
        return ERROR, eng.eval_errors["p"]
    return got, None


@pytest.mark.parametrize("expr,want,word", [
    ("VAR x = [Bad] RETURN x", ERROR, "Cannot convert value 'x'"),        # measure_used
    ("VAR x = [Bad] RETURN 10", 10, None),                                # measure_unused
    ("VAR x = [Bad] RETURN IF(ISBLANK(BLANK()), 11, x)", 11, None),       # isblank_unused
    ("IFERROR([Bad], 13)", 13, None),                                     # iferror_measure_err
    ("IF(ISERROR([Bad]), 1, 0)", 1, None),                                # iserror_measure_err
    ("IFERROR([Blank], 14)", None, None),                                 # iferror_blank_measure
], ids=["measure_used", "measure_unused", "isblank_unused", "iferror_measure_err",
        "iserror_measure_err", "iferror_blank_measure"])
def test_b134(expr, want, word):
    got, err = _engine(T_TABLES, T_MEASURES, expr)
    assert got == want
    if word is not None:
        assert word in err


@pytest.mark.parametrize("expr", [
    "SUMX(TM, [m_and])",                                    # x18
    "COUNTROWS(FILTER(TM, TM[d] = 0 || [m_mod] = 1))",      # f33
    "SUMX(TM, IF(TM[d] <> 0 && [m_mod] = 1, 1))",           # i12
], ids=["x18", "f33", "i12"])
def test_b255_the_nested_error_fails_the_caller(expr):
    got, err = _engine(TM_TABLES, TM_MEASURES, expr)
    assert got == ERROR and "MOD" in err
