"""GENERATE returns both tables' columns under their own names, and a bare [Column] reads a row whose single column
is carried as __column__/__value__ (CALENDAR, VALUES) in any case -- as the calculated date tables Desktop users
write (GENERATE(CALENDAR(...), VAR ... RETURN ROW(...))) need."""
from pbix_mcp.dax.calc_tables import evaluate_calc_table_expression as calc


def day(v) -> str:
    """A date cell as YYYY-MM-DD, whether the engine holds it as text or as a datetime."""
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)[:10]


def test_generate_keeps_both_tables_column_names():
    got, err = calc('GENERATE(CALENDAR(DATE(2024,1,1), DATE(2024,1,2)), ROW("Y", YEAR([Date])))', {})
    assert err is None and got["columns"] == ["Date", "Y"]
    assert [[day(r[0]), r[1]] for r in got["rows"]] == [["2024-01-01", 2024], ["2024-01-02", 2024]]


def test_a_calendar_row_reads_its_date_in_any_case():
    got, err = calc('ADDCOLUMNS(CALENDAR(DATE(2024,1,1), DATE(2024,1,1)), "M", MONTH([DATE]), "D", DAY([date]))', {})
    assert err is None and [[day(r[0]), *r[1:]] for r in got["rows"]] == [["2024-01-01", 1, 1]]


def test_variables_over_the_outer_row():
    expr = ('VAR S = DATE(2024,1,1) RETURN GENERATE(CALENDAR(S, DATE(2024,1,3)), '
            'VAR q = DATEDIFF(S, [DATE], DAY) + 1 RETURN ROW("Seq", q, "Wk", WEEKDAY([Date])))')
    got, err = calc(expr, {})
    assert err is None and got["columns"] == ["Date", "Seq", "Wk"]
    assert [r[1] for r in got["rows"]] == [1, 2, 3] and [r[2] for r in got["rows"]] == [2, 3, 4]


def test_a_table_row_still_resolves_by_its_table():
    tables = {"T": {"columns": ["Region", "Amount"], "rows": [["E", 1], ["W", 2]]}}
    got, err = calc('GENERATE(T, ROW("Twice", [Amount] * 2))', tables)
    assert err is None and got["columns"] == ["Region", "Amount", "Twice"]
    assert got["rows"] == [["E", 1, 2], ["W", 2, 4]]


T = {"T": {"columns": ["Region", "Amount"], "rows": [["E", 1], ["W", 2]]},
     "U": {"columns": ["Region", "X"], "rows": [["E", 10]]}}


def test_expressions_over_a_generate_still_read_its_first_table():
    assert calc('ROW("s", SUMX(GENERATE(T, ROW("A", 1)), T[Amount]))', T)[0]["rows"] == [[3]]
    assert calc('SELECTCOLUMNS(GENERATE(T, ROW("A", 1)), "R", T[Region])', T)[0]["rows"] == [["E"], ["W"]]
    got, err = calc('FILTER(GENERATE(T, ROW("A", [Amount] * 2)), T[Amount] > 1)', T)
    assert err is None and got["rows"] == [["W", 2, 4]]


def test_a_column_both_sides_have_is_refused():
    got, err = calc("GENERATE(T, U)", T)
    assert got is None and "both tables" in err
    got, err = calc('GENERATE(T, SELECTCOLUMNS(U, "region", U[X]))', T)
    assert got is None and "both tables" in err


def test_generateall_keeps_a_row_without_matches():
    # the inner table's columns are BLANK on such a row (#300); U's Region would clash with T's
    got, err = calc('GENERATEALL(T, FILTER(SELECTCOLUMNS(U, "X", U[X]), [X] > T[Amount] * 9))', T)
    assert err is None and got == {"columns": ["Region", "Amount", "X"],
                                   "rows": [["E", 1, 10], ["W", 2, None]]}


def test_a_measure_wins_over_a_row_column_whatever_the_case():
    tables = {"T": {"columns": ["total"], "rows": [[1]]}}
    got, err = calc('ADDCOLUMNS(T, "Z", [TOTAL])', tables, {"Total": "100"})
    assert err is None and got["rows"] == [[1, 100]]
