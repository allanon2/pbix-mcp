"""#298: RELATED over the rows of a table of COLUMNS is Desktop's error.

What was wrong: RELATED in an iteration over VALUES(F[Order]) or SUMMARIZE(F, F[Order]) looked
the related row up from the column the row carried: SUMX(VALUES(F[Order]), RELATED(Cal[Bit])) was
BLANK and SUMX(SUMMARIZE(F, F[Order]), RELATED(Cal[Bit])) 127. A row of a table of columns has
its columns' lineage but no expanded table, so it reaches no related table.

Desktop refuses both with "The column 'Cal[Bit]' either doesn't exist or doesn't have a
relationship to any table available in the current context." (translated from Desktop's
Norwegian UI), before evaluating the measure, as it does RELATED with no row context
(#284); the engine checks a measure's expression first (DAXEngine._static_refusal)
and refuses such a row when RELATED is evaluated (_fn_related).

Where the expected values come from: Power BI Desktop 2.152 over ADOMD, batteries B256c
(build_b256c.py r_*) and B256d (build_b256d.py r_summarize_cal, r_values_cal: a table of the
RELATED table's own columns), over the model of tests/test_issue250_userelationship_date_part.py.
Controls: ADDCOLUMNS over F keeps F's rows (r_addcolumns 172) and RELATED in FILTER's condition
over F counts five rows (r_filter_related 5).

RELATEDTABLE's rows are whole rows of their table, though they also name its first column
(``__column__``): RELATED over them reads the related row, as it does over the table itself. The
first draft of this fix refused them too, with an error IFERROR could not catch. Their values are
exact arithmetic: every F row's pk is in P, so SUMX(P, SUMX(RELATEDTABLE(F), e)) is SUMX(F, e)
(SUMX(F, RELATED(Cal[Bit])) is Desktop's d_related_plain 172), and under SUMX(Cal, ..) only the F
rows whose order day is a Cal row take part.
"""
import pytest

from pbix_mcp.dax import engine as de
from tests.test_issue250_userelationship_date_part import MEASURES, RELS, TABLES

pytestmark = pytest.mark.unit

MSG = ("The column 'Cal[Bit]' either doesn't exist or doesn't have a relationship to any table "
       "available in the current context.")
ERRORS = {
    "r_values_order": "SUMX(VALUES(F[Order]), RELATED(Cal[Bit]))",
    "r_summarize_order": "SUMX(SUMMARIZE(F, F[Order]), RELATED(Cal[Bit]))",
    # battery B256d (build_b256d.py): a table of the RELATED table's own columns is refused too
    # (0.9.147: BLANK for both)
    "r_summarize_cal": "SUMX(SUMMARIZE(F, Cal[Date]), RELATED(Cal[Bit]))",
    "r_values_cal": "SUMX(VALUES(Cal[Date]), RELATED(Cal[Bit]))",
}
VALUES = {
    "r_addcolumns": ('SUMX(ADDCOLUMNS(F, "b", RELATED(Cal[Bit])), [b])', 172),
    "r_filter_related": ("COUNTROWS(FILTER(F, RELATED(Cal[Bit]) >= 16))", 5),
}
# RELATEDTABLE's rows are rows of their table; each is pinned against its SUMX(F, ..) form
RELATEDTABLE_ROWS = {
    "rt_sum_bit": ("SUMX(P, SUMX(RELATEDTABLE(F), RELATED(Cal[Bit])))", 172),
    "rt_v_times_x": ("SUMX(P, SUMX(RELATEDTABLE(F), F[v] * RELATED(P[x])))", 30425),
    "rt_from_cal": ("SUMX(Cal, SUMX(RELATEDTABLE(F), RELATED(P[x])))", 22),
    "rt_filtered": ("SUMX(P, SUMX(FILTER(RELATEDTABLE(F), F[v] > 100), RELATED(Cal[Bit])))", 144),
    "rt_count_year": ("SUMX(P, COUNTROWS(FILTER(RELATEDTABLE(F), RELATED(Cal[Year]) = 2024)))", 9),
    "rt_maxx": ("MAXX(P, MAXX(RELATEDTABLE(F), RELATED(Cal[Bit])))", 64),
    "rt_iferror": ("IFERROR(SUMX(P, SUMX(RELATEDTABLE(F), RELATED(Cal[Bit]))), -1)", 172),
}
FLAT_FORMS = {
    "rt_sum_bit": "SUMX(F, RELATED(Cal[Bit]))",
    "rt_v_times_x": "SUMX(F, F[v] * RELATED(P[x]))",
    "rt_from_cal": "SUMX(FILTER(F, NOT ISBLANK(RELATED(Cal[Date]))), RELATED(P[x]))",
    "rt_filtered": "SUMX(FILTER(F, F[v] > 100), RELATED(Cal[Bit]))",
    "rt_count_year": "COUNTROWS(FILTER(F, RELATED(Cal[Year]) = 2024))",
    "rt_maxx": "MAXX(F, RELATED(Cal[Bit]))",
}
ALL_MEASURES = {**MEASURES, **ERRORS, **{k: e for k, (e, _w) in VALUES.items()},
                **{k: e for k, (e, _w) in RELATEDTABLE_ROWS.items()},
                **{f"{k}_flat": e for k, e in FLAT_FORMS.items()}}


@pytest.fixture(params=["fast", "slow"])
def path(request, monkeypatch):
    if request.param == "slow":
        monkeypatch.setattr(de, "_FAST_PROPAGATION", False)
    de._SHARED_FILTER_CACHES.clear()
    yield request.param
    de._SHARED_FILTER_CACHES.clear()


@pytest.mark.parametrize("name", list(ERRORS))
def test_the_measure_is_refused(name, path):
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, dict(ALL_MEASURES), None, None, {}, RELS)
    assert eng.evaluate_measure(name, ctx) is None
    assert eng.eval_errors.get(name, "").endswith(MSG), eng.eval_errors.get(name)


@pytest.mark.parametrize("name", list(ERRORS))
def test_related_refuses_the_row(name, path):
    # evaluated without the measure's check: RELATED itself refuses such a row
    ctx = de.DAXContext(TABLES, dict(ALL_MEASURES), None, None, {}, RELS)
    with pytest.raises(de._DAXEvaluationError) as err:
        de.DAXEngine()._eval_expr(ERRORS[name], ctx)
    assert err.value.message == MSG


@pytest.mark.parametrize("name", list(VALUES))
def test_rows_of_the_table(name, path):
    got = de.evaluate_measure(name, TABLES, ALL_MEASURES, {}, relationships=RELS)
    assert got == VALUES[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_in_one_batch(order, path):
    names = list(ERRORS) + list(VALUES)
    if order == "reverse":
        names.reverse()
    got = de.evaluate_measures_batch(names, TABLES, dict(ALL_MEASURES), {}, relationships=RELS)
    assert got == {**{n: None for n in ERRORS}, **{n: w for n, (_e, w) in VALUES.items()}}


@pytest.mark.parametrize("name", list(RELATEDTABLE_ROWS))
def test_relatedtable_rows_are_rows_of_the_table(name, path):
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, dict(ALL_MEASURES), None, None, {}, RELS)
    got = eng.evaluate_measure(name, ctx)
    assert name not in eng.eval_errors, eng.eval_errors.get(name)
    assert got == RELATEDTABLE_ROWS[name][1], (name, got)


@pytest.mark.parametrize("name", list(FLAT_FORMS))
def test_relatedtable_rows_equal_the_flat_form(name, path):
    # the arithmetic behind the pinned values: the same sum over F itself
    got = de.evaluate_measure(f"{name}_flat", TABLES, ALL_MEASURES, {}, relationships=RELS)
    assert got == RELATEDTABLE_ROWS[name][1], (name, got)


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_relatedtable_rows_in_one_batch(order, path):
    names = list(ERRORS) + list(RELATEDTABLE_ROWS)
    if order == "reverse":
        names.reverse()
    got = de.evaluate_measures_batch(names, TABLES, dict(ALL_MEASURES), {}, relationships=RELS)
    assert got == {**{n: None for n in ERRORS}, **{n: w for n, (_e, w) in RELATEDTABLE_ROWS.items()}}
