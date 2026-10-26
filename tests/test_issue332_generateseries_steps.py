"""#332: GENERATESERIES accumulates a Double step with no
tolerance, builds a Currency series in Currency, steps dates and times as
date-times, and refuses a step of zero or less, and a BLANK step, with errors
IFERROR cannot catch.

Power BI Desktop 2.152 (build_b257c.py g_blank_step, e_gs_iferror) fails
IFERROR(COUNTROWS(GENERATESERIES(1, 3, BLANK())), -1) with (in Norwegian)
"The arguments in the GenerateSeries function cannot be blank." -- our
English wording of it -- and IFERROR(COUNTROWS(GENERATESERIES(5, 1, -1)), -1)
with the step error; we answered 3 (a BLANK step was a step of 1) and -1.
A BLANK start or end is the same error (build_b257d.py g_blank_start,
g_blank_end; we read it as 0), ISERROR does not catch the step error
(e_gs_iserror), and a call in a branch never taken does not fail
(e_gs_if_false is 7).

GENERATESERIES compared each value with end + 1e-9, which added a row
whenever the accumulated step landed just past the end (0, 0.3, 0.1 gave 4
rows); it called float() on its arguments, so a Currency series came out
Double and a date or time argument raised inside, giving no rows; and a
negative step built a descending series, a zero step no rows. Power BI
Desktop adds the step in Double arithmetic and stops past the end, keeps a
Currency series Currency (the type follows the start and the step), steps
dates and times, and raises an error for a step of 0 or less. Its text,
"The GenerateSeries function cannot have an IncrementValue that is less
than or equal to zero.", is our wording: Desktop's Norwegian
"GenerateSeries-funksjonen kan ikke ha en IncrementValue storre enn eller
lik null." reads, word for word, "greater than or equal to zero" -- the
opposite of what Desktop does -- and its English original is not measured.

Expected values: Power BI Desktop 2.152 over ADOMD, by probe id --
F:/_desktop_oracle/build_b257b.py group I (F:/_tmp_pbix/open2/b252-order53),
build_b257c.py groups G / E and build_b257d.py groups G / E
(F:/_tmp_pbix/rel148/desk/desk_b257c.json, desk_b257d.json),
build_b254b.py e_gs (F:/_tmp_pbix/open2/b248-currency; cells: row counts of
(0, 0.7, 0.1) I8, (1, 1.3, 0.1) I3, (0, 1.7, 0.1) I17, (0.2, 0.5, 0.1) I4,
(MAX(GENERATESERIES(0, 1, 0.1)) - 1) * 1E17 = D-11.1, i.e. the Double
0.9999999999999999, (CURRENCY(0.1), 0.3, CURRENCY(0.1)) I3 rows and C0.3 at
most, MAXX(GENERATESERIES(1, 2, CURRENCY(0.5))) C2, MAXX(GENERATESERIES(1,
2, 0.5)) D2) and build_b254.py l_ctors (GENERATESERIES(0.1, 0.3, 0.1) has 2
rows). Types: C = Currency (_Currency), D = Double (float), I = Int64.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de
from pbix_mcp.errors import DAXEvaluationError

pytestmark = pytest.mark.unit

TABLES = {"T": {"columns": ["x"], "rows": [[1]]}}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


def _error(expr):
    """The engine's error for the measure (eval_errors), None when it answered."""
    eng = de.DAXEngine()
    eng.evaluate_measure("m", de.DAXContext(TABLES, {"m": expr}))
    return eng.eval_errors.get("m")


@pytest.mark.parametrize("probe,expr,want", [
    # build_b257b.py group I
    ('g_float_cnt', 'COUNTROWS(GENERATESERIES(0, 0.3, 0.1))', 3),
    ('g_float_eq', 'COUNTROWS(FILTER(GENERATESERIES(0, 1, 0.1), [Value] = 0.3)) + 0', 0),
    ('g_float_max_is1', 'IF(MAXX(GENERATESERIES(0, 1, 0.1), [Value]) = 1, 1, 0)', 0),
    ('g_end_lt', 'COUNTROWS(GENERATESERIES(5, 1)) + 0', 0),
    ('g_dates', 'COUNTROWS(GENERATESERIES(DATE(2024, 1, 1), DATE(2024, 1, 3), 1))', 3),
    ('g_dates_text', 'MAXX(GENERATESERIES(DATE(2024, 1, 1), DATE(2024, 1, 3), 1), [Value]) & ""', '1/3/2024'),
    ('g_time', 'COUNTROWS(GENERATESERIES(TIME(0, 0, 0), TIME(1, 0, 0), TIME(0, 15, 0)))', 5),
    # build_b254b.py e_gs, build_b254.py l_ctors
    ('e_gs_0', 'COUNTROWS(GENERATESERIES(0, 0.7, 0.1))', 8),
    ('e_gs_1', 'COUNTROWS(GENERATESERIES(1, 1.3, 0.1))', 3),
    ('e_gs_2', 'COUNTROWS(GENERATESERIES(0, 1.7, 0.1))', 17),
    ('e_gs_3', 'COUNTROWS(GENERATESERIES(0.2, 0.5, 0.1))', 4),
    ('e_gs_5', 'COUNTROWS(GENERATESERIES(CURRENCY(0.1), 0.3, CURRENCY(0.1)))', 3),
    ('l_ctors_4', 'COUNTROWS(GENERATESERIES(0.1, 0.3, 0.1))', 2),
])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


@pytest.mark.parametrize("probe,expr,want,kind", [
    ('e_gs_4', 'MAXX(GENERATESERIES(0, 1, 0.1), [Value])', 0.9999999999999999, float),
    ('e_gs_6', 'MAXX(GENERATESERIES(CURRENCY(0.1), 0.3, CURRENCY(0.1)), [Value])', 0.3, de._Currency),
    ('e_gs_7', 'MAXX(GENERATESERIES(1, 2, CURRENCY(0.5)), [Value])', 2, de._Currency),
    ('e_gs_8', 'MAXX(GENERATESERIES(1, 2, 0.5), [Value])', 2, float),
])
def test_values_and_types_match_desktop(probe, expr, want, kind):
    got = _ev(expr)
    assert got == want and type(got) is kind


@pytest.mark.parametrize("probe,expr", [
    ('g_neg_step', 'COUNTROWS(GENERATESERIES(5, 1, -1))'),
    ('g_zero_step', 'COUNTROWS(GENERATESERIES(1, 5, 0))'),
])
def test_a_step_of_zero_or_less_is_an_error(probe, expr):
    assert ("The GenerateSeries function cannot have an IncrementValue that is less than or equal to "
            "zero.") in (_error(expr) or "")
    with pytest.raises(DAXEvaluationError, match="IncrementValue"):
        de.DAXEngine()._eval_expr(expr, de.DAXContext(TABLES, {}))


# build_b257c.py group G (F:/_tmp_pbix/rel148/desk/desk_b257c.json)
def _typ(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISCURRENCY({x}), "C", IF(ISDOUBLE({x}), "D", '
            f'IF(ISINT64({x}), "I", "?"))))')


@pytest.mark.parametrize("probe,expr,want", [
    ('g_end_dbl_cnt', 'COUNTROWS(GENERATESERIES(1, 5.5, 1))', 5),
    ('g_end_dbl_type', _typ('MAXX(GENERATESERIES(1, 5.5, 1), [Value])'), 'I'),
    ('g_cur_end_below', 'COUNTROWS(GENERATESERIES(CURRENCY(0.1), 0.29999, CURRENCY(0.1)))', 2),
    ('g_cur_dbl_step', _typ('MAXX(GENERATESERIES(CURRENCY(1), 2, 0.5), [Value])'), 'D'),
    ('g_num_time_step', 'COUNTROWS(GENERATESERIES(0, TIME(1, 0, 0), TIME(0, 15, 0)))', 5),
])
def test_b257c_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


@pytest.mark.parametrize("probe,expr,msg", [
    # Desktop: "Argumentene i GenerateSeries-funksjonen kan ikke vaere tomme."
    ('g_blank_step', 'IFERROR(COUNTROWS(GENERATESERIES(1, 3, BLANK())), -1)',
     'The arguments in the GenerateSeries function cannot be blank.'),
    ('e_gs_iferror', 'IFERROR(COUNTROWS(GENERATESERIES(5, 1, -1)), -1)',
     'The GenerateSeries function cannot have an IncrementValue that is less than or equal to zero.'),
])
def test_errors_escape_iferror(probe, expr, msg):
    """Desktop fails both queries with GENERATESERIES' error, IFERROR
    around it or not; we answered 3 and -1."""
    assert (_error(expr) or "").endswith(msg)
    assert _ev(expr) is None


def test_a_blank_step_is_an_error_and_a_left_out_step_is_1():
    """g_blank_step without the IFERROR is the same error; leaving the step
    out is a step of 1 (build_b257b.py g_end_lt: GENERATESERIES(5, 1) is no
    rows; build_b257c.py g_end_dbl_cnt)."""
    assert (_error('COUNTROWS(GENERATESERIES(1, 3, BLANK()))') or '').endswith(
        'The arguments in the GenerateSeries function cannot be blank.')
    assert _ev('COUNTROWS(GENERATESERIES(1, 3))') == 3


# build_b257d.py group G / E (F:/_tmp_pbix/rel148/desk/desk_b257d.json)
@pytest.mark.parametrize("probe,expr", [
    # Desktop: "Argumentene i GenerateSeries-funksjonen kan ikke vaere tomme."
    # for both; we read the BLANK as 0 and answered 4 and 0
    ('g_blank_start', 'COUNTROWS(GENERATESERIES(BLANK(), 3))'),
    ('g_blank_end', 'COUNTROWS(GENERATESERIES(1, BLANK())) + 0'),
])
def test_b257d_a_blank_start_or_end_is_the_blank_error(probe, expr):
    assert (_error(expr) or "").endswith('The arguments in the GenerateSeries function cannot be blank.')
    assert _ev(expr) is None


def test_b257d_the_errors_are_the_running_calls():
    """The step error escapes ISERROR as it escapes IFERROR (e_gs_iserror
    fails the query), but a call in a branch never taken is not run:
    IF(FALSE(), COUNTROWS(GENERATESERIES(5, 1, -1)), 7) is 7 (e_gs_if_false)."""
    assert (_error('IF(ISERROR(COUNTROWS(GENERATESERIES(5, 1, -1))), 1, 0)') or "").endswith(
        'The GenerateSeries function cannot have an IncrementValue that is less than or equal to zero.')
    assert _ev('IF(FALSE(), COUNTROWS(GENERATESERIES(5, 1, -1)), 7)') == 7
