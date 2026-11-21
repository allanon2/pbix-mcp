"""The 0.9.154 merge keeps 0.9.153+pivotstudio.9's answers where upstream's
re-implementation of the same rules (#373-#383) merged in beside ours and ran
first. Each case below answered otherwise with both in place (a review of the
merge, 2026-11-22); the expected values are .9's own, asked of .9.

Labels: the IFERROR / ISERROR cases and the date formats follow measured rules
(#344: Desktop's type check is not caught; #373: en-CA prints "/" as "-"). The
order of two errors in ADDCOLUMNS and ROW, a literal value heading, and
MEDIANX over an empty or failing table are NOT measured: they pin .9's answer
so a later merge cannot change it silently, and want a Desktop check.
"""
from __future__ import annotations

import json

import pytest

from pbix_mcp import server as S
from pbix_mcp.builder import PBIXBuilder

D = "DATE(2026,1,6) + TIME(13,30,5)"
ERR = object()
CLASH_TEXT_INT = ("DAX comparison operations do not support comparing values of type Text with values of "
                  "type Integer.")

CASES = {
    # measured rules
    "ref_clash_m": ("IFERROR([clash_m], 9)", (ERR, CLASH_TEXT_INT)),
    "ref_clash_lit": ("IFERROR([clash_lit], 9)", (ERR, CLASH_TEXT_INT)),
    "ref_clash_in": ("IFERROR([clash_in], 9)",
                     (ERR, "Function 'CONTAINSROW' does not support comparing values of type Text with values "
                           "of type Integer.")),
    "iserr_ref": ("ISERROR([clash_m])", (ERR, CLASH_TEXT_INT)),
    "fmt_ca_plain": (f'FORMAT({D}, "d/MM/yyyy", "en-CA")', "6-01-2026"),
    "fmt_ca_escaped": (f'FORMAT({D}, "dd\\/MM/yyyy", "en-CA")', "06\\/01-2026"),
    "fmt_ca_quoted": (f'FORMAT({D}, "dd""/""MM", "en-CA")', '06"/"01'),
    "fmt_au_plain": (f'FORMAT({D}, "d/MM/yyyy", "en-AU")', "6/01/2026"),
    # .9's answers, not measured
    "addcol_order": ('COUNTROWS(ADDCOLUMNS(Opp, "a", 1, "Name", 2, "a", 3))',
                     (ERR, "Function 'ADDCOLUMNS' cannot add column [Name] since it already exists.")),
    "addcol_order2": ('COUNTROWS(ADDCOLUMNS(Opp, "q", 1, "rank", 2, "Q", 3))',
                      (ERR, "Function 'ADDCOLUMNS' cannot add column [rank] since it already exists.")),
    "row_dup_then_missing": ('COUNTROWS(ROW("a", 1, "A", 2, "b", ))',
                             (ERR, "Argument '6' in ROW function is required.")),
    "row_err_then_dup": ('COUNTROWS(ROW("a", ERROR("boom"), "A", 2))', (ERR, "boom")),
    "sumcols_lit_value": ('TOCSV(SUMMARIZECOLUMNS(Opp[Name], "x", "Name"))', "'Opp'[Name],[x]\na,Name\nb,Name"),
    "summarize_lit_value": ('TOCSV(SUMMARIZE(Opp, Opp[Name], "x", "Name"))', "'Opp'[Name],[x]\na,Name\nb,Name"),
    "sumcols_filter_lit_value": ('TOCSV(SUMMARIZECOLUMNS(Opp[Name], TREATAS({1}, Opp[Rank]), "x", "Name"))',
                                 "'Opp'[Name],[x]\na,Name"),
    "medianx_empty_text": ("MEDIANX(FILTER(Opp, Opp[Rank] > 5), Opp[Name])", None),
    "medianx_tbl_err": ('MEDIANX(ERROR("tbl"), Opp[Name])', (ERR, "DAX ERROR(): tbl")),
}
MEASURES = {"clash_m": "IF(MAX(Opp[Name]) = 1, 1, 0)", "clash_lit": '"a" = 1', "clash_in": '1 IN {1, "a"}'}


@pytest.fixture(scope="module")
def alias(tmp_path_factory):
    b = PBIXBuilder("m154")
    cols = [("Rank", "Int64"), ("Name", "String"), ("Flag", "Boolean"), ("When", "DateTime"),
            ("Probability", "Double")]
    rows = [(1, "a", True, "2026-01-05T00:00:00", 0.1), (2, "b", False, "2026-01-06T00:00:00", 0.2),
            (3, "a", True, "2026-01-07T00:00:00", 0.3)]
    b.add_table("Opp", [{"name": c, "data_type": t} for c, t in cols],
                rows=[{c: v for (c, _), v in zip(cols, r)} for r in rows])
    b.add_table("_M", [{"name": "Column1", "data_type": "String"}], rows=[])
    b.add_measure("_M", "Total", "6")
    for k, dax in MEASURES.items():
        b.add_measure("_M", k, dax)
    for k, (dax, _want) in CASES.items():
        b.add_measure("_M", k, dax)
    b.add_page("Page 1")
    path = tmp_path_factory.mktemp("m154") / "m154.pbix"
    with pytest.warns(UserWarning):          # "_M has no rows", as intended
        b.save(str(path))
    assert json.loads(S.pbix_open(str(path), "m154"))["success"]
    yield "m154"
    S.pbix_close("m154")


def test_every_case_is_asked():
    assert len(CASES) == 17


@pytest.mark.parametrize("name", list(CASES))
def test_answers_as_before_the_merge(alias, name):
    want = CASES[name][1]
    r = json.loads(S.pbix_evaluate_dax(alias, name, apply_default_filters=False))["results"][0]
    if isinstance(want, tuple) and want[0] is ERR:
        assert r.get("status") == "error", r
        assert want[1] in r["error_message"], r["error_message"]
    else:
        assert r.get("status") != "error", r
        assert r.get("value") == want, r
