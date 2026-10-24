"""Issue #222: a text relationship key reaches the rows of that text alone.

A relationship carries a filter to the other table as its keys' spellings,
and the lookup there also tried each spelling's numeric alternates (#39:
an Int64 key 2024 against a Double cell 2024.0). For a TEXT key that is
float() of the text, so the one side's '01' was also spelled '1' and took
the fact rows of the other key '1': CALCULATE(SUM(TK[v]), DK[code] = "01")
answered 15, the fact's own TK[code] = "01" 10. The column store compares a
text key as text; into a text column no numeric spellings are tried now. A
number key still brings its int and float spellings (_join_key_aliases).

Expected values: Power BI Desktop 2.152 over ADOMD (build_b210.py, the DK /
TK tables), through pbix-mcp's builder, reader and engine; the hand-built
cases follow from the same answers (TK's '01' relates to DK's '01' alone)."""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

DK = [("01", 1), ("1", 2), ("7", 3)]
TK = [("01", 10), ("1", 5), ("7", 1)]

# (probe, DAX, Desktop's answer); 0.9.145 answered the one in the comment
DESKTOP = [
    ("k_dim_01", 'CALCULATE(SUM(TK[v]), DK[code] = "01")', "10"),        # 15
    ("k_dim_x1", "CALCULATE(SUM(TK[v]), DK[x] = 1)", "10"),              # 15
    ("k_dim_x2", "CALCULATE(SUM(TK[v]), DK[x] <= 2)", "15"),
    ("k_dim_x3", "CALCULATE(SUM(TK[v]), DK[x] <= 3)", "16"),
    ("k_fact_01", 'CALCULATE(SUM(TK[v]), TK[code] = "01")', "10"),
    ("k_count_x2", "CALCULATE(COUNT(TK[v]), DK[x] <= 2)", "2"),
]


@pytest.fixture(scope="module")
def answers(tmp_path_factory):
    import warnings

    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    warnings.simplefilter("ignore")
    path = str(tmp_path_factory.mktemp("i222") / "b222.pbix")
    b = PBIXBuilder("b222")
    b.add_table("DK", [{"name": "code", "data_type": "String"}, {"name": "x", "data_type": "Int64"}],
                rows=[{"code": c, "x": x} for c, x in DK])
    b.add_table("TK", [{"name": "code", "data_type": "String"}, {"name": "v", "data_type": "Int64"}],
                rows=[{"code": c, "v": v} for c, v in TK])
    b.add_relationship("TK", "code", "DK", "code")
    b.add_page("Page 1")
    b.save(path)
    S.pbix_open(path, "i222")
    try:
        ctx = S._get_dax_context("i222")
        measures = {name: dax for name, dax, _want in DESKTOP}
        defs = dict(ctx["measure_defs"], **measures)
        mt = dict(ctx.get("measure_tables") or {}, **{k: "TK" for k in measures})
        got = de.evaluate_measures_smart(
            list(measures), ctx["tables"], defs, {}, ctx["date_table"], ctx["date_column"],
            ctx["relationships"], simulate_row_context=False, measure_tables=mt,
            model_columns=ctx.get("model_columns"), date_tables=ctx.get("date_tables"))
    finally:
        S.pbix_close("i222")
    return got


def _adomd(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


@pytest.mark.parametrize("probe,dax,want", DESKTOP, ids=[p[0] for p in DESKTOP])
def test_matches_desktop(answers, probe, dax, want):
    assert _adomd(answers.get(probe)) == want


RELS = [{"FromTable": "TK", "FromColumn": "code", "ToTable": "DK", "ToColumn": "code", "IsActive": 1}]


def _tables():
    return {"DK": {"columns": ["code", "x"], "rows": [list(r) for r in DK]},
            "TK": {"columns": ["code", "v"], "rows": [list(r) for r in TK]}}


def _ev(measure, fc=None, tables=None, rels=RELS):
    ctx = de.DAXContext(tables or _tables(), {"M": measure}, None, None, fc or {}, rels)
    return de.DAXEngine().evaluate_measure("M", ctx)


@pytest.mark.parametrize("fc,want", [
    ({"DK.code": ["01"]}, 10),       # 0.9.145: 15
    ({"DK.code": ["1"]}, 5),
    ({"DK.x": [1]}, 10),             # 0.9.145: 15
    ({"DK.x": [1, 2]}, 15),
    ({"DK.code": ["7"]}, 1),
])
def test_a_dimension_filter_reaches_its_own_text_key(fc, want):
    assert _ev("SUM(TK[v])", fc) == want


def test_a_fact_row_relates_to_its_own_text_key():
    """Across the relationship the other way (an expanded table filter, a
    bidirectional filter) TK's '01' reaches DK's '01' alone."""
    assert _ev("CALCULATE(COUNTROWS(DK), FILTER(TK, TK[v] = 10))") == 1                    # 0.9.145: 2
    assert _ev("CALCULATE(SUM(DK[x]), FILTER(TK, TK[v] = 10))") == 1                       # 0.9.145: 3
    assert _ev("CALCULATE(SUM(DK[x]), CROSSFILTER(TK[code], DK[code], BOTH), TK[v] = 10)") == 1  # 0.9.145: 3
    assert _ev('CONCATENATEX(TK, TK[code] & "=" & RELATED(DK[x]), ",", TK[v], ASC)') == "7=3,1=2,01=1"


def test_number_keys_keep_their_int_and_float_spellings():
    """An Int64 key against Double cells, and the other way (#39)."""
    tables = {"D": {"columns": ["k", "n"], "rows": [[1, "a"], [2, "b"]]},
              "F": {"columns": ["k", "v"], "rows": [[1.0, 10], [2.0, 5], [1.0, 1]]}}
    rels = [{"FromTable": "F", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1}]
    assert _ev("SUM(F[v])", {"D.n": ["a"]}, tables, rels) == 11
    tables = {"D": {"columns": ["k", "n"], "rows": [[1.0, "a"], [2.0, "b"]]},
              "F": {"columns": ["k", "v"], "rows": [[1, 10], [2, 5], [1, 1]]}}
    assert _ev("SUM(F[v])", {"D.n": ["b"]}, tables, rels) == 5


def test_a_number_key_still_reaches_its_text_spelling():
    """The number 1 is spelled '1' (and '1.0'); a text key column holding
    those spellings is reached as before -- never '01'."""
    tables = {"D": {"columns": ["k", "n"], "rows": [[1, "a"], [7, "b"]]},
              "F": {"columns": ["k", "v"], "rows": [["1", 10], ["01", 5], ["7", 1]]}}
    rels = [{"FromTable": "F", "FromColumn": "k", "ToTable": "D", "ToColumn": "k", "IsActive": 1}]
    assert _ev("SUM(F[v])", {"D.n": ["a"]}, tables, rels) == 10


def test_spellings_into_a_text_column():
    assert de._index_spellings("01", False) == ("01",)
    assert de._index_spellings("2024", False) == ("2024",)
    assert de._index_spellings("2020-01-01 00:00:00", False) == ("2020-01-01 00:00:00", "2020-01-01")   # #126
    assert de._index_spellings("01") == ("01", "1.0", "1")     # any other column: as before (#39)
