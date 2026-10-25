"""Issue #236 (follow-up): the current row of a window function in a measure.

Since a measure's body no longer reads its caller's row (#236), OFFSET /
INDEX / WINDOW / ROWNUMBER / RANK in a measure called per row found no row
context and took the visual-axis path (#25 r23#1). That path replaced the
relation with rows of the ORDERBY / PARTITIONBY columns alone, so CALCULATE
no longer overrode the caller's filters on the relation's other columns:
with offd = CALCULATE(SUM(Fact[Value]), OFFSET(-1, ALL(Dim),
ORDERBY(Dim[Region], ASC))), CONCATENATEX(Dim, [offd], ...) went from
0.9.146's ',15,20' to ',,' and SUMX(Dim, [wind]) from 92 to 42. The same
fault was older at the top level: [offd] under Dim[Region] = S was BLANK.

Expected values come from the DAX rules, not from Desktop (no battery has
measured window functions in measures yet): OFFSET, INDEX and WINDOW return
rows OF THE RELATION, and CALCULATE applies them to every column of it; the
current row is the relation's row named by the outer values of the ORDERBY
and PARTITIONBY columns (MATCHBY when given); where those columns have
several values, the function is evaluated for each existing combination and
the rows are united (the documented "apply semantics"). For the shapes that
0.9.146 answered from its caller row the values are 0.9.146's; the engine's
own CALCULATE(CALCULATE(<body>)) written inline gives them too.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "Fact": {"columns": ["Region", "Value"],
             "rows": [["N", 10], ["S", 20], ["N", 5], ["W", 7]]},
    "Dim": {"columns": ["Region", "Name"],
            "rows": [["N", "North"], ["S", "South"], ["W", "West"]]},
    "S": {"columns": ["Cat", "Reg", "V"],
          "rows": [["A", "N", 30], ["B", "N", 35], ["C", "S", 40], ["D", "S", 75]]},
}
RELS = [{"FromTable": "Fact", "FromColumn": "Region", "ToTable": "Dim",
         "ToColumn": "Region", "IsActive": 1}]
MEASURES = {
    "mf": "SUM(Fact[Value])",
    "offd": "CALCULATE(SUM(Fact[Value]), OFFSET(-1, ALL(Dim), ORDERBY(Dim[Region], ASC)))",
    "idxd": "CALCULATE(SUM(Fact[Value]), INDEX(1, ALL(Dim), ORDERBY(Dim[Region], ASC)))",
    "wind": "CALCULATE(SUM(Fact[Value]), WINDOW(1, ABS, 0, REL, ALL(Dim), "
            "ORDERBY(Dim[Region], ASC)))",
    "offname": "CONCATENATEX(OFFSET(-1, ALL(Dim), ORDERBY(Dim[Region], ASC)), Dim[Name])",
    "idxname": "CONCATENATEX(INDEX(1, ALL(Dim), ORDERBY(Dim[Region], ASC)), Dim[Name])",
    "off": "CALCULATE(SUM(Fact[Value]), OFFSET(-1, ALL(Dim[Region]), ORDERBY(Dim[Region], ASC)))",
    "win": "CALCULATE(SUM(Fact[Value]), WINDOW(1, ABS, 0, REL, ALL(Dim[Region]), "
           "ORDERBY(Dim[Region], ASC)))",
    "prevf": "CALCULATE(SUM(Fact[Value]), OFFSET(-1, ALL(Fact), ORDERBY(Fact[Value], ASC)))",
    "prevsel": "CALCULATE([mf], OFFSET(-1, ALLSELECTED(Dim), ORDERBY(Dim[Region], ASC)))",
    "growth": "[mf] - [prevsel]",
    "sv": "SUM(S[V])",
    "off_p": "CALCULATE(SUM(S[V]), OFFSET(-1, ALL(S), ORDERBY(S[V]), PARTITIONBY(S[Reg])))",
    "idx_p": "CALCULATE(SUM(S[V]), INDEX(1, ALL(S), ORDERBY(S[V]), PARTITIONBY(S[Reg])))",
    "win_p": "CALCULATE(SUM(S[V]), WINDOW(1, ABS, 0, REL, ALL(S), ORDERBY(S[V]), "
             "PARTITIONBY(S[Reg])))",
    "rn_m": "ROWNUMBER(ALL(S), ORDERBY(S[V], DESC), MATCHBY(S[Cat]))",
    "rn_reg": "ROWNUMBER(ALL(S), ORDERBY(S[Reg]))",
    "rk_reg": "RANK(DENSE, ALL(S), ORDERBY(S[Reg]))",
    "off_all": "CALCULATE(SUM(S[V]), OFFSET(-1, ALL(S), ORDERBY(S[Cat])))",
    "off_allcol": "CALCULATE(SUM(S[V]), OFFSET(-1, ALL(S[Cat]), ORDERBY(S[Cat])))",
    "off_tbl": "CALCULATE(SUM(S[V]), OFFSET(-1, S, ORDERBY(S[Cat])))",
    "idx_all": "CALCULATE(SUM(S[V]), INDEX(1, ALL(S), ORDERBY(S[Cat])))",
    "win_all": "CALCULATE(SUM(S[V]), WINDOW(1, ABS, 0, REL, ALL(S), ORDERBY(S[Cat])))",
}
HOME = {"mf": "Fact", "offd": "Fact", "idxd": "Fact", "wind": "Fact", "offname": "Fact",
        "idxname": "Fact", "off": "Fact", "win": "Fact", "prevf": "Fact", "prevsel": "Fact",
        "growth": "Fact"}


def _ev(expr, filters=None):
    de._engine.eval_errors.clear()
    measures = dict(MEASURES, m=expr)
    homes = {k: HOME.get(k, "S") for k in measures}
    kw = {"filter_context": filters} if filters else {}
    return de.evaluate_measures_batch(["m"], TABLES, measures, relationships=RELS,
                                      measure_tables=homes, **kw)["m"]


# ---- a window measure called per row of an iterator: 0.9.146's answers ----

CALLED_PER_ROW = [
    ("offset_all_dim", 'CONCATENATEX(Dim, [offd], ",", Dim[Region], ASC)', ",15,20"),
    ("index_all_dim", 'CONCATENATEX(Dim, [idxd], ",", Dim[Region], ASC)', "15,15,15"),
    ("window_all_dim", 'CONCATENATEX(Dim, [wind], ",", Dim[Region], ASC)', "15,35,42"),
    ("offset_rows_names", 'CONCATENATEX(Dim, [offname], ",", Dim[Region], ASC)', ",North,South"),
    ("index_rows_names", 'CONCATENATEX(Dim, [idxname], ",", Dim[Region], ASC)',
     "North,North,North"),
    ("filter_on_offset", 'CONCATENATEX(FILTER(Dim, [offd] <> 0), Dim[Region], ",", '
                         'Dim[Region], ASC)', "S,W"),
    ("sumx_offset", "SUMX(Dim, [offd])", 35),
    ("sumx_window", "SUMX(Dim, [wind])", 92),
    ("offset_all_fact", "SUMX(Fact, [prevf])", 22),
    ("offset_allselected", 'CONCATENATEX(Dim, [prevsel], ",", Dim[Region], ASC)', ",15,20"),
    ("growth_filter", 'CONCATENATEX(FILTER(Dim, [growth] > 0), Dim[Region], ",", '
                      'Dim[Region], ASC)', "N,S"),
    ("averagex_offset", "AVERAGEX(Dim, [prevsel])", 17.5),
    ("partition_offset", 'CONCATENATEX(S, [off_p], ",", S[Cat], ASC)', ",30,,40"),
    ("partition_index", 'CONCATENATEX(S, [idx_p], ",", S[Cat], ASC)', "30,30,40,40"),
    ("partition_window", 'CONCATENATEX(S, [win_p], ",", S[Cat], ASC)', "30,65,40,115"),
    ("matchby_rownumber", 'CONCATENATEX(S, [rn_m], ",", S[Cat], ASC)', "4,3,2,1"),
    # ORDERBY(S[Reg]) is not unique: the caller's other columns tell the rows apart
    ("rownumber_ties_told_apart", 'CONCATENATEX(S, [rn_reg], ",", S[Cat], ASC)', "1,2,3,4"),
]


@pytest.mark.parametrize("expr,want", [c[1:] for c in CALLED_PER_ROW],
                         ids=[c[0] for c in CALLED_PER_ROW])
def test_window_measure_called_per_row(expr, want):
    assert _ev(expr) == want


def test_the_same_as_the_body_written_inline():
    # a measure reference is CALCULATE(<body>); written inline the engine
    # gave ',15,20' / 92 for CALCULATE(<body>) and ',,' / 42 for
    # CALCULATE(CALCULATE(<body>)) -- the top-level path
    body = "CALCULATE(SUM(Fact[Value]), OFFSET(-1, ALL(Dim), ORDERBY(Dim[Region], ASC)))"
    assert _ev(f'CONCATENATEX(Dim, CALCULATE({body}), ",", Dim[Region], ASC)') == ",15,20"
    wbody = ("CALCULATE(SUM(Fact[Value]), WINDOW(1, ABS, 0, REL, ALL(Dim), "
             "ORDERBY(Dim[Region], ASC)))")
    assert _ev(f"SUMX(Dim, CALCULATE({wbody}))") == 92


# ---- no row context at all: the filter context names the current row ----

S_SOUTH = {"Dim.Region": ["S"], "Dim.Name": ["South"]}


@pytest.mark.parametrize("measure,filters,want", [
    ("offd", S_SOUTH, 15),
    ("offd", {"Dim.Region": ["S"]}, 15),
    ("wind", S_SOUTH, 35),
    ("offname", S_SOUTH, "North"),
    # OFFSET of ALL(S) returns every column of row A: it overrides Cat = B
    ("off_all", {"S.Cat": ["B"]}, 30),
    ("off_allcol", {"S.Cat": ["B"]}, 30),
    ("idx_all", {"S.Cat": ["B"]}, 30),
    ("win_all", {"S.Cat": ["B"]}, 65),
    ("off_p", {"S.Cat": ["B"]}, 30),
    ("rn_m", {"S.Cat": ["B"]}, 3),
    # one group of a visual by S[Cat]
    ("off_p", {"S.Cat": ["D"]}, 40),
    ("idx_p", {"S.Cat": ["D"]}, 40),
    ("win_p", {"S.Cat": ["D"]}, 115),
    # filters on two columns: the second tells the Reg = N rows apart
    ("rn_reg", {"S.Reg": ["N"], "S.V": [35]}, 2),
])
def test_filter_context_names_the_current_row(measure, filters, want):
    assert _ev(f"[{measure}]", filters) == want


@pytest.mark.parametrize("cat", ["A", "B", "C", "D"])
def test_an_explicit_table_relation_is_evaluated_in_the_context(cat):
    # OFFSET(-1, S, ...) under S[Cat] = <cat>: the relation is that one row
    # (the reason a visual's "previous" uses ALLSELECTED); the visual axis
    # answered the previous group's sum
    assert _ev("[off_tbl]", {"S.Cat": [cat]}) is None
    assert _ev("[sv]", {"S.Cat": [cat]}) is not None


# ---- several current rows: the union ("apply semantics") ----

@pytest.mark.parametrize("expr,filters,want", [
    # a caller row with no lineage: every region is current, OFFSET(-1)
    # unites N and S (0.9.146 BLANK; the visual axis ignored the filter, 42)
    ('CONCATENATEX(GENERATESERIES(1, 2), [off], ",", [Value], ASC)', None, "35,35"),
    ('CONCATENATEX(GENERATESERIES(1, 2), [offd], ",", [Value], ASC)', None, "35,35"),
    ('CONCATENATEX(GENERATESERIES(1, 2), [win], ",", [Value], ASC)', None, "42,42"),
    # a total: the previous row of every row, per partition
    ("[off_p]", None, 70),
    ("[idx_p]", None, 70),
    ("[win_p]", None, 180),
    ("[off_all]", None, 105),
    # a group of a visual by S[Reg]: C and D are current
    ("[off_all]", {"S.Reg": ["S"]}, 75),
    ("[off_allcol]", {"S.Reg": ["S"]}, 40),
    ("[off_tbl]", {"S.Reg": ["S"]}, 40),
    ("[win_p]", {"S.Reg": ["N"]}, 65),
    ('CONCATENATEX(VALUES(S[Reg]), [off_p], ",", S[Reg], ASC)', None, "30,40"),
])
def test_several_current_rows_unite(expr, filters, want):
    assert _ev(expr, filters) == want


def test_a_scalar_window_function_of_several_rows():
    # RANK: the Reg = S rows share the rank; ROWNUMBER has no one answer
    assert _ev("[rk_reg]", {"S.Reg": ["S"]}) == 2
    assert _ev("[rn_reg]", {"S.Reg": ["S"]}) is None


def test_a_total_sorts_each_partition_once(monkeypatch):
    # four current rows in two partitions: two sorts, not one per row
    calls = []
    orig = de.DAXEngine._win_sort

    def counting(self, rows, *a, **k):
        calls.append(len(rows))
        return orig(self, rows, *a, **k)

    monkeypatch.setattr(de.DAXEngine, "_win_sort", counting)
    assert _ev("[off_p]") == 70
    assert sorted(calls) == [2, 2]


def test_a_row_the_filter_names_needs_no_visual_axis(monkeypatch):
    # a RANK measure called per row: the relation is explicit and the
    # caller's filter names the row, so no axis is built per call
    calls = []
    orig = de.DAXEngine._win_visual_axis

    def counting(self, *a, **k):
        calls.append(1)
        return orig(self, *a, **k)

    monkeypatch.setattr(de.DAXEngine, "_win_visual_axis", counting)
    measures = {"rk": "RANK(DENSE, ALL(S[Cat]), ORDERBY(S[Cat], DESC))"}
    MEASURES.update(measures)
    try:
        assert _ev('CONCATENATEX(VALUES(S[Cat]), [rk], ",", S[Cat], ASC)') == "4,3,2,1"
    finally:
        del MEASURES["rk"]
    assert calls == []
