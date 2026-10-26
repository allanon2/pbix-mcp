"""#304: a VAR named as a model table is the variable in
the check a measure's expression passes before it is evaluated.

What was wrong (0.9.147 .. 0.9.148 round 3): the check of #287
(DAXEngine._static_refusal) read the table argument of an iterator by its
name alone. In a model with a table T, ``VAR t = FILTER(T3, T3[id] > 0)
RETURN SUMX(t, T3[v] * 0.5)`` read SUMX(t, ..) as rows of T (names are
case-insensitive), found T3[v] in no row context and refused the measure
with "A single value for column 'v' in table 'T3' cannot be determined.
...". The evaluation itself took the variable, so _eval_expr answered and a
measure did not.

Expected values: Power BI Desktop 2.152 over ADOMD, build_b248g.py
p_se_tables cell 5 (desk_b248g.json): C0.001 -- T3 is ten rows of a Fixed
Decimal 0.0003, and the storage engine's 0.0003 * 0.5 is 1 count per row
(#318); the model has a table T. The guard is the rule of
#287 (build_b256c.py b_one_row's message) where no VAR is
involved: SUMX(T, T3[v]) still reads T3[v] with no row context over T3.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

MSG = ("A single value for column 'v' in table 'T3' cannot be determined. This can happen when a "
       "measure formula refers to a column that contains many values without specifying an aggregation "
       "such as min, max, count, or sum to get a single result.")


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


MEASURES = {
    # build_b248g.py p_se_tables cell 5, verbatim
    "p_se_tables_5": g("VAR t = FILTER(T3, T3[id] > 0) RETURN SUMX(t, T3[v] * 0.5)"),
    "raw": "VAR t = FILTER(T3, T3[id] > 0) RETURN SUMX(t, T3[v] * 0.5)",
    "upper": "VAR T = T3 RETURN SUMX(T, T3[v])",
    "no_var": "SUMX(T, T3[v])",
    # T3[v] names the model column whatever the VARs are called (DAX has no
    # var[column]), so the check still refuses it (#287)
    "var_col": "VAR t3 = 1 RETURN T3[v]",
}


def _run(name):
    tables = {
        "T3": {"columns": ["id", "v", "d", "k"], "rows": [[i, 0.0003, 0.5, 1] for i in range(1, 11)]},
        "T": {"columns": ["c", "k", "g"], "rows": [[0.1, 1, "a"], [0.1, 2, "a"], [0.2, 3, "b"], [1.2345, 4, "b"]]},
    }
    de.mark_currency_columns(tables, {"T3": ["v"], "T": ["c"]})
    eng = de.DAXEngine()
    ctx = de.DAXContext(tables, dict(MEASURES), None, None, {}, [])
    return eng.evaluate_measure(name, ctx), eng.eval_errors.get(name)


def test_desktop_p_se_tables_cell_5():
    assert _run("p_se_tables_5") == ("C0.001000000", None)


def test_the_variable_is_iterated():
    v, err = _run("raw")
    assert err is None
    assert isinstance(v, de._Currency) and float(v) == 0.001


def test_a_var_spelled_as_the_table():
    # ten rows of 0.0003, added exactly (issue #241)
    v, err = _run("upper")
    assert err is None
    assert isinstance(v, de._Currency) and float(v) == 0.003


def test_without_a_var_the_check_still_refuses():
    v, err = _run("no_var")
    assert v is None
    assert err is not None and err.endswith(MSG), err


def test_a_var_named_as_the_columns_table_does_not_exempt_the_column():
    # review of round 4: exempting T3[v] here let the measure return the
    # engine's internal ('T3', 'v') marker
    v, err = _run("var_col")
    assert v is None
    assert err is not None and err.endswith(MSG), err
