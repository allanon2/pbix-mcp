"""#300: a column of GENERATE's / GENERATEALL's inner rows,
and of NATURALINNERJOIN's / NATURALLEFTOUTERJOIN's right-hand rows, read by
name is that row's own value.

What was wrong (0.9.147, unchanged until 0.9.148): the joined row is the
outer (left) row with the inner row's keys prefixed ``_inner_`` (GENERATE)
or the right row's columns merged in under the left table's name (natural
joins), and Table[Column] of the inner / right table was looked up on the
outer row alone. It came back as a (table, column) marker:
CONCATENATEX(GENERATE(D, FILTER(T, T[g] = D[g])), T[k] & "") printed
"('T', 'k')", SUMX(NATURALINNERJOIN(T, D), D[p] * 0.5) was BLANK, and a bare
[z] of GENERATE(T, ROW("z", ..)) raised "cannot be found". With one row in
the right table, a MAXX happened to find the value through the filters.

Expected values:
- Power BI Desktop 2.152's g() cells over ADOMD (type letter +
  FORMAT(CONVERT(x, DOUBLE), "0.000000000"), builder verbatim),
  build_b248c.py l_reads: T3 = ten rows (id, Fixed Decimal 0.0003, 0.5,
  k = 1), D3 = one row (1, 0.5, Fixed Decimal 0.25), T3[k] -> D3[k].
- Where Desktop has not been asked (build_b248g.py g1_* asks), DAX's
  semantics: the inner / right row's own value, BLANK for a left row no
  right row matched and for GENERATEALL's outer row with no inner row.
  T = (c Fixed Decimal, w, k, g, id): (0.1, 0.1, 1, a, 1), (0.1, 0.15, 2,
  a, 2), (0.2, 0.2, 3, b, 3), (1.2345, 2.5, 4, b, 4); D = (g, p Fixed
  Decimal): (a, 0.1), (b, 0.2); E = (k, q Fixed Decimal): (1, 1.5) ..
  (4, 4.5); T[g] -> D[g], T[k] -> E[k]. Sums are exact count arithmetic.

Review round (EDGES): a join of a join keeps the parts the left (and the
right) row joined already, so D[p] of NATURALINNERJOIN(NATURALINNERJOIN(T,
D), E) reads D's row, where the second join's part overwrote the first's;
and the BLANK inner / right row comes from the inner / right table's
layout when no row of it joined at all (an empty FILTER), where T[k] still
leaked the marker and a bare [x] of a SELECTCOLUMNS inner table raised.

Desktop's b248g answers (test_desktop_b248g, the g1_* probes of
build_b248g.py verbatim): every DAX-semantics value above held except one.
A NATURALLEFTOUTERJOIN left row no right row matched reads a right-hand
table that the left row reaches through an active relationship as the
related row, not BLANK: g1_nloj_read 1/0.1,2/0.1,3/0.2,4/0.2,
g1_empty_nloj the same with FILTER(D, FALSE()), g1_nij_sum cell 2 C0.2
(the engine of round 3 answered 3/,4/ and C0.1). The cases that rule
decides without a probe of their own (a join of a join, T[k] * D[p]) follow
it; a bare [p] of such a row stays BLANK (not measured).
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


def cells(*xs):
    return ' & " " & '.join(xs)


def t(x):
    return f'IFERROR(({x}) & "", "E")'


def _ctx():
    tables = {
        "T3": {"columns": ["id", "v", "d", "k"], "rows": [[i, 0.0003, 0.5, 1] for i in range(1, 11)]},
        "D3": {"columns": ["k", "p", "pc"], "rows": [[1, 0.5, 0.25]]},
        "T": {"columns": ["c", "w", "k", "g", "id"],
              "rows": [[0.1, 0.1, 1, "a", 1], [0.1, 0.15, 2, "a", 2], [0.2, 0.2, 3, "b", 3],
                       [1.2345, 2.5, 4, "b", 4]]},
        "D": {"columns": ["g", "p"], "rows": [["a", 0.1], ["b", 0.2]]},
        "E": {"columns": ["k", "q"], "rows": [[1, 1.5], [2, 2.5], [3, 3.5], [4, 4.5]]},
    }
    de.mark_currency_columns(tables, {"T3": ["v"], "D3": ["pc"], "T": ["c"], "D": ["p"], "E": ["q"]})
    rels = [{"FromTable": "T3", "FromColumn": "k", "ToTable": "D3", "ToColumn": "k", "IsActive": 1},
            {"FromTable": "T", "FromColumn": "g", "ToTable": "D", "ToColumn": "g", "IsActive": 1},
            {"FromTable": "T", "FromColumn": "k", "ToTable": "E", "ToColumn": "k", "IsActive": 1}]
    return de.DAXContext(tables, {}, None, None, None, rels)


def _ev(expr):
    return de.DAXEngine()._eval_expr(expr, _ctx())


def test_desktop_l_reads():
    # build_b248c.py l_reads (MAXA / MINA of the Fixed Decimal column first)
    expr = cells(g("MAXA(T3[v])"), g("MINA(T3[v])"), g("MAXX(NATURALINNERJOIN(T3, D3), D3[pc])"),
                 g("MAXX(NATURALLEFTOUTERJOIN(T3, D3), D3[pc])"),
                 g("MAXX(GENERATE(VALUES(T3[k]), FILTER(D3, D3[k] = EARLIER(T3[k]))), D3[pc])"),
                 g("SUMX(NATURALINNERJOIN(T3, D3), D3[pc])"))
    assert _ev(expr) == ("C0.000300000 C0.000300000 C0.250000000 C0.250000000 C0.250000000 "
                         "C2.500000000")


SEMANTICS = [
    (t('CONCATENATEX(NATURALINNERJOIN(T, D), T[k] & "/" & D[p], ",", T[k], ASC)'), "1/0.1,2/0.1,3/0.2,4/0.2"),
    (t('CONCATENATEX(NATURALLEFTOUTERJOIN(T, FILTER(D, D[g] = "a")), T[k] & "/" & D[p], ",", T[k], ASC)'),
     "1/0.1,2/0.1,3/0.2,4/0.2"),
    (t('CONCATENATEX(NATURALINNERJOIN(T, D), IF(ISCURRENCY(D[p]), "C", "D") & IF(ISCURRENCY([p]), "C", "D"), '
       '",", T[k], ASC)'), "CC,CC,CC,CC"),
    (cells(g("SUMX(NATURALINNERJOIN(T, D), D[p] * 0.5)"), g("SUMX(NATURALINNERJOIN(T, D), D[p])"),
           g('MAXX(NATURALLEFTOUTERJOIN(T, FILTER(D, D[g] = "a")), D[p])')),
     "C0.300000000 C0.600000000 C0.200000000"),
    (t('CONCATENATEX(GENERATE(D, FILTER(T, T[g] = D[g])), D[g] & T[k] & "/" & T[c], ",", T[k], ASC)'),
     "a1/0.1,a2/0.1,b3/0.2,b4/1.2345"),
    (cells(g("SUMX(GENERATE(D, FILTER(T, T[g] = D[g])), T[c])"),
           g("SUMX(GENERATE(D, FILTER(T, T[g] = D[g])), T[k] * D[p])")),
     "C1.634500000 C1.700000000"),
    (t('CONCATENATEX(GENERATE(D, FILTER(T, T[g] = D[g])), IF(ISCURRENCY(T[c]), "C", "D") & [k], ",", T[k], ASC)'),
     "C1,C2,C3,C4"),
    (t('CONCATENATEX(GENERATE(T, ROW("z", T[k] * 10)), [z], ",", [z], ASC)'), "10,20,30,40"),
    (t('CONCATENATEX(GENERATEALL(D, FILTER(T, T[g] = D[g] && T[k] > 2)), D[g] & "/" & T[k], ",", D[g], ASC, '
       'T[k], ASC)'), "a/,b/3,b/4"),
]


@pytest.mark.parametrize("expr, expected", SEMANTICS, ids=[f"s{i}" for i in range(len(SEMANTICS))])
def test_the_joined_rows_own_value(expr, expected):
    assert _ev(expr) == expected


EDGES = [
    # a join of a join: every part it joined reads its own row, typed
    (t('CONCATENATEX(NATURALINNERJOIN(NATURALINNERJOIN(T, D), E), T[k] & "/" & D[p] & "/" & E[q], ",", '
       'T[k], ASC)'), "1/0.1/1.5,2/0.1/2.5,3/0.2/3.5,4/0.2/4.5"),
    (t('CONCATENATEX(NATURALINNERJOIN(NATURALINNERJOIN(T, D), E), IF(ISCURRENCY(D[p]), "C", "X") & '
       'IF(ISCURRENCY([p]), "C", "X") & IF(ISCURRENCY(E[q]), "C", "X"), ",")'), "CCC,CCC,CCC,CCC"),
    (t('CONCATENATEX(NATURALLEFTOUTERJOIN(NATURALINNERJOIN(T, D), FILTER(E, E[k] < 3)), T[k] & "/" & D[p] & '
       '"/" & E[q], ",", T[k], ASC)'), "1/0.1/1.5,2/0.1/2.5,3/0.2/3.5,4/0.2/4.5"),
    # no inner / right row joined anywhere
    (t('CONCATENATEX(GENERATEALL(D, FILTER(T, FALSE())), D[g] & "/" & T[k] & "/" & [k], ",", D[g], ASC)'),
     "a//,b//"),
    (t('CONCATENATEX(NATURALLEFTOUTERJOIN(T, FILTER(D, FALSE())), T[k] & "/" & D[p] & "/" & [p], ",", '
       'T[k], ASC)'), "1/0.1/,2/0.1/,3/0.2/,4/0.2/"),
    (t('CONCATENATEX(GENERATEALL(D, SELECTCOLUMNS(FILTER(T, FALSE()), "x", T[k])), D[g] & "/" & [x], ",", '
       'D[g], ASC)'), "a/,b/"),
    # a bare column of the BLANK inner / right row
    (t('CONCATENATEX(GENERATEALL(D, SELECTCOLUMNS(FILTER(T, T[g] = D[g] && T[k] > 2), "x", T[k])), '
       'D[g] & "/" & [x], ",", D[g], ASC, [x], ASC)'), "a/,b/3,b/4"),
    (t('CONCATENATEX(NATURALLEFTOUTERJOIN(T, FILTER(D, D[g] = "a")), T[k] & "/" & [p], ",", T[k], ASC)'),
     "1/0.1,2/0.1,3/,4/"),
    # exact count arithmetic: 1 * 0.1 + 2 * 0.1 + 3 * 0.2 + 4 * 0.2, the b
    # rows reading D's related row
    (g('SUMX(NATURALLEFTOUTERJOIN(T, FILTER(D, D[g] = "a")), T[k] * D[p])'), "C1.700000000"),
]


@pytest.mark.parametrize("expr, expected", EDGES, ids=[f"e{i}" for i in range(len(EDGES))])
def test_joins_of_joins_and_no_joined_row(expr, expected):
    assert _ev(expr) == expected


def test_generateall_blank_inner_row_has_the_inner_columns():
    # DAX's GENERATEALL row is the outer columns, then every inner column,
    # BLANK where no inner row joined: positional EXCEPT keeps the one row
    # GENERATE lacks (D's a row), and the cells are D's two then T's five
    rows = de.DAXEngine()._fn_generateall('D, FILTER(T, T[g] = D[g] && T[k] > 2)', _ctx())
    assert [de.DAXEngine._row_cells(r) for r in rows] == [
        ["a", 0.1, None, None, None, None, None],
        ["b", 0.2, 0.2, 0.2, 3, "b", 3],
        ["b", 0.2, 1.2345, 2.5, 4, "b", 4],
    ]
    assert _ev('COUNTROWS(EXCEPT(GENERATEALL(D, FILTER(T, T[g] = D[g] && T[k] > 2)), '
               'GENERATE(D, FILTER(T, T[g] = D[g] && T[k] > 2))))') == 1


def tc(x):
    return f'IF(ISCURRENCY({x}), "C", IF(ISDOUBLE({x}), "D", IF(ISBLANK({x}), "B", "?")))'


# Power BI Desktop 2.152, build_b248g.py (desk_b248g.json): the g1_* probes
# verbatim, with Desktop's answers
DESKTOP_B248G = [
    ("g1_nij_read", t('CONCATENATEX(NATURALINNERJOIN(T, D), T[k] & "/" & D[p], ",", T[k], ASC)'),
     "1/0.1,2/0.1,3/0.2,4/0.2"),
    ("g1_nloj_read", t('CONCATENATEX(NATURALLEFTOUTERJOIN(T, FILTER(D, D[g] = "a")), T[k] & "/" & D[p], ",", '
                       'T[k], ASC)'), "1/0.1,2/0.1,3/0.2,4/0.2"),
    ("g1_nij_types", t(f'CONCATENATEX(NATURALINNERJOIN(T, D), {tc("D[p]")} & {tc("T[c]")}, ",", T[k], ASC)'),
     "CC,CC,CC,CC"),
    ("g1_nij_sum", cells(g("SUMX(NATURALINNERJOIN(T, D), D[p] * 0.5)"), g("SUMX(NATURALINNERJOIN(T, D), D[p])"),
                         g("MAXX(NATURALLEFTOUTERJOIN(T, FILTER(D, D[g] = \"a\")), D[p])")),
     "C0.300000000 C0.600000000 C0.200000000"),
    ("g1_gen_read", t('CONCATENATEX(GENERATE(D, FILTER(T, T[g] = D[g])), D[g] & T[k] & "/" & T[c], ",", T[k], ASC)'),
     "a1/0.1,a2/0.1,b3/0.2,b4/1.2345"),
    ("g1_gen_sum", cells(g("SUMX(GENERATE(D, FILTER(T, T[g] = D[g])), T[c])"),
                         g("SUMX(GENERATE(D, FILTER(T, T[g] = D[g])), T[k] * D[p])"),
                         g("MAXX(GENERATE(VALUES(T[g]), FILTER(D, D[g] = EARLIER(T[g]))), D[p])")),
     "C1.634500000 C1.700000000 C0.200000000"),
    ("g1_gen_types", t(f'CONCATENATEX(GENERATE(D, FILTER(T, T[g] = D[g])), {tc("T[c]")}, ",", T[k], ASC)'),
     "C,C,C,C"),
    ("g1_gen_bare", t('CONCATENATEX(GENERATE(T, ROW("z", T[k] * 10)), [z], ",", [z], ASC)'), "10,20,30,40"),
    ("g1_genall", t('CONCATENATEX(GENERATEALL(D, FILTER(T, T[g] = D[g] && T[k] > 2)), D[g] & "/" & T[k], ",", '
                    'D[g], ASC, T[k], ASC)'), "a/,b/3,b/4"),
    ("g1_chain", t('CONCATENATEX(NATURALINNERJOIN(NATURALINNERJOIN(T, D), E), T[k] & "/" & D[p] & "/" & E[q], '
                   '",", T[k], ASC)'), "1/0.1/1.5,2/0.1/2.5,3/0.2/3.5,4/0.2/4.5"),
    ("g1_chain_types", t(f'CONCATENATEX(NATURALINNERJOIN(NATURALINNERJOIN(T, D), E), {tc("D[p]")} & {tc("[p]")} & '
                         f'{tc("E[q]")}, ",", T[k], ASC)'), "CCC,CCC,CCC,CCC"),
    ("g1_empty_genall", t('CONCATENATEX(GENERATEALL(D, FILTER(T, FALSE())), D[g] & "/" & T[k], ",", D[g], ASC)'),
     "a/,b/"),
    ("g1_empty_nloj", t('CONCATENATEX(NATURALLEFTOUTERJOIN(T, FILTER(D, FALSE())), T[k] & "/" & D[p], ",", '
                        'T[k], ASC)'), "1/0.1,2/0.1,3/0.2,4/0.2"),
    ("g1_empty_sel", t('CONCATENATEX(GENERATEALL(D, SELECTCOLUMNS(FILTER(T, T[g] = D[g] && T[k] > 2), "x", T[k])), '
                       'D[g] & "/" & [x], ",", D[g], ASC, [x], ASC)'), "a/,b/3,b/4"),
]


@pytest.mark.parametrize("expr, expected", [c[1:] for c in DESKTOP_B248G], ids=[c[0] for c in DESKTOP_B248G])
def test_desktop_b248g(expr, expected):
    assert _ev(expr) == expected
