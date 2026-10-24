"""Issue #221: an In-set filter on a TEXT column matches its cells as text.

A filter's values were read as a caller may spell them for a column of any
type (_filter_value_keys): the text "None" also as BLANK (the engine's own
spelling of BLANK in its internal key sets), and a text that is wholly an ISO
or d/m/Y date also as that moment. In a Text column those readings matched
other cells: TA[S] = "None" took the BLANK row too, TB[S] = "2020-01-01" the
cell "2020-01-01T00:00:00", TB[S] = "1/2/2020" the cells "01/02/2020" and
"2020-02-01". The same In-set carries TREATAS, KEEPFILTERS(FILTER(VALUES(...)))
and the #203 rewrite of CALCULATE(<aggregate>, FILTER(T, <condition>)), so
all of them answered 3 for 1. Power BI Desktop compares the text alone.

The filter signature shared one spelling for BLANK and the text "None" (and
for 1.0 and "1.0"), so a memo keyed on it could serve one filter's rows for
the other; it now spells each value with its type.

Expected values: Power BI Desktop 2.152 over ADOMD (build_b210.py, the TA /
TB / TC tables), through pbix-mcp's builder, reader and engine; the hand-built
cases below follow from the same answers (a text column's text value selects
that text, BLANK selects BLANK)."""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TA": ([("S", "String"), ("A", "Int64")], [("None", 1), (None, 2), ("x", 4)]),
    "TB": ([("S", "String"), ("A", "Int64")],
           [("2020-01-01", 1), ("2020-01-01T00:00:00", 2), ("x", 4), ("1/2/2020", 8), ("01/02/2020", 16),
            ("2020-02-01", 32)]),
    "TC": ([("TS", "String"), ("A", "Int64")],
           [("2020-01-01T10:00:00+01:00", 1), ("2020-01-01T10:00:00+02:00", 2), ("x", 4)]),
}

# (probe, DAX, Desktop's answer); 0.9.145 answered the one in the comment
DESKTOP = [
    ("a_filter_none", 'CALCULATE(SUM(TA[A]), FILTER(TA, TA[S] = "None"))', "1"),                    # 3
    ("a_values_none", 'CALCULATE(SUM(TA[A]), KEEPFILTERS(FILTER(VALUES(TA[S]), TA[S] = "None")))', "1"),  # 3
    ("a_pred_none", 'CALCULATE(SUM(TA[A]), TA[S] = "None")', "1"),                                  # 3
    ("a_treatas_none", 'CALCULATE(SUM(TA[A]), TREATAS({"None"}, TA[S]))', "1"),                     # 3
    ("a_treatas_blank", 'CALCULATE(SUM(TA[A]), TREATAS({BLANK()}, TA[S]))', "2"),
    ("a_notblank", 'CALCULATE(SUM(TA[A]), FILTER(TA, NOT ISBLANK(TA[S])))', "5"),                    # 7
    ("a_values", 'COUNTROWS(VALUES(TA[S]))', "3"),
    ("b_len10", 'CALCULATE(SUM(TB[A]), FILTER(TB, LEN(TB[S]) = 10))', "49"),                         # 59
    ("b_pred_iso", 'CALCULATE(SUM(TB[A]), TB[S] = "2020-01-01")', "1"),                              # 3
    ("b_len8", 'CALCULATE(SUM(TB[A]), FILTER(TB, LEN(TB[S]) = 8))', "8"),                            # 56
    ("b_pred_dmy", 'CALCULATE(SUM(TB[A]), TB[S] = "1/2/2020")', "8"),                                # 56
    ("b_values_iso", 'CALCULATE(SUM(TB[A]), KEEPFILTERS(FILTER(VALUES(TB[S]), LEN(TB[S]) = 10)))', "49"),  # 59
    ("c_offset", 'CALCULATE(SUM(TC[A]), FILTER(TC, RIGHT(TC[TS], 6) = "+01:00"))', "1"),             # 3
]


@pytest.fixture(scope="module")
def answers(tmp_path_factory):
    import warnings

    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    warnings.simplefilter("ignore")
    path = str(tmp_path_factory.mktemp("i221") / "b221.pbix")
    b = PBIXBuilder("b221")
    for name, (cols, rows) in TABLES.items():
        b.add_table(name, [{"name": c, "data_type": t} for c, t in cols],
                    rows=[{c: v for (c, _), v in zip(cols, r)} for r in rows])
    b.add_page("Page 1")
    b.save(path)
    S.pbix_open(path, "i221")
    try:
        ctx = S._get_dax_context("i221")
        measures = {name: dax for name, dax, _want in DESKTOP}
        defs = dict(ctx["measure_defs"], **measures)
        mt = dict(ctx.get("measure_tables") or {}, **{k: "TA" for k in measures})
        got = de.evaluate_measures_smart(
            list(measures), ctx["tables"], defs, {}, ctx["date_table"], ctx["date_column"],
            ctx["relationships"], simulate_row_context=False, measure_tables=mt,
            model_columns=ctx.get("model_columns"), date_tables=ctx.get("date_tables"))
    finally:
        S.pbix_close("i221")
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


def _ta():
    return {"TA": {"columns": ["S", "A"], "rows": [["None", 1], [None, 2], ["x", 4]]}}


def _sum(tables, fc=None, measure="SUM(TA[A])", rels=None):
    ctx = de.DAXContext(tables, {"M": measure}, None, None, fc or {}, rels)
    return de.DAXEngine().evaluate_measure("M", ctx)


@pytest.mark.parametrize("values,want", [
    (["None"], 1),             # 0.9.145: 3, the BLANK row too
    (["NONE"], 1),             # the column store folds ASCII letters (#102)
    ([None], 2),               # BLANK is BLANK
    (["None", None], 3),
    (["x", "None"], 5),        # 0.9.145: 7
])
def test_a_caller_filter_on_a_text_column(values, want):
    assert _sum(_ta(), {"TA.S": values}) == want


def test_in_and_not_in_predicates_on_a_text_column():
    assert _sum(_ta(), {"TA.S": {"in": ["None"]}}) == 1               # 0.9.145: 3
    assert _sum(_ta(), {"TA.S": {"not_in": ["None"]}}) == 6           # 0.9.145: 4


def test_a_date_spelled_text_is_text_in_a_text_column():
    tb = {"TB": {"columns": ["S", "A"], "rows": [list(r) for r in TABLES["TB"][1]]}}
    assert _sum(tb, {"TB.S": ["2020-01-01"]}, "SUM(TB[A])") == 1        # 0.9.145: 3
    assert _sum(tb, {"TB.S": ["2020-01-01T00:00:00"]}, "SUM(TB[A])") == 2
    assert _sum(tb, {"TB.S": ["01/02/2020"]}, "SUM(TB[A])") == 16       # 0.9.145: 56
    assert _sum(tb, {"TB.S": [" 2020-02-01"]}, "SUM(TB[A])") is None    # 0.9.145: 56


def test_lookups_compare_a_text_column_as_text():
    """LOOKUPVALUE and CONTAINS read the same cell keys."""
    t = {"TL": {"columns": ["S", "A"], "rows": [[None, 2], ["None", 1], ["x", 4]]},
         "TN": {"columns": ["S", "A"], "rows": [[None, 2], ["x", 4]]}}
    assert _sum(t, measure='LOOKUPVALUE(TL[A], TL[S], "None")') == 1      # 0.9.145: 2
    assert _sum(t, measure='IF(CONTAINS(TN, TN[S], "None"), 1, 0)') == 0  # 0.9.145: 1
    assert _sum(t, measure='IF(CONTAINS(TN, TN[S], BLANK()), 1, 0)') == 1


DIM = {"D": {"columns": ["S", "n"], "rows": [["None", 1], ["x", 2]]},
       "F": {"columns": ["S", "A"], "rows": [["None", 1], ["x", 4], ["zz", 2]]}}
RELS = [{"FromTable": "F", "FromColumn": "S", "ToTable": "D", "ToColumn": "S", "IsActive": 1}]


def test_the_text_none_keeps_no_blank_row_of_a_dimension():
    """F's "zz" matches no D row: it reaches D's blank row, which BLANK keeps
    and the text "None" does not."""
    assert _sum(DIM, {"D.S": ["None"]}, "SUM(F[A])", RELS) == 1           # 0.9.145: 3
    assert _sum(DIM, {"D.S": [None]}, "SUM(F[A])", RELS) == 2
    assert _sum(DIM, measure='CALCULATE(SUM(F[A]), D[S] = "None")', rels=RELS) == 1   # 0.9.145: 3
    assert _sum(DIM, measure='CALCULATE(SUM(F[A]), D[S] = BLANK())', rels=RELS) == 2


@pytest.mark.parametrize("order", [("blank", "text"), ("text", "blank")])
def test_blank_and_the_text_none_are_two_filters_to_the_memos(order):
    """One model, one shared cache: whichever filter runs first must not
    answer the other (the cross-filter memo is keyed on the signature)."""
    tables = {k: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]} for k, v in DIM.items()}
    spell = {"blank": [None], "text": ["None"]}
    want = {"blank": 2, "text": 1}
    for which in order:
        assert _sum(tables, {"D.S": spell[which]}, "SUM(F[A])", RELS) == want[which], which


def test_the_signature_spells_each_value_with_its_type():
    sig = de.DAXContext._filter_sig
    assert sig([None]) != sig(["None"])
    assert sig([1.0]) != sig(["1.0"])
    assert sig(["a", "b"]) == sig(["b", "a"])
    big = [f"k{i}" for i in range(5000)]
    exact = de.DAXContext._filter_sig_exact
    assert exact(big + [None]) != exact(big + ["None"])
    assert exact(big + [1.0]) != exact(big + ["1.0"])
    assert exact(list(reversed(big))) == exact(big)


# Kept: what a column of another type reads a value as
def test_other_columns_keep_their_readings():
    nums = {"N": {"columns": ["V", "A"], "rows": [[2024.0, 1], [None, 2], [7.0, 4]]}}
    assert _sum(nums, {"N.V": ["2024"]}, "SUM(N[A])") == 1               # a numeric string, a Double
    assert _sum(nums, {"N.V": [2024]}, "SUM(N[A])") == 1                 # an int, a Double (#39)
    assert _sum(nums, {"N.V": ["None"]}, "SUM(N[A])") == 2               # the engine's BLANK spelling
    codes = {"C": {"columns": ["K", "A"], "rows": [["7", 1], ["07", 2]]}}
    assert _sum(codes, {"C.K": [7]}, "SUM(C[A])") == 1                   # a number, its text (#102)
    from datetime import datetime
    dt = {"T": {"columns": ["D", "A"], "rows": [[datetime(2020, 1, 1), 1], [datetime(2020, 1, 1, 9), 2]]}}
    assert _sum(dt, {"T.D": ["2020-01-01"]}, "SUM(T[A])") == 1           # ISO text, a date column
    assert _sum(dt, {"T.D": ["2020-01-01T09:00:00"]}, "SUM(T[A])") == 2
    # a column that keeps every date as text still reads them as dates (#42)
    dtext = {"T": {"columns": ["D", "A"], "rows": [["2009-12-01 00:00:00", 5], ["2009-12-02 00:00:00", 7]]}}
    assert _sum(dtext, {"T.D": ["2009-12-01"]}, "SUM(T[A])") == 5


def test_text_column_classification():
    rows = {"t": [["a"], [None]], "dates": [["2020-01-01"], [None]], "mixed": [["a"], [1]],
            "blank": [[None]], "num": [[1.0]], "both": [["2020-01-01"], ["x"]]}
    tables = {k: {"columns": ["c"], "rows": r} for k, r in rows.items()}
    ctx = de.DAXContext(tables, {})
    got = {k: ctx._text_column(t, 0) for k, t in tables.items()}
    assert got == {"t": True, "dates": False, "mixed": False, "blank": False, "num": False, "both": True}
