"""Issue #82: the blank (unknown) member of a violated relationship.

When a relationship joins a row whose key matches no row of the one side (or
a blank key), Power BI gives the one-side table a BLANK ROW and attributes
those rows to it. The engine modelled it only for ALL over the key column and
multi-hop filters; VALUES, ALL over other columns, filters that admit BLANK,
grouping, inactive / bidirectional / 1:1 relationships and the top of a
snowflake all left it out. ISBLANK(T[C]), NOT ISBLANK(T[C]) and T[C] = BLANK()
as CALCULATE filters applied no filter at all.

Every expected value below is Power BI Desktop 2.152's, over ADOMD, on this
same model -- identical before and after a full refresh, so the builder's
encoding (#81) is Desktop's too.
"""
from __future__ import annotations

import json

import pytest

from pbix_mcp import server as S
from pbix_mcp.builder import PBIXBuilder

pytestmark = pytest.mark.unit


def _pair(b, dim, fact, dim_rows, fact_rows, **rel):
    b.add_table(dim, [{"name": "Key", "data_type": "Int64"}, {"name": "Name", "data_type": "String"}],
                rows=[{"Key": k, "Name": n} for k, n in dim_rows])
    b.add_table(fact, [{"name": "Key", "data_type": "Int64"}, {"name": "amt", "data_type": "Int64"}],
                rows=[{"Key": k, "amt": a} for k, a in fact_rows])
    b.add_relationship(fact, "Key", dim, "Key", **rel)


def build_blank_member_model(path, measures):
    """The Desktop battery model. Every pair has fact keys with no dimension
    row, except CD/CF (the control):

    BD <- BF   orphan keys 98, 99 (amt 16 + 4 = the blank row's 20)
    SD <- SF   string keys, orphans 'zz', 'q'
    ND <- NF   blank keys, no orphans
    QD <- QF   INACTIVE relationship, orphan 7
    WD <- WF   bidirectional, orphans 7, 8
    KT <- KM <- KF   snowflake, orphan 9 at the bottom
    RD <- RF   a REAL blank Name next to orphan 4
    ZD <- ZF   numeric key 0 next to orphan 5 (BLANK = 0)
    OA <-> OB  1:1, unmatched keys on both sides
    MD <- MF   many-to-many, unmatched 3
    """
    b = PBIXBuilder("blankmember")
    _pair(b, "BD", "BF", [(10, "X"), (20, "Y"), (30, "Z")],
          [(10, 1), (20, 2), (99, 4), (30, 8), (98, 16), (10, 32)])
    b.add_table("SD", [{"name": "Code", "data_type": "String"}, {"name": "Label", "data_type": "String"}],
                rows=[{"Code": "b", "Label": "Bee"}, {"Code": "a", "Label": "Ay"}])
    b.add_table("SF", [{"name": "Code", "data_type": "String"}, {"name": "amt", "data_type": "Int64"}],
                rows=[{"Code": "a", "amt": 1}, {"Code": "zz", "amt": 2},
                      {"Code": "b", "amt": 4}, {"Code": "q", "amt": 8}])
    b.add_relationship("SF", "Code", "SD", "Code")
    _pair(b, "ND", "NF", [(1, "N1"), (2, "N2")], [(1, 1), (None, 2), (2, 4), (None, 8)])
    _pair(b, "CD", "CF", [(1, "C1"), (2, "C2")], [(2, 1), (1, 2), (2, 4)])
    _pair(b, "QD", "QF", [(1, "Q1"), (2, "Q2")], [(1, 1), (7, 2), (2, 4)], is_active=False)
    _pair(b, "WD", "WF", [(1, "W1"), (2, "W2")], [(1, 1), (7, 2), (2, 4), (8, 8)],
          cross_filter_behavior=2)
    b.add_table("KT", [{"name": "TKey", "data_type": "Int64"}, {"name": "Name", "data_type": "String"}],
                rows=[{"TKey": 1, "Name": "TA"}, {"TKey": 2, "Name": "TB"}])
    b.add_table("KM", [{"name": "Key", "data_type": "Int64"}, {"name": "Name", "data_type": "String"},
                       {"name": "TKey", "data_type": "Int64"}],
                rows=[{"Key": 1, "Name": "M1", "TKey": 1}, {"Key": 2, "Name": "M2", "TKey": 2},
                      {"Key": 3, "Name": "M3", "TKey": 1}])
    b.add_table("KF", [{"name": "Key", "data_type": "Int64"}, {"name": "amt", "data_type": "Int64"}],
                rows=[{"Key": 1, "amt": 1}, {"Key": 2, "amt": 2}, {"Key": 9, "amt": 4},
                      {"Key": 3, "amt": 8}])
    b.add_relationship("KF", "Key", "KM", "Key")
    b.add_relationship("KM", "TKey", "KT", "TKey")
    _pair(b, "RD", "RF", [(1, "R1"), (2, None), (3, "R3")], [(1, 1), (2, 2), (3, 4), (4, 8)])
    _pair(b, "ZD", "ZF", [(0, "Zero"), (1, "One")], [(0, 1), (1, 2), (5, 4)])
    for t, rows in (("OA", [(1, "A1"), (2, "A2"), (3, "A3")]),
                    ("OB", [(2, "B2"), (3, "B3"), (4, "B4")])):
        b.add_table(t, [{"name": "Key", "data_type": "Int64"}, {"name": "Name", "data_type": "String"}],
                    rows=[{"Key": k, "Name": n} for k, n in rows])
    b.add_relationship("OB", "Key", "OA", "Key", from_cardinality=1, to_cardinality=1,
                       cross_filter_behavior=2)
    _pair(b, "MD", "MF", [(1, "D1"), (1, "D1b"), (2, "D2")], [(1, 1), (2, 2), (3, 4)],
          from_cardinality=2, to_cardinality=2)
    b.add_table("DU2", [{"name": "K", "data_type": "Int64"}, {"name": "V", "data_type": "String"}],
                rows=[{"K": 1, "V": "x"}, {"K": 1, "V": "x"}, {"K": 2, "V": "y"}])
    for t, m in (("BF", "Total"), ("SF", "STotal"), ("NF", "NTotal"), ("CF", "CTotal"),
                 ("QF", "QTotal"), ("WF", "WTotal"), ("KF", "KTotal"), ("RF", "RTotal"),
                 ("ZF", "ZTotal"), ("MF", "MTotal")):
        b.add_measure(t, m, f"SUM({t}[amt])")
    b.add_measure("QF", "QTotalU", "CALCULATE([QTotal], USERELATIONSHIP(QF[Key], QD[Key]))")
    b.add_measure("BF", "Names", "COUNTROWS(VALUES(BD[Name]))")
    b.add_measure("BF", "InScope", "SUMX(VALUES(BD[Name]), IF(CALCULATE(ISINSCOPE(BD[Name])), 1, 0))")
    b.add_measure("BF", "IsIn", "ISINSCOPE(BD[Name])")
    b.add_measure("OB", "OBCount", "COUNTROWS(OB)")
    b.add_measure("OA", "OACount", "COUNTROWS(OA)")
    for name, expr in measures.items():
        b.add_measure("BF", name, expr)
    b.add_page("Page 1")
    b.save(path)
    alias = "bm_" + str(abs(hash(path)))[:8]
    assert json.loads(S.pbix_open(path, alias))["success"]
    return alias


def scalar(alias, name):
    r = json.loads(S.pbix_evaluate_dax(alias, name, "{}", apply_default_filters=False))
    assert r["success"], r
    (res,) = r["results"]
    return None if res.get("status") == "blank" else res.get("value")


def grouped(alias, col, measures):
    """Groups as SUMMARIZECOLUMNS returns them: a group whose every measure
    is blank is dropped."""
    key = col.replace("[", ".").rstrip("]")
    r = json.loads(S.pbix_evaluate_dax_grouped(alias, ",".join(measures), key))
    assert r["success"], r
    return sorted(([list(g["key"].values())[0]] + [g["values"].get(m) for m in measures]
                   for g in r["data"]["groups"]
                   if any(g["values"].get(m) is not None for m in measures)), key=repr)


def same(got, want):
    if isinstance(want, float) or isinstance(got, float):
        return got is not None and want is not None and abs(float(got) - float(want)) < 1e-9
    return got == want


BLANK = {'v_name': ('COUNTROWS(VALUES(BD[Name]))', 4),
 'v_key': ('COUNTROWS(VALUES(BD[Key]))', 4),
 'v_tbl': ('COUNTROWS(VALUES(BD))', 4),
 'd_name': ('COUNTROWS(DISTINCT(BD[Name]))', 3),
 'd_tbl': ('COUNTROWS(DISTINCT(BD))', 3),
 'a_name': ('COUNTROWS(ALL(BD[Name]))', 4),
 'a_key': ('COUNTROWS(ALL(BD[Key]))', 4),
 'a_tbl': ('COUNTROWS(ALL(BD))', 4),
 'anb_name': ('COUNTROWS(ALLNOBLANKROW(BD[Name]))', 3),
 'anb_tbl': ('COUNTROWS(ALLNOBLANKROW(BD))', 3),
 'cr_tbl': ('COUNTROWS(BD)', 3),
 'dc_name': ('DISTINCTCOUNT(BD[Name])', 3),
 'dcnb_name': ('DISTINCTCOUNTNOBLANK(BD[Name])', 3),
 'cb_name': ('COUNTBLANK(BD[Name])', None),
 'ca_name': ('COUNTA(BD[Name])', 3),
 'cnt_key': ('COUNT(BD[Key])', 3),
 'f_blank': ('COUNTROWS(FILTER(ALL(BD[Name]), ISBLANK(BD[Name])))', 1),
 'concat': ('CONCATENATEX(VALUES(BD[Name]), BD[Name], ",")', 'X,Y,Z,'),
 'as_name': ('COUNTROWS(ALLSELECTED(BD[Name]))', 4),
 'sx_values': ('SUMX(VALUES(BD[Name]), [Total])', 63),
 'sx_tbl': ('SUMX(BD, [Total])', 43),
 'sx_all': ('SUMX(ALL(BD), [Total])', 63),
 'sx_distinct': ('SUMX(DISTINCT(BD[Name]), [Total])', 43),
 'summ_fact': ('COUNTROWS(SUMMARIZE(BF, BD[Name]))', 4),
 'hov': ('HASONEVALUE(BD[Name])', False),
 'minx_key': ('MINX(VALUES(BD[Key]), BD[Key])', 10),
 'max_key': ('MAX(BD[Key])', 30),
 'min_name': ('MIN(BD[Name])', 'X'),
 'c_eqblank': ('CALCULATE([Total], BD[Name] = BLANK())', 20),
 'c_isblank': ('CALCULATE([Total], ISBLANK(BD[Name]))', 20),
 'c_ne': ('CALCULATE([Total], BD[Name] <> "X")', 30),
 'c_notin': ('CALCULATE([Total], NOT BD[Name] IN {"X"})', 30),
 'c_keygt': ('CALCULATE([Total], BD[Key] > 15)', 10),
 'c_in_blank': ('CALCULATE([Total], BD[Name] IN {"X", BLANK()})', 53),
 'c_kf_ne': ('CALCULATE([Total], KEEPFILTERS(BD[Name] <> "X"))', 30),
 'c_eqx': ('CALCULATE([Total], BD[Name] = "X")', 33),
 'c_filter_tbl': ('CALCULATE([Total], FILTER(BD, BD[Name] <> "X"))', 10),
 'c_filter_all': ('CALCULATE([Total], FILTER(ALL(BD), BD[Name] <> "X"))', 30),
 'c_except': ('CALCULATE([Total], EXCEPT(ALL(BD[Name]), {"X"}))', 30),
 'c_keynblank': ('CALCULATE([Total], NOT ISBLANK(BD[Key]))', 43),
 'c_all_blank': ('CALCULATE(COUNTROWS(VALUES(BD[Name])), ALL(BD))', 4),
 'v_under_x': ('CALCULATE(COUNTROWS(VALUES(BD[Name])), BD[Name] = "X")', 1),
 'v_under_fact': ('CALCULATE(COUNTROWS(VALUES(BD[Name])), BF[amt] = 1)', 4),
 'v_under_key': ('CALCULATE(COUNTROWS(VALUES(BD[Name])), BD[Key] = 10)', 1),
 'v_under_ne': ('CALCULATE(COUNTROWS(VALUES(BD[Name])), BD[Name] <> "X")', 3),
 'sv_blank': ('CALCULATE(SELECTEDVALUE(BD[Name], "none"), ISBLANK(BD[Name]))', None),
 'cr_fact_blank': ('CALCULATE(COUNTROWS(BF), ISBLANK(BD[Name]))', 2),
 'nv_name': ('COUNTROWS(VALUES(ND[Name]))', 3),
 'nc_isblank': ('CALCULATE([NTotal], ISBLANK(ND[Name]))', 10),
 'nd_name': ('COUNTROWS(DISTINCT(ND[Name]))', 2),
 'na_name': ('COUNTROWS(ALL(ND[Name]))', 3),
 'sv_label': ('COUNTROWS(VALUES(SD[Label]))', 3),
 'sc_isblank': ('CALCULATE([STotal], ISBLANK(SD[Label]))', 10),
 'cv_name': ('COUNTROWS(VALUES(CD[Name]))', 2),
 'ca_tbl': ('COUNTROWS(ALL(CD))', 2),
 'qv_name': ('COUNTROWS(VALUES(QD[Name]))', 3),
 'qa_name': ('COUNTROWS(ALL(QD[Name]))', 3),
 'qc_blank_u': ('CALCULATE([QTotal], USERELATIONSHIP(QF[Key], QD[Key]), ISBLANK(QD[Name]))', 2),
 'qc_blank': ('CALCULATE([QTotal], ISBLANK(QD[Name]))', 7),
 'wv_name': ('COUNTROWS(VALUES(WD[Name]))', 3),
 'wv_under_fact': ('CALCULATE(COUNTROWS(VALUES(WD[Name])), WF[amt] = 4)', 1),
 'wv_under_fact2': ('CALCULATE(COUNTROWS(VALUES(WD[Name])), WF[amt] = 2)', 1),
 'wc_blank': ('CALCULATE([WTotal], ISBLANK(WD[Name]))', 10),
 'kv_mid': ('COUNTROWS(VALUES(KM[Name]))', 4),
 'kv_top': ('COUNTROWS(VALUES(KT[Name]))', 3),
 'kc_mid_blank': ('CALCULATE([KTotal], ISBLANK(KM[Name]))', 4),
 'kc_top_blank': ('CALCULATE([KTotal], ISBLANK(KT[Name]))', 4),
 'kc_top_a': ('CALCULATE([KTotal], KT[Name] = "TA")', 9),
 'r_all': ('COUNTROWS(ALL(RD[Name]))', 3),
 'r_values': ('COUNTROWS(VALUES(RD[Name]))', 3),
 'r_anb': ('COUNTROWS(ALLNOBLANKROW(RD[Name]))', 3),
 'r_distinct': ('COUNTROWS(DISTINCT(RD[Name]))', 3),
 'r_isblank': ('CALCULATE([RTotal], ISBLANK(RD[Name]))', 10),
 'r_all_tbl': ('COUNTROWS(ALL(RD))', 4),
 'r_anb_tbl': ('COUNTROWS(ALLNOBLANKROW(RD))', 3),
 'r_values_tbl': ('COUNTROWS(VALUES(RD))', 4),
 'r_cb': ('COUNTBLANK(RD[Name])', 1),
 'r_dc': ('DISTINCTCOUNT(RD[Name])', 3),
 'r_dcnb': ('DISTINCTCOUNTNOBLANK(RD[Name])', 2),
 'r_ne': ('CALCULATE([RTotal], RD[Name] <> "R1")', 14),
 'z_eqblank': ('CALCULATE([ZTotal], ZD[Key] = BLANK())', 5),
 'z_isblank': ('CALCULATE([ZTotal], ISBLANK(ZD[Key]))', 4),
 'z_eq0': ('CALCULATE([ZTotal], ZD[Key] = 0)', 5),
 'z_neblank': ('CALCULATE([ZTotal], ZD[Key] <> BLANK())', 2),
 'z_lt1': ('CALCULATE([ZTotal], ZD[Key] < 1)', 5),
 'b_key_eqblank': ('CALCULATE([Total], BD[Key] = BLANK())', 20),
 'o_va': ('COUNTROWS(VALUES(OA[Name]))', 4),
 'o_vb': ('COUNTROWS(VALUES(OB[Name]))', 4),
 'o_aa': ('COUNTROWS(ALL(OA[Name]))', 4),
 'o_ab': ('COUNTROWS(ALL(OB[Name]))', 4),
 'o_blank_a': ('CALCULATE(COUNTROWS(OB), ISBLANK(OA[Name]))', 1),
 'o_blank_b': ('CALCULATE(COUNTROWS(OA), ISBLANK(OB[Name]))', 1),
 'm_v': ('COUNTROWS(VALUES(MD[Name]))', 3),
 'm_a': ('COUNTROWS(ALL(MD[Name]))', 3),
 'm_blank': ('CALCULATE([MTotal], ISBLANK(MD[Name]))', None),
 'm_total': ('[MTotal]', 7),
 'dup_values': ('COUNTROWS(VALUES(DU2))', 3),
 'dup_distinct': ('COUNTROWS(DISTINCT(DU2))', 2),
 'dup_all': ('COUNTROWS(ALL(DU2))', 3),
 'mc_all': ('COUNTROWS(ALL(BD[Key], BD[Name]))', 4),
 'mc_anb': ('COUNTROWS(ALLNOBLANKROW(BD[Key], BD[Name]))', 3),
 'filters_cnt': ('COUNTROWS(FILTERS(BD[Name]))', 4),
 'ct_ne': ('COUNTROWS(CALCULATETABLE(VALUES(BD[Name]), BD[Name] <> "X"))', 3),
 'cj': ('COUNTROWS(CROSSJOIN(VALUES(BD[Name]), VALUES(CD[Name])))', 8),
 'countx': ('COUNTX(VALUES(BD[Name]), [Total])', 4),
 'avgx': ('AVERAGEX(VALUES(BD[Name]), [Total])', 15.75),
 'maxx_all': ('MAXX(ALL(BD[Name]), [Total])', 33),
 'minx_all': ('MINX(ALL(BD[Name]), [Total])', 2),
 'lookup': ('LOOKUPVALUE(BD[Name], BD[Key], 99)', None),
 'isempty_blank': ('CALCULATE(ISEMPTY(VALUES(BD[Name])), ISBLANK(BD[Name]))', False),
 'hov_blank': ('CALCULATE(HASONEVALUE(BD[Name]), ISBLANK(BD[Name]))', True),
 'isfilt_blank': ('CALCULATE(ISFILTERED(BD[Name]), ISBLANK(BD[Name]))', True),
 'rank_blank': ('CALCULATE(RANKX(ALL(BD[Name]), [Total]), ISBLANK(BD[Name]))', 2),
 'sx_all_name': ('SUMX(ALL(BD[Name]), [Total])', 63),
 'sx_anb_tbl': ('SUMX(ALLNOBLANKROW(BD), [Total])', 43),
 'values_under_values': ('CALCULATE(COUNTROWS(VALUES(BD[Name])), VALUES(BD[Name]))', 4),
 'kv_mid_under_top_blank': ('CALCULATE(COUNTROWS(VALUES(KM[Name])), ISBLANK(KT[Name]))', 1)}

GROUPED_BLANK = {'g_bd': ('BD[Name]',
          ['Total', 'InScope', 'Names'],
          [['X', 33, 1, 1], ['Y', 2, 1, 1], ['Z', 8, 1, 1], [None, 20, 1, 1]]),
 'g_bd_key': ('BD[Key]', ['Total'], [[10, 33], [20, 2], [30, 8], [None, 20]]),
 'g_sd': ('SD[Label]', ['STotal'], [['Ay', 1], ['Bee', 4], [None, 10]]),
 'g_nd': ('ND[Name]', ['NTotal'], [['N1', 1], ['N2', 4], [None, 10]]),
 'g_cd': ('CD[Name]', ['CTotal'], [['C1', 2], ['C2', 5]]),
 'g_qd': ('QD[Name]', ['QTotal', 'QTotalU'], [['Q1', 7, 1], ['Q2', 7, 4], [None, 7, 2]]),
 'g_wd': ('WD[Name]', ['WTotal'], [['W1', 1], ['W2', 4], [None, 10]]),
 'g_km': ('KM[Name]', ['KTotal'], [['M1', 1], ['M2', 2], ['M3', 8], [None, 4]]),
 'g_kt': ('KT[Name]', ['KTotal'], [['TA', 9], ['TB', 2], [None, 4]]),
 'g_isin': ('BD[Name]', ['IsIn'], [['X', True], ['Y', True], ['Z', True], [None, True]]),
 'g_rd': ('RD[Name]', ['RTotal'], [['R1', 1], ['R3', 4], [None, 10]]),
 'g_zd': ('ZD[Key]', ['ZTotal'], [[0, 1], [1, 2], [None, 4]]),
 'g_oa': ('OA[Name]', ['OBCount'], [['A2', 1], ['A3', 1], [None, 1]]),
 'g_ob': ('OB[Name]', ['OACount'], [['B2', 1], ['B3', 1], [None, 1]]),
 'g_md': ('MD[Name]', ['MTotal'], [['D1', 1], ['D1b', 1], ['D2', 2]])}


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("i82") / "blank_member.pbix")
    alias = build_blank_member_model(path, {n: e for n, (e, _v) in BLANK.items()})
    yield alias
    S._open_files.pop(alias, None)
    S._dax_cache.pop(alias, None)


@pytest.mark.parametrize("name", list(BLANK))
def test_matches_desktop(model, name):
    expr, want = BLANK[name]
    got = scalar(model, name)
    assert same(got, want), f"{expr}: Desktop {want!r}, engine {got!r}"


@pytest.mark.parametrize("gid", list(GROUPED_BLANK))
def test_grouped_tool_matches_desktop(model, gid):
    col, measures, want = GROUPED_BLANK[gid]
    assert grouped(model, col, measures) == want


def test_blank_row_tables_follow_the_relationship_kinds(model):
    from pbix_mcp.dax.engine import DAXContext
    ctx = S._get_dax_context(model)
    dc = DAXContext(ctx["tables"], ctx["measure_defs"], None, None, {}, ctx["relationships"])
    # inactive QD and both sides of the 1:1 have one; many-to-many MD and the
    # control CD do not; KT gets one through KM's blank row
    assert dc.blank_row_tables() == {"BD", "SD", "ND", "QD", "WD", "KM", "KT", "RD", "ZD",
                                     "OA", "OB"}
