"""#320 (#248, #252): IF, SWITCH, COALESCE and two-argument
MIN / MAX type their result from every branch: an Int64 or a Currency beside
a Double branch is a Double, an Int64 beside a Currency branch a Currency.

What was wrong (0.9.147): the engine evaluates one branch and returned its
value as it was -- IF(TRUE(), 1, 2.5) the Int64 1, IF(FALSE(), CURRENCY(1.5),
2) the Int64 2, COALESCE(CURRENCY(1.5), 2.5) the Currency 1.5, MIN(1, 2.5) the
Int64 1 -- and an Int64 past 2^53 stayed exact where Desktop makes it the
Double 2^53 (IF(MAX(TS[G]) = 2, 9007199254740993, 9007199254740992.0)).

Expected values: Power BI Desktop 2.152's answers over ADOMD -- g() cells
(type letter + FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim)
of build_b254b.py c_if, c_coalesce and build_b254.py f_if, f_minmax; the
numbers and labels of build_b257b.py if_unify, if_unify_col, if_unify_top,
switch_unify, coalesce_unify. h_cur = CURRENCY(1.5), h_dbl = 2.5 (declared
Double), h_true = TRUE() are the battery's helper measures.

Desktop's b248g answer (test_desktop_b248g_p_if_variant, build_b248g.py
p_if_variant verbatim): I1 D1 I1 I1 I1 I1 -- a Variant beside the chosen
Int64 leaves it an Int64; IF(TRUE(), 1, IF(FALSE(), 2.5, 3)) is a Double.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


P = 2 ** 53
MEASURES = {"h_cur": "CURRENCY(1.5)", "h_dbl": "2.5", "h_true": "TRUE()"}
TABLES = {
    "TS": {"columns": ["G", "L"], "rows": [[1, "p"], [1, "q"], [2, "r"]]},
    "TC": {"columns": ["L", "K", "A"], "rows": [["b", P, 1], ["c", P + 1, 2]]},
}


def _ev(expr):
    ctx = de.DAXContext(TABLES, MEASURES, None, None, None, [])
    return de.DAXEngine()._eval_expr(expr, ctx)


CASES = [
    # build_b254b.py c_if
    ("IF(TRUE(), CURRENCY(1.5), 2.5)", "D1.500000000"),
    ("IF(FALSE(), CURRENCY(1.5), 2)", "C2.000000000"),
    ("IF(TRUE(), 1, 2.5)", "D1.000000000"),
    ("IF(TRUE(), 1, CURRENCY(2))", "C1.000000000"),
    ("IF([h_true], CURRENCY(1.5), 2.5)", "D1.500000000"),
    ("IF(TRUE(), [h_cur], [h_dbl])", "D1.500000000"),
    ('IF(TRUE(), CURRENCY(1.5), "x")', "C1.500000000"),
    ("IF(TRUE(), CURRENCY(1.5), BLANK())", "C1.500000000"),
    # build_b254b.py c_coalesce
    ("COALESCE(1, 2.5)", "D1.000000000"),
    ("COALESCE(BLANK(), 1, CURRENCY(2))", "C1.000000000"),
    ("SWITCH(TRUE(), TRUE(), 1, 2.5)", "D1.000000000"),
    ("SWITCH(1, 2, CURRENCY(1.5), 3)", "C3.000000000"),
    ("MIN(1, 2.5)", "D1.000000000"),
    ("MAX(2, 0.5)", "D2.000000000"),
    ("MIN(CURRENCY(2.5), 2)", "C2.000000000"),
    # build_b254.py f_if
    ("IF(TRUE(), CURRENCY(1.5), 2)", "C1.500000000"),
    ("IF(FALSE(), CURRENCY(1.5), 2.5)", "D2.500000000"),
    ("IF(TRUE(), 2.25, CURRENCY(1.5))", "D2.250000000"),
    ("COALESCE(BLANK(), CURRENCY(1.5))", "C1.500000000"),
    ("COALESCE(CURRENCY(1.5), 2.5)", "D1.500000000"),
    ("SWITCH(1, 1, CURRENCY(1.5), 2.5)", "D1.500000000"),
    ("IF(FALSE(), CURRENCY(1.5))", "B"),
    # build_b254.py f_minmax
    ("MIN(CURRENCY(1.5), 2)", "C1.500000000"),
    ("MIN(CURRENCY(2.5), 2)", "C2.000000000"),
    ("MAX(CURRENCY(1.5), 0.25)", "D1.500000000"),
    ("MAX(CURRENCY(0.1), 0.25)", "D0.250000000"),
]


@pytest.mark.parametrize("expr, desktop", CASES, ids=[c[0] for c in CASES])
def test_desktop_cells(expr, desktop):
    assert _ev(g(expr)) == desktop


# build_b248c.py f_if_types (h_dbl = 2.5 declared Double, h_blank = BLANK(),
# h_int = 2; T3[d] a Double column, T3[v] a Fixed Decimal one): a branch
# that is no number beside a number makes the call a Variant, whose chosen
# branch keeps its type -- the inner IF(FALSE(), 2.5, "x") is one, so the
# outer IF's 1 stays I1 (the first implementation typed the inner IF a
# Double and answered D1; 0.9.147 answered I1)
F_MEASURES = {"h_dbl": "2.5", "h_blank": "BLANK()", "h_int": "2"}
F_CASES = [
    ('IF(TRUE(), 1, IF(FALSE(), 2.5, "x"))', "I1.000000000"),
    ("VAR x = [h_dbl] RETURN IF(TRUE(), 1, x)", "D1.000000000"),
    ("IF(TRUE(), 1, [h_blank])", "I1.000000000"),
    ("IF(TRUE(), 1, [h_int])", "I1.000000000"),
    ("SWITCH(TRUE(), FALSE(), 2.5, 1)", "D1.000000000"),
    ("IF(TRUE(), 1, CONVERT(2, DOUBLE))", "D1.000000000"),
    ("IF(TRUE(), 1, SUM(T3[d]))", "D1.000000000"),
    ("IF(TRUE(), 1, SUM(T3[v]))", "C1.000000000"),
]


@pytest.mark.parametrize("expr, desktop", F_CASES, ids=[c[0] for c in F_CASES])
def test_desktop_cells_b248c(expr, desktop):
    tables = {"T3": {"columns": ["id", "v", "d", "k"], "rows": [[i, 0.0003, 0.5, 1] for i in range(1, 11)]}}
    de.mark_currency_columns(tables, {"T3": ["v"]})
    ctx = de.DAXContext(tables, F_MEASURES, None, None, None, [])
    assert de.DAXEngine()._eval_expr(g(expr), ctx) == desktop


B53 = "9007199254740992"
INT_DOUBLE = [
    # build_b257b.py group B: the Int64 2^53 + 1 beside a Double branch is the
    # Double 2^53 (0), not exact (1)
    (f"IF(MAX(TS[G]) = 2, 9007199254740993, 9007199254740992.0) - {B53}", 0),
    (f'MAXX(TC, IF(TC[L] = "c", TC[K], 0.5)) - {B53}', 0),
    (f"SWITCH(MAX(TS[G]), 1, 1.5, 9007199254740993) - {B53}", 0),
    (f"COALESCE(BLANK(), 9007199254740993, 1.5) - {B53}", 0),
    # the two rows tie as Doubles, so TOPN(1, ...) keeps both
    ('CONCATENATEX(TOPN(1, TC, IF(TC[L] = "c", TC[K], TC[K] + 0.0), DESC), TC[L], "", TC[L], ASC)', "bc"),
]


@pytest.mark.parametrize("expr, desktop", INT_DOUBLE, ids=["if", "if_col", "switch", "coalesce", "if_top"])
def test_int64_beside_a_double_is_a_double(expr, desktop):
    assert _ev(expr) == desktop


def test_the_branch_types_are_typed_again_for_other_measures():
    """The result type is memoized per call text on the model's cache, with
    the measures it was typed against: the same IF over the same model and
    another measures dict types its measure branch again. Desktop's c_if
    types a measure branch statically (IF(TRUE(), [h_cur], [h_dbl]) D1.5) and
    gives IF(TRUE(), 1, 2.5) D1, IF(TRUE(), 1, CURRENCY(2)) C1."""
    eng = de.DAXEngine()
    expr = g("IF(TRUE(), 1, [h_m])")
    ctx = de.DAXContext(TABLES, {"h_m": "2.5"}, None, None, None, [])
    assert eng._eval_expr(expr, ctx) == "D1.000000000"
    ctx = de.DAXContext(TABLES, {"h_m": "CURRENCY(2)"}, None, None, None, [])
    assert eng._eval_expr(expr, ctx) == "C1.000000000"


class _Probe:
    """Something a weak reference can watch, held by the model's tables."""


def test_the_memo_does_not_keep_a_closed_model_alive():
    """Review of this fix: the result types were first memoized on the
    engine, with the model's measures and tables dicts. The module's engine
    is one per thread and lives across models, so every IF / SWITCH /
    COALESCE text kept its model's whole tables dict alive after the model
    was closed and its cache had left the registry (0.9.147, which memoized
    nothing there, freed it). The memo now lives on the model's own filter
    cache. Expected values: Desktop's c_if / c_coalesce rule (an Int64 beside
    a Double branch is a Double: IF(TRUE(), 1, 2.5) D1, COALESCE(1, 2.5) D1,
    SWITCH(TRUE(), TRUE(), 1, 2.5) D1)."""
    import gc
    import weakref

    eng = de.DAXEngine()
    tables = {"TK": {"columns": ["k", "w"], "rows": [[1, 2.5], [2, 3.5]], "probe": _Probe()}}
    ctx = de.DAXContext(tables, {"h_w": "SUM(TK[w])"}, None, None, None, [])
    for expr in ("SUMX(TK, IF(TK[k] > 0, TK[k], TK[w]))", "SUMX(TK, COALESCE(TK[k], [h_w]))",
                 "SUMX(TK, SWITCH(TK[k], 1, TK[k], 2, TK[k], 0.5))", "IF(TRUE(), MAX(TK[k]) + 1, 2.5)",
                 "SWITCH(TRUE(), TRUE(), MAX(TK[k]) + 1, 2.5)", "COALESCE(MAX(TK[k]) + 1, 2.5)"):
        got = eng._eval_expr(expr, ctx)
        assert (type(got), got) == (float, 3.0), expr
    watch = weakref.ref(tables["TK"]["probe"])
    # the model is closed: its cache leaves the registry, its contexts go
    with de._CACHE_LOCK:
        for key in [k for k, c in de._SHARED_FILTER_CACHES.items() if getattr(c, "tables", None) is tables]:
            de._SHARED_FILTER_CACHES.pop(key)
    del ctx, tables
    gc.collect()
    assert watch() is None
    assert eng is not None


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


def test_desktop_b248g_p_if_variant():
    # build_b248g.py p_if_variant, Power BI Desktop 2.152 (desk_b248g.json)
    expr = _cells248(_g248('SWITCH(TRUE(), FALSE(), 2.5, FALSE(), "x", 1)'),
                     _g248("IF(TRUE(), 1, IF(FALSE(), 2.5, 3))"),
                     _g248("IF(TRUE(), 1, IF(FALSE(), TRUE(), 2.5))"),
                     _g248('VAR x = "a" RETURN SWITCH(TRUE(), TRUE(), 1, FALSE(), 2.5, x)'),
                     _g248('COALESCE(1, IF(FALSE(), 2.5, "x"))'), _g248('IF(TRUE(), 1, IF(FALSE(), "x", "y"))'))
    assert _ev(expr) == ("I1.000000000 D1.000000000 I1.000000000 I1.000000000 I1.000000000 "
                         "I1.000000000")
