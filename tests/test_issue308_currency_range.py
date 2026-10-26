"""#308 (#248): the Fixed Decimal (Currency) range. Integer
Currency arithmetic -- Currency +- Currency / Int64, Currency * Int64, an
Int64 converted, a SUM -- wraps around the signed 64-bit count of 1/10,000;
a conversion from a Double past the range is Desktop's error "The result of
a conversion or arithmetic operation is either too large or too small.".

What was wrong (0.9.147): there was no range: CURRENCY(1E15) was 1E15,
CURRENCY(5E14) + CURRENCY(5E14) 1E15, SUM over three rows of 4E14 1.2E15.

Expected values: Power BI Desktop 2.152's g() cells over ADOMD (type letter
+ FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim): build_b254.py
o_scalar and o_column (TBig: three rows of 4E14); build_b254b.py g_ovf. The
raw probes build_b254b.py g_ovf_conv (CURRENCY(1E15)) and g_ovf_mul
(CURRENCY(5E14) * 2.5) fail with "Resultatet av en konverteringsoperasjon
eller en aritmetisk operasjon er enten for stort eller for lite." -- in
English the message above.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def _tables():
    tables = {"TBig": {"columns": ["v"], "rows": [[4e14]] * 3}}
    de.mark_currency_columns(tables, {"TBig": ["v"]})
    return tables


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(_tables(), {}, None, None, None, []))


CASES = [
    # build_b254.py o_scalar
    ("CURRENCY(922337203685477)", "C922337203685477.000000000"),
    ("CURRENCY(1E15)", "E"),
    ("CURRENCY(5E14) + CURRENCY(5E14)", "C-844674407370955.000000000"),
    ("CURRENCY(5E14) * 2", "C-844674407370955.000000000"),
    ("CURRENCY(5E14) * 2.5", "E"),
    ("CURRENCY(5E14) / 0.1", "E"),
    ("CURRENCY(5E14) + 5E14", "D1000000000000000.000000000"),
    ("SUMX(GENERATESERIES(1, 3), CURRENCY(4E14))", "C-644674407370955.000000000"),
    # build_b254.py o_column
    ("SUM(TBig[v])", "C-644674407370955.000000000"),
    ("SUMX(TBig, TBig[v])", "C-644674407370955.000000000"),
    ("AVERAGE(TBig[v])", "C-214891469123652.000000000"),
    ("SUMX(TBig, TBig[v] * 1)", "C-644674407370955.000000000"),
    ("MAX(TBig[v]) * 3", "C-644674407370955.000000000"),
    # build_b254b.py g_ovf
    ("CURRENCY(1) + 1000000000000000", "C-844674407370954.000000000"),
    ("CONVERT(1000000000000000, CURRENCY)", "C-844674407370955.000000000"),
    ("CURRENCY(5E14) * 2.0", "E"),
    ('-CURRENCY("-922337203685477.5808")', "C-922337203685478.000000000"),
    ("CURRENCY(9E14) + CURRENCY(9E14) - CURRENCY(9E14)", "C900000000000000.000000000"),
    ("SUMX(GENERATESERIES(1, 3), CURRENCY(4E14)) / 3", "C-214891469123652.000000000"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_cells(expr, desktop):
    assert _ev(g(expr)) == desktop


@pytest.mark.parametrize("expr", ["CURRENCY(1E15)", "CURRENCY(5E14) * 2.5"])
def test_the_overflow_message(expr):
    with pytest.raises(DAXEvaluationError,
                       match="either too large or too small"):
        _ev(expr)


def test_the_wrapped_counts_are_exact():
    # exact arithmetic: 5E18 + 5E18 counts wrapped is 10^19 - 2^64; three rows
    # of 4E18 counts 12E18 - 2^64; their third rounded half away
    assert de._cur_count(_ev("CURRENCY(5E14) + CURRENCY(5E14)")) == 10 ** 19 - 2 ** 64
    assert de._cur_count(_ev("SUM(TBig[v])")) == 12 * 10 ** 18 - 2 ** 64
    assert de._cur_count(_ev("AVERAGE(TBig[v])")) == -2148914691236517205
