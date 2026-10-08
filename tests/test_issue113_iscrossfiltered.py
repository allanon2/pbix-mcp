"""Issue #113: ISCROSSFILTERED(<table or column>) is TRUE when anything
filters the table.

_fn_iscrossfiltered checked only (Entity, Property) pairs: the table form was
never TRUE (a bare table name evaluates to its rows, as in #111), and the
column form missed a filter on another column of its own table and a filter on
column combinations. Expected values: Power BI Desktop 2.152 over ADOMD -- a
276-cell battery of ISFILTERED and ISCROSSFILTERED, table and column form, on
both tables, under 15 filter shapes, a bidirectional pair and 2 groupings;
plus the row-context and nested-modifier edges.

Desktop's column form answers exactly as the table form. Left out until they
are fixed: ISFILTERED on the dimension under a fact-table FILTER (#114, a table
filter filters its expanded table) and under a context transition (#115).
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


def _tables():
    dim = [["N", "z1"], ["S", "z1"], ["W", "z2"]]
    fact = [["N", 50.0], ["S", 150.0], ["W", 250.0]]
    out = {}
    for d, o in (("Dim", "Orders"), ("Dim2", "Orders2")):
        out[d] = {"columns": ["Region", "Zone"], "rows": [list(r) for r in dim]}
        out[o] = {"columns": ["Region", "Revenue"], "rows": [list(r) for r in fact]}
    return out


TABLES = _tables()
RELS = [{"FromTable": o, "FromColumn": "Region", "ToTable": d, "ToColumn": "Region", "IsActive": True,
         "CrossFilteringBehavior": xf, "FromCardinality": 2, "ToCardinality": 1}
        for o, d, xf in (("Orders", "Dim", 1), ("Orders2", "Dim2", 2))]


def _funcs(o, d):
    return [f"ISFILTERED({o})", f"ISFILTERED({o}[Revenue])", f"ISFILTERED({o}[Region])",
            f"ISFILTERED({d})", f"ISFILTERED({d}[Zone])", f"ISFILTERED({d}[Region])",
            f"ISCROSSFILTERED({o})", f"ISCROSSFILTERED({o}[Revenue])", f"ISCROSSFILTERED({o}[Region])",
            f"ISCROSSFILTERED({d})", f"ISCROSSFILTERED({d}[Zone])", f"ISCROSSFILTERED({d}[Region])"]


# (tables, wrap, Desktop's answers in _funcs order: T = TRUE, . = FALSE, - = left out)
DESKTOP = [
    (("Orders", "Dim"), "{f}", "............"),
    (("Orders", "Dim"), "CALCULATE({f}, Orders[Revenue] > 100)", "TT....TTT..."),
    (("Orders", "Dim"), 'CALCULATE({f}, Orders[Region] = "N")', "T.T...TTT..."),
    (("Orders", "Dim"), 'CALCULATE({f}, Dim[Zone] = "z1")', "...TT.TTTTTT"),
    (("Orders", "Dim"), 'CALCULATE({f}, Dim[Region] = "N")', "...T.TTTTTTT"),
    (("Orders", "Dim"), "CALCULATE({f}, FILTER(Orders, Orders[Revenue] > 100))", "TTT---TTTTTT"),
    (("Orders", "Dim"), 'CALCULATE({f}, FILTER(Dim, Dim[Zone] = "z1"))', "...TTTTTTTTT"),
    (("Orders", "Dim"), 'CALCULATE({f}, TREATAS({{"N"}}, Orders[Region]))', "T.T...TTT..."),
    (("Orders", "Dim"), 'CALCULATE({f}, TREATAS({{("N", "z1")}}, Dim[Region], Dim[Zone]))', "...TTTTTTTTT"),
    (("Orders", "Dim"), "CALCULATE({f}, ALL(Orders))", "............"),
    (("Orders", "Dim"), "CALCULATE({f}, VALUES(Orders[Region]))", "T.T...TTT..."),
    (("Orders", "Dim"), 'CALCULATE({f}, FILTER(ALL(Orders[Region]), Orders[Region] = "N"))', "T.T...TTT..."),
    (("Orders", "Dim"), "CALCULATE(CALCULATE({f}, REMOVEFILTERS(Orders)), Orders[Revenue] > 100)",
     "............"),
    (("Orders", "Dim"), 'CALCULATE(CALCULATE({f}, ALL(Dim)), Dim[Zone] = "z1")', "............"),
    (("Orders", "Dim"), 'CALCULATE(CALCULATE({f}, ALL(Orders[Revenue])), Orders[Region] = "N")',
     "T.T...TTT..."),
    (("Orders2", "Dim2"), "CALCULATE({f}, Orders2[Revenue] > 100)", "TT....TTTTTT"),    # both ways
    (("Orders2", "Dim2"), 'CALCULATE({f}, Dim2[Zone] = "z1")', "...TT.TTTTTT"),
    (("Orders2", "Dim2"), "CALCULATE({f}, FILTER(Orders2, Orders2[Revenue] > 100))", "TTT---TTTTTT"),
]
CELLS = [(wrap.format(f=f), flag == "T")
         for tabs, wrap, flags in DESKTOP
         for f, flag in zip(_funcs(*tabs), flags) if flag != "-"]


@pytest.mark.parametrize("expr,want", CELLS, ids=[c[0] for c in CELLS])
def test_matches_desktop(expr, want):
    got = de.evaluate_measures_smart(["m"], TABLES, {"m": expr}, {}, relationships=RELS,
                                     simulate_row_context=False)["m"]
    assert bool(got) is want


@pytest.mark.parametrize("key,val,flags", [
    ("Dim.Zone", "z1", "...TT.TTTTTT"), ("Dim.Zone", "z2", "...TT.TTTTTT"),
    ("Orders.Region", "N", "T.T...TTT..."), ("Orders.Region", "S", "T.T...TTT..."),
    ("Orders.Region", "W", "T.T...TTT..."),
])
def test_under_a_grouping(key, val, flags):
    """Desktop's SUMMARIZECOLUMNS(<key>, ...): the answers on every row."""
    fs = _funcs("Orders", "Dim")
    names = [f"m{i}" for i in range(len(fs))]
    got = de.evaluate_measures_smart(names, TABLES, dict(zip(names, fs)), {key: [val]}, relationships=RELS,
                                     simulate_row_context=False, group_by={key}, selected_filters={})
    assert "".join("T" if got[n] else "." for n in names) == flags


EDGES = [   # Desktop 2.152; row context, nested modifiers, table expressions as filters
    ("CALCULATE(SUMX(Orders, IF(ISFILTERED(Orders[Revenue]), 1, 0)), Orders[Revenue] > 100)", 2),
    ("SUMX(Orders, IF(ISFILTERED(Orders[Revenue]), 1, 0))", 0),    # a row context filters nothing
    ("SUMX(Orders, CALCULATE(IF(ISFILTERED(Orders[Revenue]), 1, 0)))", 3),
    ("CALCULATE(CALCULATE(ISFILTERED(Dim), ALL(Dim)), FILTER(Orders, Orders[Revenue] > 100))", False),
    ("CALCULATE(CALCULATE(ISCROSSFILTERED(Dim), ALL(Dim)), FILTER(Orders, Orders[Revenue] > 100))", False),
    ("CALCULATE(CALCULATE(ISFILTERED(Orders), ALL(Dim)), FILTER(Orders, Orders[Revenue] > 100))", True),
    ("CALCULATE(CALCULATE(ISFILTERED(Orders), REMOVEFILTERS(Dim)), FILTER(Orders, Orders[Revenue] > 100))",
     True),
    ("CALCULATE(ISFILTERED(Dim), SUMMARIZE(Orders, Orders[Region]))", False),
    ("CALCULATE(ISFILTERED(Dim[Region]), SUMMARIZE(Orders, Dim[Zone]))", False),
    ("CALCULATE(ISFILTERED(Orders), SUMMARIZE(Orders, Dim[Zone]))", False),
    ("CALCULATE(ISCROSSFILTERED(Dim), VALUES(Orders[Region]))", False),
    ('CALCULATE(CALCULATE(ISFILTERED(Orders), ALLEXCEPT(Orders, Orders[Region])), Orders[Region] = "N")',
     True),
    ('CALCULATE(CALCULATE(ISFILTERED(Dim), ALLEXCEPT(Orders, Orders[Region])), Dim[Zone] = "z1")', False),
    ('CALCULATE(CALCULATE(ISCROSSFILTERED(Orders), ALLEXCEPT(Orders, Dim[Zone])), Dim[Zone] = "z1")', True),
    ("CALCULATE(CALCULATE(ISFILTERED(Dim[Zone]), REMOVEFILTERS(Dim[Zone])), "
     "FILTER(Orders, Orders[Revenue] > 100))", False),
    ("CALCULATE(CALCULATE(ISFILTERED(Orders[Revenue]), REMOVEFILTERS(Orders[Revenue])), "
     "FILTER(Orders, Orders[Revenue] > 100))", False),
    ("CALCULATE(CALCULATE(ISFILTERED(Orders[Region]), REMOVEFILTERS(Orders[Revenue])), "
     "FILTER(Orders, Orders[Revenue] > 100))", True),
    ('CALCULATE(ISFILTERED(Dim), FILTER(Dim, Dim[Zone] = "z1"), FILTER(Orders, Orders[Revenue] > 100))', True),
    ('CALCULATE(ISCROSSFILTERED(Orders), ALL(Dim), Dim[Zone] = "z1")', True),
    ('CALCULATE(CALCULATE(ISCROSSFILTERED(Orders), REMOVEFILTERS(Orders)), Dim[Zone] = "z1")', False),
    ("CALCULATE(ISFILTERED(Orders), ALLSELECTED(Orders))", False),
]


@pytest.mark.parametrize("expr,want", EDGES, ids=[e[0] for e in EDGES])
def test_edges_match_desktop(expr, want):
    got = de.evaluate_measures_smart(["m"], TABLES, {"m": expr}, {}, relationships=RELS,
                                     simulate_row_context=False)["m"]
    if isinstance(want, bool):
        assert bool(got) is want
    else:
        assert got == want
