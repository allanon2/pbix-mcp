"""#334: CONVERT(<Int64>, INTEGER), FORMAT(<Int64>, ...) and a
DATATABLE INTEGER literal keep an Int64 past 2**53 exact; INT reads it as
its Double.

The engine did the opposite in each case: CONVERT went through
int(round(float(v))), FORMAT through float(val), DATATABLE through
int(float(raw)) -- all landing on 9007199254740992 -- while INT kept the
exact int. Power BI Desktop: CONVERT(9007199254740993, INTEGER) -
9007199254740992 is 1, RIGHT(FORMAT(9007199254740993, "0"), 3) is "993",
MAXX(DATATABLE("v", INTEGER, {{9007199254740993}}), [v]) - 9007199254740992
is 1, and INT(9007199254740993) - 9007199254740992 is 0. ISODD and VALUE of
the text work in Doubles (0), as the engine already did. FORMAT(<Int64>,
"General Number") prints the exact digits too (build_b257c.py f_general:
RIGHT(FORMAT(9007199254740993, "General Number"), 3) is "993"; we printed
9.00719925474099E+15).

Expected values: Power BI Desktop 2.152 over ADOMD, batteries
F:/_desktop_oracle/build_b257b.py group J (F:/_tmp_pbix/open2/b252-order53)
and build_b257c.py group F (F:/_tmp_pbix/rel148/desk/desk_b257c.json), by
probe id.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"T": {"columns": ["x"], "rows": [[1]]}}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


@pytest.mark.parametrize("probe,expr,want", [
    ('z53_int', 'INT(9007199254740993) - 9007199254740992', 0),
    ('z53_convert_int', 'CONVERT(9007199254740993, INTEGER) - 9007199254740992', 1),
    ('z53_format', 'RIGHT(FORMAT(9007199254740993, "0"), 3)', '993'),
    ('z53_datatable_int', 'MAXX(DATATABLE("v", INTEGER, {{9007199254740993}}), [v]) - 9007199254740992', 1),
    ('z53_isodd', 'IF(ISODD(9007199254740993), 1, 0)', 0),
    ('z53_value_txt', 'VALUE("9007199254740993") - 9007199254740992', 0),
])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


@pytest.mark.parametrize("probe,expr,want", [
    # build_b257c.py group F (F:/_tmp_pbix/rel148/desk/desk_b257c.json):
    # "General Number" prints an Int64's exact digits, as & does; we
    # printed its Double, 9.00719925474099E+15 ("+15")
    ('f_general', 'RIGHT(FORMAT(9007199254740993, "General Number"), 3)', '993'),
    ('f_concat', 'RIGHT(9007199254740993 & "", 3)', '993'),
])
def test_b257c_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


def test_general_number_of_an_int64_is_its_digits():
    """Exact on f_general's rule: every Int64 prints its own digits; a
    Double still prints 15 significant digits."""
    assert _ev('FORMAT(1234567890123456789, "General Number")') == '1234567890123456789'
    assert _ev('FORMAT(-42, "General Number")') == '-42'
    assert _ev('FORMAT(9007199254740993.0, "General Number")') == '9.00719925474099E+15'


def test_int_literal():
    """Exact arithmetic on the DATATABLE literal reader."""
    assert de._int_literal(" 9007199254740993 ") == 9007199254740993
    assert de._int_literal("2.0") == 2


@pytest.mark.parametrize("probe,expr,want", [
    ('f_general_neg', 'FORMAT(-9007199254740993, "General Number")', '-9007199254740993'),
    ('f_general_16', 'FORMAT(1234567890123456, "General Number")', '1234567890123456'),
])
def test_b257d_matches_desktop(probe, expr, want):
    """Power BI Desktop 2.152 (build_b257d.py group F,
    F:/_tmp_pbix/rel148/desk/desk_b257d.json): the exact digits of a negative
    and of a 16-digit Int64, not 15 significant digits."""
    assert _ev(expr) == want
