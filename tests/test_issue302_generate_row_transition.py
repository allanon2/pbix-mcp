"""#302: the context transition of a GENERATE /
GENERATEALL row filters the inner row's columns too.

What was wrong (0.9.147 and before): the transition filtered the outer
row's table alone, with the inner row's keys (prefixed ``_inner_``) as
columns of that table that match nothing. So CALCULATE inside such a row
saw the inner table filtered only through the outer row:
SUMX(GENERATE(D, FILTER(T, T[g] = D[g])), CALCULATE(SUM(T[c]))) added each
group's total once per row of it.

Expected values: Power BI Desktop 2.152, build_b248g.py g3_trans (below); DAX's
semantics, which are unambiguous here: a GENERATE row holds the outer and
the inner table's columns, and its transition filters every one of them to
the row's value -- a BLANK inner column (GENERATEALL's outer row with no
inner row) to BLANK. Model as the G1 tests: T = (c Fixed Decimal, w, k, g,
id) four rows, c = 0.1, 0.1, 0.2, 1.2345 in groups a, a, b, b; D = (g, p)
a, b; E = (k, q) k = 1..4; T[g] -> D[g], T[k] -> E[k]. Sums are exact count
arithmetic, counts are counts.

Desktop's b248g answer (test_desktop_b248g_g3_trans, build_b248g.py g3_trans
verbatim): C1.6345 I4 I8 I2 B I4 -- every value above.
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
    measures = {"mT": "COUNTROWS(T)"}
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(tables, measures, None, None, None, rels))


def test_calculate_sees_the_inner_row():
    # each row filters its own T row: 0.1 + 0.1 + 0.2 + 1.2345 (3.269 before)
    assert _ev('SUMX(GENERATE(D, FILTER(T, T[g] = D[g])), CALCULATE(SUM(T[c]))) & ""') == "1.6345"


def test_a_measure_sees_the_inner_row():
    # four rows, one T row each (8 before: each group's two rows twice)
    assert _ev("SUMX(GENERATE(D, FILTER(T, T[g] = D[g])), [mT])") == 4


def test_a_row_of_one_column():
    # D's two rows times E's four k: each row filters E[k] to one value (32 before)
    assert _ev("SUMX(GENERATE(D, VALUES(E[k])), CALCULATE(COUNTROWS(E)))") == 8


def test_generateall_blank_inner_row_filters_blank():
    # D's a row has no inner row: T filtered to BLANK columns has no row;
    # the two b rows have one each
    assert _ev("SUMX(GENERATEALL(D, FILTER(T, T[g] = D[g] && T[k] > 2)), CALCULATE(COUNTROWS(T)))") == 2
    assert _ev("SUMX(GENERATEALL(D, FILTER(T, FALSE())), CALCULATE(COUNTROWS(T)))") is None


def test_outer_row_alone_unchanged():
    # a ROW inner row filters no table: the outer D row alone, as before
    assert _ev('SUMX(GENERATE(D, ROW("z", 1)), CALCULATE(COUNTROWS(T)))') == 4


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


def test_desktop_b248g_g3_trans():
    # build_b248g.py g3_trans, Power BI Desktop 2.152 (desk_b248g.json)
    expr = _cells248(_g248("SUMX(GENERATE(D, FILTER(T, T[g] = D[g])), CALCULATE(SUM(T[c])))"),
                     _g248("SUMX(GENERATE(D, FILTER(T, T[g] = D[g])), CALCULATE(COUNTROWS(T)))"),
                     _g248("SUMX(GENERATE(D, VALUES(E[k])), CALCULATE(COUNTROWS(E)))"),
                     _g248("SUMX(GENERATEALL(D, FILTER(T, T[g] = D[g] && T[k] > 2)), CALCULATE(COUNTROWS(T)))"),
                     _g248("SUMX(GENERATEALL(D, FILTER(T, FALSE())), CALCULATE(COUNTROWS(T)))"),
                     _g248('SUMX(GENERATE(D, ROW("z", 1)), CALCULATE(COUNTROWS(T)))'))
    assert _ev(expr) == "C1.634500000 I4.000000000 I8.000000000 I2.000000000 B I4.000000000"
