"""#331: IF / SWITCH / COALESCE type a branch that is a mix of
+ and - (or * and /) by its operators.

IF, SWITCH and COALESCE give their result the type every result branch
unifies to -- an Int64 beside a Double branch is a Double, beside a
Currency branch a Currency (Power BI Desktop 2.152, build_b257b.py if_unify:
IF(TC[L] = "c", TC[K], TC[K] + 0.0) is the Double 2**53 for TC[K] = 2**53 +
1; build_b254b.py c_if, c_coalesce) -- and the branch not taken is typed from
its text. A chain of one precedence level that mixes its operators, a + b -
c or a * b / c, is one plan step with one operator per part
(#335), and the typing read that tuple as one operator and
gave the branch no type: IF(TRUE(), 1, 2.5 + 1 - 1) stayed the Int64 1,
IF(TRUE(), 1, CURRENCY(2) + 1 - 1) the Int64 1, IF(TRUE(), 1, 6 / 2 * 1)
the Int64 1 -- where a + / - with a Double is a Double, with a Currency (and
no Double) a Currency, and a / a Double (build_b254.py t_add, t_sub, t_div).

Expected values: Desktop's measured rules above (if_unify, c_if; t_add,
t_sub, t_div) applied to each branch. The same chains with one operator were
typed before and are the control.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

P = 2 ** 53
TABLES = {"T": {"columns": ["x"], "rows": [[1]]}}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


@pytest.mark.parametrize("expr,kind", [
    ("IF(TRUE(), 1, 2.5 + 1 - 1)", float),
    ("IF(TRUE(), 1, 1 - 2.5 + 1)", float),
    ("IF(TRUE(), 1, 6 / 2 * 1)", float),
    ("IF(TRUE(), 1, 6 * 2 / 1)", float),
    ("IF(TRUE(), 1, CURRENCY(2) + 1 - 1)", de._Currency),
    ("SWITCH(TRUE(), TRUE(), 1, 2.5 - 1 + 1)", float),
    ("COALESCE(1, 2.5 + 1 - 1)", float),
    # controls: one operator a chain
    ("IF(TRUE(), 1, 2.5 + 1 + 1)", float),
    ("IF(TRUE(), 1, 6 / 2 / 1)", float),
    ("IF(TRUE(), 1, 2 + 1 - 1)", int),
])
def test_branch_types(expr, kind):
    got = _ev(expr)
    assert got == 1 and type(got) is kind


def test_past_2_53():
    """The Int64 branch becomes the Double 2**53 (if_unify's arithmetic)."""
    assert _ev(f"IF(TRUE(), {P + 1}, 2.5 + 1 - 1) - {P}") == 0


def _typ(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISCURRENCY({x}), "C", IF(ISDOUBLE({x}), "D", '
            f'IF(ISINT64({x}), "I", "?"))))')


@pytest.mark.parametrize("probe,expr,want", [
    ('d_if_addsub', _typ('IF(TRUE(), 1, 2.5 + 1 - 1)'), 'D'),
    ('d_if_muldiv', _typ('IF(TRUE(), 1, 6 / 2 * 1)'), 'D'),
    ('d_if_cur', _typ('IF(TRUE(), 1, CURRENCY(2) + 1 - 1)'), 'C'),
])
def test_b257d_matches_desktop(probe, expr, want):
    """Power BI Desktop 2.152 (build_b257d.py group D,
    F:/_tmp_pbix/rel148/desk/desk_b257d.json) types the mixed chains as the
    unification above does."""
    assert _ev(expr) == want
