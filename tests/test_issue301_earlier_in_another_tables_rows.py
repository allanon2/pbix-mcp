"""#301: EARLIER(T[c]) where the innermost row context is
another table's reaches the one row context over T.

What was wrong (0.9.147): EARLIER skipped the innermost row context that
holds the column and asked for one more, so with a single row context over
T3 -- GENERATE's VALUES(T3[k]) row around FILTER(D3, ...) -- it raised
"EARLIER/EARLIEST refers to an earlier row context which doesn't exist".

Expected values: Power BI Desktop 2.152's g() cell over ADOMD (type letter +
FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim): build_b248c.py
l_reads cell 4 (T3 = ten rows with k = 1, D3 = one row k = 1, Fixed Decimal
pc = 0.25), and build_b248g.py g2_* (test_desktop_b248g): g2_earlier_sumx
4, g2_earlier_levels 7, g2_earliest 20, g2_values 20.

g2_earlier_levels settles the rule: the innermost row context counts as
the first whatever its table, so with two row contexts over T around D's
FILTER, EARLIER(T[c]) is the inner one (the SUMX over FILTER(ALL(T), ..)):
7. Round 3 kept the outer one there (3), from the per-table model.
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


def _ev(expr):
    tables = {
        "T3": {"columns": ["id", "v", "d", "k"], "rows": [[i, 0.0003, 0.5, 1] for i in range(1, 11)]},
        "D3": {"columns": ["k", "p", "pc"], "rows": [[1, 0.5, 0.25]]},
        "T": {"columns": ["c", "k", "g"], "rows": [[0.1, 1, "a"], [0.1, 2, "a"], [0.2, 3, "b"], [1.2345, 4, "b"]]},
        "D": {"columns": ["g", "p"], "rows": [["a", 0.1], ["b", 0.2]]},
    }
    de.mark_currency_columns(tables, {"T3": ["v"], "D3": ["pc"], "T": ["c"], "D": ["p"]})
    rels = [{"FromTable": "T3", "FromColumn": "k", "ToTable": "D3", "ToColumn": "k", "IsActive": 1},
            {"FromTable": "T", "FromColumn": "g", "ToTable": "D", "ToColumn": "g", "IsActive": 1}]
    return de.DAXEngine()._eval_expr(expr, de.DAXContext(tables, {}, None, None, None, rels))


def test_desktop_generate_inner_filter():
    # build_b248c.py l_reads cell 4
    assert _ev(g("MAXX(GENERATE(VALUES(T3[k]), FILTER(D3, D3[k] = EARLIER(T3[k]))), D3[pc])")) == "C0.250000000"


def test_sumx_over_another_tables_filter():
    assert _ev(g("SUMX(T, COUNTROWS(FILTER(D, D[g] = EARLIER(T[g]))))")) == "I4.000000000"


def test_earlier_of_the_same_table_is_unchanged():
    # build_b116.py (issue #116): the outer row of the same table
    assert _ev("SUMX(T, COUNTROWS(FILTER(T, T[k] < EARLIER(T[k]))))") == 6


def test_no_row_context_over_the_table_is_still_an_error():
    with pytest.raises(DAXEvaluationError):
        _ev("COUNTROWS(FILTER(D, D[g] = EARLIER(T[g])))")


# Power BI Desktop 2.152, build_b248g.py (desk_b248g.json): the g2_* probes
# verbatim, with Desktop's answers
DESKTOP_B248G = [
    ("g2_earlier_sumx", g("SUMX(T, COUNTROWS(FILTER(D, D[g] = EARLIER(T[g]))))"), "I4.000000000"),
    ("g2_earlier_levels", g("SUMX(T, SUMX(FILTER(ALL(T), T[k] <= EARLIER(T[k])), "
                            "COUNTROWS(FILTER(D, D[p] > EARLIER(T[c])))))"), "I7.000000000"),
    ("g2_earliest", g("SUMX(T, SUMX(D, EARLIEST(T[k])))"), "I20.000000000"),
    ("g2_values", g("SUMX(T, SUMX(VALUES(T[g]), EARLIER(T[k])))"), "I20.000000000"),
]


@pytest.mark.parametrize("expr, expected", [c[1:] for c in DESKTOP_B248G], ids=[c[0] for c in DESKTOP_B248G])
def test_desktop_b248g(expr, expected):
    assert _ev(expr) == expected


def test_earlier_with_n_counts_the_other_tables_row_first():
    # the same rule for EARLIER(.., 2) (not measured): the second row
    # context over T outward from D's, the outer SUMX's row -- each outer row
    # t1 counts its k <= t1 rows t2 against D[p] > t1[c]: 1 + 2 + 0 + 0 = 3
    assert _ev("SUMX(T, SUMX(FILTER(ALL(T), T[k] <= EARLIER(T[k])), "
               "COUNTROWS(FILTER(D, D[p] > EARLIER(T[c], 2)))))") == 3
