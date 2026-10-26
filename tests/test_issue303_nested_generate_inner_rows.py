"""#303: GENERATE over GENERATE's rows keeps both inner
rows.

What was wrong (0.9.147 and before): GENERATE adds its inner row's keys to
the outer row prefixed ``_inner_``; when the outer row was itself a GENERATE
row, the new inner row's keys overwrote the old ones, so a column of the
first inner table read as a (table, column) marker (or the wrong row):
CONCATENATEX(GENERATE(GENERATE(D, FILTER(E, E[k] < 2)), FILTER(T, T[k] =
1)), E[q] & "") printed "('E', 'q')".

Expected values: Power BI Desktop 2.152, build_b248g.py g4_nested (below);
DAX's semantics, each joined row's own value. Model as the G1 tests.

Desktop's b248g answer (test_desktop_b248g_g4_nested, build_b248g.py
g4_nested verbatim): a1.5/0.1,a2.5/0.1,b1.5/0.1,b2.5/0.1, as predicted.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def _ev(expr):
    tables = {
        "T": {"columns": ["c", "w", "k", "g", "id"],
              "rows": [[0.1, 0.1, 1, "a", 1], [0.1, 0.15, 2, "a", 2], [0.2, 0.2, 3, "b", 3],
                       [1.2345, 2.5, 4, "b", 4]]},
        "D": {"columns": ["g", "p"], "rows": [["a", 0.1], ["b", 0.2]]},
        "E": {"columns": ["k", "q"], "rows": [[1, 1.5], [2, 2.5], [3, 3.5], [4, 4.5]]},
    }
    de.mark_currency_columns(tables, {"T": ["c"], "D": ["p"], "E": ["q"]})
    rels = [{"FromTable": "T", "FromColumn": "g", "ToTable": "D", "ToColumn": "g", "IsActive": 1},
            {"FromTable": "T", "FromColumn": "k", "ToTable": "E", "ToColumn": "k", "IsActive": 1}]
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(tables, {}, None, None, None, rels))


def test_both_inner_rows_read():
    assert _ev('CONCATENATEX(GENERATE(GENERATE(D, FILTER(E, E[k] < 3)), FILTER(T, T[k] = 1)), '
               'D[g] & E[q] & "/" & T[c] & "/" & [q], ",", D[g], ASC, E[q], ASC)') == (
        "a1.5/0.1/1.5,a2.5/0.1/2.5,b1.5/0.1/1.5,b2.5/0.1/2.5")


def test_sum_over_both_inner_tables():
    # 2 D rows x (1.5 + 2.5) x 1 T row (k = 1): 8, exact
    assert _ev('SUMX(GENERATE(GENERATE(D, FILTER(E, E[k] < 3)), FILTER(T, T[k] = 1)), E[q] * T[k]) & ""') == "8"


def test_cells_in_order():
    rows = _ev('GENERATE(GENERATE(FILTER(D, D[g] = "a"), FILTER(E, E[k] = 1)), FILTER(T, T[k] = 1))')
    assert [de.DAXEngine._row_cells(r) for r in rows] == [["a", 0.1, 1, 1.5, 0.1, 0.1, 1, "a", 1]]


def _tc248(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def _g248(x):
    return f'IF(ISERROR({x}), "E", {_tc248(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def _t248(x):
    return f'IFERROR(({x}) & "", "E")'


def _cells248(*xs):
    return ' & " " & '.join(xs)


def test_desktop_b248g_g4_nested():
    # build_b248g.py g4_nested, Power BI Desktop 2.152 (desk_b248g.json)
    assert _ev(_t248('CONCATENATEX(GENERATE(GENERATE(D, FILTER(E, E[k] < 3)), FILTER(T, T[k] = 1)), '
                     'D[g] & E[q] & "/" & T[c], ",", D[g], ASC, E[q], ASC)')) == "a1.5/0.1,a2.5/0.1,b1.5/0.1,b2.5/0.1"
