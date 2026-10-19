"""GENERATE returns both tables' columns under their own names, and a bare [Column] reads a row whose single column
is carried as __column__/__value__ (CALENDAR, VALUES) in any case -- as the calculated date tables Desktop users
write (GENERATE(CALENDAR(...), VAR ... RETURN ROW(...))) need."""
from pbix_mcp.dax.calc_tables import evaluate_calc_table_expression as calc


def test_generate_keeps_both_tables_column_names():
    got, err = calc('GENERATE(CALENDAR(DATE(2024,1,1), DATE(2024,1,2)), ROW("Y", YEAR([Date])))', {})
    assert err is None and got == {"columns": ["Date", "Y"], "rows": [["2024-01-01", 2024], ["2024-01-02", 2024]]}


def test_a_calendar_row_reads_its_date_in_any_case():
    got, err = calc('ADDCOLUMNS(CALENDAR(DATE(2024,1,1), DATE(2024,1,1)), "M", MONTH([DATE]), "D", DAY([date]))', {})
    assert err is None and got["rows"] == [["2024-01-01", 1, 1]]


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
