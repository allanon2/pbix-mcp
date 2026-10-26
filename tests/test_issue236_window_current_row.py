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

The expected values were first derived from the DAX rules; Power BI Desktop
2.152 has since answered every one of these shapes (build_b247.py; the probe
ids are noted), and the values are its answers: OFFSET, INDEX and WINDOW
return rows OF THE RELATION, and CALCULATE applies them to every column of
it; where no row context names the current row, the relation's columns do,
with the values they have in the filter context; where several rows are
current, OFFSET / INDEX / WINDOW unite their rows and RANK / ROWNUMBER are
BLANK (#247).

Desktop refuted the cases over ALL(S), S and ALL(Fact) -- tables no
relationship gives a key -- with "The relation parameter of OFFSET may have
duplicate rows. This is not allowed." (#247), whatever the context, and
ROWNUMBER(ALL(S), ORDERBY(S[Reg])) with "Duplicate rows detected in the
relation parameter of the ROWNUMBER function." (#255); this
file expected values there (0.9.146's, or the engine's). RANK of the two
current rows under S[Reg] = "S" is BLANK, not their shared rank
(#258).
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
    ("offset_allselected", 'CONCATENATEX(Dim, [prevsel], ",", Dim[Region], ASC)', ",15,20"),
    ("growth_filter", 'CONCATENATEX(FILTER(Dim, [growth] > 0), Dim[Region], ",", '
                      'Dim[Region], ASC)', "N,S"),
    ("averagex_offset", "AVERAGEX(Dim, [prevsel])", 17.5),
    ("matchby_rownumber", 'CONCATENATEX(S, [rn_m], ",", S[Cat], ASC)', "4,3,2,1"),
]


@pytest.mark.parametrize("expr,want", [c[1:] for c in CALLED_PER_ROW],
                         ids=[c[0] for c in CALLED_PER_ROW])
def test_window_measure_called_per_row(expr, want):
    assert _ev(expr) == want


DUP = "may have duplicate rows"
TIE = "Duplicate rows detected in the relation parameter of the ROWNUMBER function."


def _err(expr, filters=None):
    """The error Desktop fails the measure with: it fails the whole query,
    though the refusal is in a measure the query only references."""
    assert _ev(expr, filters) is None
    err = de._engine.eval_errors.get("m")
    assert err, expr
    return err


@pytest.mark.parametrize("expr,fn", [
    ("SUMX(Fact, [prevf])", "OFFSET"),                                     # w_offset_all_fact
    ('CONCATENATEX(S, [off_p], ",", S[Cat], ASC)', "OFFSET"),              # w_partition_offset
    ('CONCATENATEX(S, [idx_p], ",", S[Cat], ASC)', "INDEX"),               # w_partition_index
    ('CONCATENATEX(S, [win_p], ",", S[Cat], ASC)', "WINDOW"),              # w_partition_window
])
def test_called_per_row_over_a_relation_without_a_key(expr, fn):
    err = _err(expr)
    assert DUP in err and f"of {fn} " in err, err


def test_a_rownumber_tie_is_no_row_of_the_caller():
    # w_rownumber_ties: ORDERBY(S[Reg]) ties; the caller's other columns did
    # tell the rows apart (0.9.146 '1,2,3,4')
    assert TIE in _err('CONCATENATEX(S, [rn_reg], ",", S[Cat], ASC)')


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
    ("offname", S_SOUTH, "North"),                      # f_offname_ss
    ("off_allcol", {"S.Cat": ["B"]}, 30),               # f_off_allcol_b
    ("rn_m", {"S.Cat": ["B"]}, 3),                      # f_rn_m_b
])
def test_filter_context_names_the_current_row(measure, filters, want):
    assert _ev(f"[{measure}]", filters) == want


@pytest.mark.parametrize("measure,filters,fn", [
    ("off_all", {"S.Cat": ["B"]}, "OFFSET"),            # f_off_all_b
    ("idx_all", {"S.Cat": ["B"]}, "INDEX"),             # f_idx_all_b
    ("win_all", {"S.Cat": ["B"]}, "WINDOW"),            # f_win_all_b
    ("off_p", {"S.Cat": ["B"]}, "OFFSET"),              # f_off_p_b
    # one group of a visual by S[Cat]
    ("off_p", {"S.Cat": ["D"]}, "OFFSET"),              # f_off_p_d
    ("idx_p", {"S.Cat": ["D"]}, "INDEX"),               # f_idx_p_d
    ("win_p", {"S.Cat": ["D"]}, "WINDOW"),              # f_win_p_d
])
def test_a_named_row_of_a_relation_without_a_key(measure, filters, fn):
    err = _err(f"[{measure}]", filters)
    assert DUP in err and f"of {fn} " in err, err


def test_filters_on_two_columns_tell_no_tie_apart():
    # f_rn_reg_n35: the second filter told the Reg = N rows apart (0.9.146: 2)
    assert TIE in _err("[rn_reg]", {"S.Reg": ["N"], "S.V": [35]})


@pytest.mark.parametrize("cat", ["A", "B", "C", "D"])
def test_an_explicit_table_relation_is_refused_in_every_context(cat):
    # OFFSET(-1, S, ...) under S[Cat] = <cat>: S has no key, so Desktop
    # refuses it before it reads the one row the context leaves (f_off_tbl_a,
    # f_off_tbl_c); the visual axis answered the previous group's sum, and
    # 0.9.147 BLANK
    err = _err("[off_tbl]", {"S.Cat": [cat]})
    assert DUP in err and "of OFFSET " in err, err
    assert _ev("[sv]", {"S.Cat": [cat]}) is not None


# ---- several current rows: the union ("apply semantics") ----

@pytest.mark.parametrize("expr,filters,want", [
    # a caller row with no lineage: every region is current, OFFSET(-1)
    # unites N and S (0.9.146 BLANK; the visual axis ignored the filter, 42)
    ('CONCATENATEX(GENERATESERIES(1, 2), [off], ",", [Value], ASC)', None, "35,35"),
    ('CONCATENATEX(GENERATESERIES(1, 2), [offd], ",", [Value], ASC)', None, "35,35"),
    ('CONCATENATEX(GENERATESERIES(1, 2), [win], ",", [Value], ASC)', None, "42,42"),
    # a group of a visual by S[Reg]: C and D are current
    ("[off_allcol]", {"S.Reg": ["S"]}, 40),                         # u_reg_s_off_allcol
])
def test_several_current_rows_unite(expr, filters, want):
    assert _ev(expr, filters) == want


@pytest.mark.parametrize("expr,filters,fn", [
    # a total: the previous row of every row, per partition
    ("[off_p]", None, "OFFSET"),                                    # u_total_off_p
    ("[idx_p]", None, "INDEX"),                                     # u_total_idx_p
    ("[win_p]", None, "WINDOW"),                                    # u_total_win_p
    ("[off_all]", None, "OFFSET"),                                  # u_total_off_all
    ("[off_all]", {"S.Reg": ["S"]}, "OFFSET"),                      # u_reg_s_off_all
    ("[off_tbl]", {"S.Reg": ["S"]}, "OFFSET"),                      # u_reg_s_off_tbl
    ("[win_p]", {"S.Reg": ["N"]}, "WINDOW"),                        # u_reg_n_win_p
    ('CONCATENATEX(VALUES(S[Reg]), [off_p], ",", S[Reg], ASC)', None, "OFFSET"),  # u_values_off_p
])
def test_several_current_rows_of_a_relation_without_a_key(expr, filters, fn):
    err = _err(expr, filters)
    assert DUP in err and f"of {fn} " in err, err


def test_a_scalar_window_function_of_several_rows():
    # RANK of the Reg = S rows, which share the rank: BLANK (u_rk_reg_s);
    # ROWNUMBER: ALL(S) ties on S[Reg] (u_rn_reg_s)
    assert _ev("[rk_reg]", {"S.Reg": ["S"]}) is None
    assert de._engine.eval_errors.get("m") is None
    assert TIE in _err("[rn_reg]", {"S.Reg": ["S"]})


def test_a_total_sorts_each_partition_once(monkeypatch):
    # four current rows in two partitions: two sorts, not one per row. The
    # relation is a column list, which Desktop accepts (build_b253.py
    # o_allcols_p ',A,,C' per row); at the total B and D are united with
    # their previous rows A and C: 30 + 40
    calls = []
    orig = de.DAXEngine._win_sort

    def counting(self, rows, *a, **k):
        calls.append(len(rows))
        return orig(self, rows, *a, **k)

    monkeypatch.setattr(de.DAXEngine, "_win_sort", counting)
    MEASURES["off_lp"] = ("CALCULATE(SUM(S[V]), OFFSET(-1, ALL(S[Reg], S[Cat]), "
                          "ORDERBY(S[Cat]), PARTITIONBY(S[Reg])))")
    try:
        assert _ev("[off_lp]") == 70
    finally:
        del MEASURES["off_lp"]
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
