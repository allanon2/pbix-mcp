"""The M evaluator (pbix_mcp.m.evaluate, library, load): language semantics from the
M specification, conversions from Microsoft's function reference, and this
package's own Enter-data M evaluated back to the rows it was built from."""
import datetime as dt
import math

import pytest

from pbix_mcp.builder import _build_enter_data_m
from pbix_mcp.m import LIBRARY_NAMES, CellError, MError, Record, Table, evaluate, force_table
from pbix_mcp.m.library import number_from_text
from pbix_mcp.m.load import load_rows, model_value


class TestLanguage:
    def test_arithmetic_and_text(self):
        assert evaluate("1 + 2 * 3") == 7
        assert evaluate('"a" & "b"') == "ab"
        assert evaluate("{1, 2} & {3}") == [1, 2, 3]
        assert evaluate("null + 1") is None
        assert evaluate("1 / 0") == float("inf")

    def test_let_is_lazy_and_unused_steps_never_run(self):
        assert evaluate('let a = error "never", b = 2 in b') == 2

    def test_a_step_that_refers_to_itself_is_an_error(self):
        with pytest.raises(MError, match="cyclic"):
            evaluate("let a = a + 1 in a")

    def test_record_fields_see_each_other(self):
        assert evaluate("[a = 1, b = a + 1][b]") == 2

    def test_logical_operators_with_null(self):
        assert evaluate("null and false") is False
        assert evaluate("null and true") is None
        assert evaluate("null or true") is True
        assert evaluate("false and (error \"skipped\")") is False

    def test_functions_and_each(self):
        assert evaluate("let f = (x, optional y) => x + (y ?? 10) in f(1) + f(1, 2)") == 14
        assert evaluate("let f = each _ * 2 in f(4)") == 8
        with pytest.raises(MError, match="arguments"):
            evaluate("((x) => x)()")

    def test_if_needs_a_logical(self):
        assert evaluate('if 1 = 1 then "y" else "n"') == "y"
        with pytest.raises(MError):
            evaluate("if 1 then 2 else 3")

    def test_try(self):
        r = evaluate('try error "boom"')
        assert r["HasError"] is True and r["Error"]["Message"] == "boom"
        assert evaluate('try error "boom" otherwise 5') == 5
        assert evaluate("try 1")["Value"] == 1

    def test_lists_and_ranges(self):
        assert evaluate("{1..3, 7}") == [1, 2, 3, 7]
        assert evaluate("{1, 2}{1}") == 2
        assert evaluate("{1}{5}?") is None
        with pytest.raises(MError, match="enough elements"):
            evaluate("{1}{5}")

    def test_metadata_does_not_change_the_value(self):
        assert evaluate('"C:\\x.json" meta [IsParameterQuery = true]') == "C:\\x.json"

    def test_as_and_is(self):
        assert evaluate("1 is number") is True
        assert evaluate("null is nullable text") is True
        with pytest.raises(MError):
            evaluate('"x" as number')

    def test_unknown_names_are_errors_and_resolve_finds_queries(self):
        with pytest.raises(MError, match="wasn't recognized"):
            evaluate("Web.Contents")
        assert evaluate("Other + 1", resolve=lambda name: {"Other": 41}[name]) == 42

    def test_section_documents(self):
        s = evaluate("section S; A = B + 1; shared B = 2;")
        assert isinstance(s, Record) and s["A"] == 3

    def test_tables_by_key_and_position(self):
        t = '#table({"Item", "Data"}, {{"a", 1}, {"b", 2}})'
        assert evaluate(t + '{[Item = "b"]}[Data]') == 2
        assert evaluate(t + "{0}[Item]") == "a"
        assert evaluate(t + "[Data]") == [1, 2]
        with pytest.raises(MError, match="didn't match"):
            evaluate(t + '{[Item = "z"]}')


class TestConversions:
    """Microsoft's function reference: Number.FromText's "15", "3,423.10", "5.0E-10"; Number.From("12.3%") = 0.123;
    Int64.From and Currency.From round half to even; Logical.FromText takes "true"/"false" in any case."""

    @pytest.mark.parametrize("text, value", [
        ("15", 15), ("3,423.10", 3423.1), ("5.0E-10", 5e-10), ("12.3%", 0.123), ("-7", -7), (" 42 ", 42),
        (".5", 0.5), ("$356,981", 356981), ("1,234,567", 1234567),
    ])
    def test_number_text(self, text, value):
        assert number_from_text(text, "en-US") == pytest.approx(value)

    @pytest.mark.parametrize("text", ["abc", "1.2.3", "$", "1 234", "0x10", "(-5)", "(5", "-$-5"])
    def test_not_numbers(self, text):
        with pytest.raises(MError) as e:
            number_from_text(text, "en-US")
        assert e.value.reason == "DataFormat.Error"

    def transform(self, cells, type_, culture='"en-US"'):
        m = (f'Table.TransformColumnTypes(#table({{"c"}}, {{{", ".join("{" + c + "}" for c in cells)}}}), '
             f'{{{{"c", {type_}}}}}, {culture})')
        return [row[0] for row in evaluate(m).rows]

    def test_int64_rounds_half_to_even(self):
        assert self.transform(['"2.5"', '"3.5"', '"-2.5"', '"7"'], "Int64.Type") == [2, 4, -2, 7]

    def test_currency_keeps_four_places_half_to_even(self):
        assert self.transform(['"1.23455"', '"1.23465"', '"$1,000"'], "Currency.Type") == [1.2346, 1.2346, 1000]

    def test_percentage(self):
        assert self.transform(['"10%"', '"0.25"'], "Percentage.Type") == pytest.approx([0.1, 0.25])

    def test_datetime_iso_and_en_us(self):
        assert self.transform(['"2026-09-01 00:00:00"', '"11/20/2015 10:59"', '"1/2/2024 3:04:05 PM"'],
                              "type datetime") == [dt.datetime(2026, 9, 1), dt.datetime(2015, 11, 20, 10, 59),
                                                   dt.datetime(2024, 1, 2, 15, 4, 5)]

    def test_logical(self):
        assert self.transform(['"TRUE"', '"false"'], "type logical") == [True, False]

    def test_a_failed_cell_keeps_its_row(self):
        t = evaluate('Table.TransformColumnTypes(#table({"c"}, {{"1"}, {"x"}, {null}}), {{"c", Int64.Type}})')
        assert len(t.rows) == 3 and isinstance(t.rows[1][0], CellError)
        assert force_table(t, errors_as_null=True) == [[1], [None], [None]]
        with pytest.raises(MError) as e:
            force_table(t, errors_as_null=False)
        assert e.value.reason == "DataFormat.Error"

    def test_another_culture_is_an_error_not_a_guess(self):
        with pytest.raises(MError, match="de-DE"):
            self.transform(['"1,5"'], "type number", '"de-DE"')

    def test_a_missing_column_is_an_error(self):
        with pytest.raises(MError, match="column 'x'"):
            evaluate('Table.TransformColumnTypes(#table({"c"}, {}), {{"x", type text}})')

    def test_an_unsupported_number_type_is_an_error(self):
        with pytest.raises(MError, match="isn't supported"):
            evaluate('Table.TransformColumnTypes(#table({"c"}, {}), {{"c", type duration}})')


class TestTables:
    def test_from_rows_columns_forms(self):
        assert evaluate('Table.FromRows({{1, 2}})').columns == ["Column1", "Column2"]
        assert evaluate('Table.FromRows({{1, 2}}, {"a", "b"})').columns == ["a", "b"]
        assert evaluate('Table.FromRows({{1, 2}}, type table [a = number, b = text])').columns == ["a", "b"]
        with pytest.raises(MError, match="row 0"):
            evaluate('Table.FromRows({{1}}, {"a", "b"})')

    def test_combine_unions_columns_with_nulls(self):
        t = evaluate('Table.Combine({#table({"a", "b"}, {{1, 2}}), #table({"b", "c"}, {{3, 4}})})')
        assert t.columns == ["a", "b", "c"] and t.rows == [[1, 2, None], [None, 3, 4]]

    def test_library_by_tier(self):
        from pbix_mcp.m import TIERS
        tier = lambda n: {k for k, v in TIERS.items() if v == n}  # noqa: E731
        assert tier(1) == {
            "Binary.FromText", "BinaryEncoding.Base64", "BinaryEncoding.Hex", "Binary.Decompress", "Compression.GZip",
            "Compression.Deflate", "Json.Document", "Table.FromRows", "#table", "Table.TransformColumnTypes",
            "Table.Combine", "Int64.Type", "Currency.Type", "Percentage.Type", "#infinity", "#nan", "#date", "#datetime",
            "#datetimezone", "#time", "#duration"}
        assert {"Table.RenameColumns", "Table.RemoveColumns", "Table.SelectColumns", "Table.AddColumn",
                "Table.PromoteHeaders", "Text.BeforeDelimiter"} <= tier(2)
        assert {"File.Contents", "Csv.Document", "Excel.Workbook", "Parquet.Document"} <= tier(3)
        assert LIBRARY_NAMES == set(TIERS)
        # The enumerations' values, from Microsoft's reference pages.
        assert [evaluate(x) for x in ("BinaryEncoding.Base64", "BinaryEncoding.Hex", "Compression.GZip",
                                      "Compression.Deflate")] == [0, 1, 0, 1]


class TestEnterData:
    """The builder writes Desktop's Enter-data M; evaluating it gives back the typed rows it was built from."""

    COLUMNS = [{"name": "ID", "data_type": "Int64"}, {"name": "Name", "data_type": "String"},
               {"name": "Amount", "data_type": "Double"}, {"name": "Price", "data_type": "Decimal"},
               {"name": "When", "data_type": "DateTime"}, {"name": "Flag", "data_type": "Boolean"},
               {"name": 'Quote "x"', "data_type": "String"}]
    ROWS = [{"ID": 1, "Name": "a", "Amount": 1.5, "Price": 2.25, "When": "2024-01-02 03:04:05", "Flag": True,
             'Quote "x"': 'say "hi"'},
            {"ID": 2, "Name": None, "Amount": -3.0, "Price": 1000, "When": None, "Flag": False, 'Quote "x"': ""}]

    def test_round_trip(self):
        t = evaluate(_build_enter_data_m(self.COLUMNS, self.ROWS))
        assert isinstance(t, Table) and t.columns == [c["name"] for c in self.COLUMNS]
        assert [ty.facet or ty.kind for ty in t.types] == ["Int64.Type", "text", "number", "Currency.Type",
                                                            "datetime", "logical", "text"]
        assert force_table(t, errors_as_null=False) == [
            [1, "a", 1.5, 2.25, dt.datetime(2024, 1, 2, 3, 4, 5), True, 'say "hi"'],
            [2, None, -3, 1000, None, False, ""]]


class TestLoad:
    def test_a_number_into_a_datetime_column_is_an_ole_date(self):
        # Microsoft's Revenue Opportunities sample stores Int64 41679 in a dateTime column as 2014-02-09.
        assert model_value(41679, "dateTime") == dt.datetime(2014, 2, 9)
        assert model_value(dt.date(2024, 1, 2), "dateTime") == dt.datetime(2024, 1, 2)

    def test_other_pairings_are_errors(self):
        with pytest.raises(MError, match="can't be loaded"):
            model_value("x", "int64")
        for v, ty in ((1e300, "int64"), (5, "string"), (dt.date(2015, 11, 20), "string"), ("2015-11-20", "dateTime"),
                      ("1", "decimal"), (True, "double")):
            with pytest.raises(MError):
                model_value(v, ty)

    def test_load_rows_maps_columns_and_errors(self):
        t = evaluate('Table.TransformColumnTypes(#table({"a", "b"}, {{"1", "x"}, {"y", "z"}}), {{"a", Int64.Type}})')
        assert load_rows(t, [("b", "string"), ("a", "double")], errors_as_null=True) == [["x", 1.0], ["z", None]]


class TestReviewFindings:
    """Cases from an independent review against the specification and the function reference."""

    def test_lists_are_lazy(self):
        # The specification: { error "a", 1, error "c"}{1} is 1.
        assert evaluate('{ error "a", 1, error "c"}{1}') == 1
        with pytest.raises(MError, match="a"):
            evaluate('{ error "a", 1}{0}')
        assert evaluate('#table({"a"}, {{error "x"}, {1}}){1}[a]') == 1
        t = evaluate('#table({"a"}, {{error "x"}, {1}})')
        assert force_table(t, errors_as_null=True) == [[None], [1]]

    def test_projections(self):
        r = evaluate("[A = 1, B = 2][[B], [C]]?")
        assert list(r.fields) == ["B", "C"] and r["C"] is None
        assert evaluate('[A = error "x", B = 1][[B]][B]') == 1
        with pytest.raises(MError, match="'C'"):
            evaluate("[A = 1][[C]]")

    def test_parameter_and_return_types_are_checked(self):
        with pytest.raises(MError, match="parameter x"):
            evaluate('((x as number) => x)("a")')
        with pytest.raises(MError, match="returns number"):
            evaluate('(() as number => "a")()')
        assert evaluate("((optional x as number) => x)()") is None
        assert evaluate("((x as nullable number) => x)(null)") is None

    def test_table_equality_ignores_column_order(self):
        assert evaluate('#table({"A","B"},{{1,2}}) = #table({"B","A"},{{2,1}})') is True
        assert evaluate('#table({"A","B"},{{1,2}}) = #table({"A","B"},{{2,1}})') is False

    def test_error_records(self):
        e = evaluate('try error [Message = "m"]')["Error"]
        assert e["Reason"] is None and e["Message"] == "m" and e["ErrorCode"] is None
        e = evaluate('try error [Reason = "R", Message.Format = "v #{0} #{1}", Message.Parameters = {"z", 2}]')["Error"]
        assert e["Reason"] == "R" and e["Message"] == "v z 2"
        assert evaluate('try error "x" catch (e) => e[Reason]') == "Expression.Error"
        assert evaluate('try error "x" catch () => 3') == 3

    def test_ieee_division_and_nan(self):
        assert evaluate("1 / -0.0") == float("-inf")
        assert evaluate("-1 / 0") == float("-inf")
        assert math.isnan(evaluate("(0/0) / 0"))
        assert evaluate("#nan = #nan") is False

    def test_null_with_operators_of_the_wrong_kind(self):
        assert evaluate('"a" & null') is None
        with pytest.raises(MError):
            evaluate("{1} & null")
        with pytest.raises(MError):
            evaluate("null < {1}")
        assert evaluate("null < 1") is None

    def test_key_lookups(self):
        t = '#table({"A", "B"}, {{1, 1}, {2, 1}})'
        assert evaluate(t + "{[A = 3]}?") is None
        with pytest.raises(MError, match="more than one"):
            evaluate(t + "{[B = 1]}?")
        with pytest.raises(MError, match="column 'Z'"):
            evaluate(t + "{[Z = 1]}?")

    def test_combine_with_a_table_type_and_without_a_count(self):
        # The reference's Example 3: the type projects the result.
        t = evaluate('Table.Combine({#table({"a", "b"}, {{"x", 1}})}, type table [a = text])')
        assert t.columns == ["a"] and t.rows == [["x"]] and t.types[0].kind == "text"
        with pytest.raises(MError):
            evaluate('Table.Combine({#table({"a"}, {{1}})}, 1)')

    def test_transform_single_pair(self):
        # The reference's Example 1 passes one {column, type} pair.
        t = evaluate('Table.TransformColumnTypes(#table({"a"}, {{"1"}}), {"a", type number})')
        assert t.rows == [[1]]

    def test_no_python_exception_escapes(self):
        for m in ('Table.TransformColumnTypes(#table({"c"}, {{1/0}, {0/0}}), {{"c", Currency.Type}})',
                  'Table.TransformColumnTypes(#table({"c"}, {{1e10}}), {{"c", type datetime}})',
                  'Table.TransformColumnTypes(#table({"c"}, {{"1e400"}}), {{"c", Int64.Type}})'):
            t = evaluate(m)
            assert all(isinstance(r[0], CellError) for r in t.rows), m
        with pytest.raises(MError, match="nested too deeply"):
            evaluate("let f = (n) => @f(n) in f(1)")
        for v, ty in ((1e10, "dateTime"), (1e30, "decimal"), (float("nan"), "decimal")):
            with pytest.raises(MError):
                model_value(v, ty)

    def test_binary_and_json_inputs(self):
        assert evaluate('Binary.FromText("QUJD")') == b"ABC"
        assert evaluate('Binary.FromText("QU JD")') == b"ABC"
        assert evaluate('Binary.FromText("414243", BinaryEncoding.Hex)') == b"ABC"
        for m in ('Binary.FromText("QU!!JD")', 'Binary.FromText("QUJD", false)', 'Json.Document("NaN")',
                  'Binary.Decompress(Binary.FromText("QUJD"), true)'):
            with pytest.raises(MError):
                evaluate(m)
        r = evaluate('Json.Document("{""a"": [1, 2.5, true, null, ""x""]}")')
        assert r["a"] == [1, 2.5, True, None, "x"]

    def test_int64_and_currency_take_no_logical(self):
        t = evaluate('Table.TransformColumnTypes(#table({"c"}, {{true}}), {{"c", Int64.Type}})')
        assert isinstance(t.rows[0][0], CellError)

    def test_ole_dates(self):
        assert model_value(-1.25, "dateTime") == dt.datetime(1899, 12, 29, 6)
        assert model_value(0.5, "dateTime") == dt.datetime(1899, 12, 30, 12)
        from pbix_mcp.m.library import datetime_to_ole
        for d in (dt.datetime(1899, 12, 29, 6), dt.datetime(2014, 2, 9, 18)):
            assert model_value(datetime_to_ole(d), "dateTime") == d

    def test_text_from_refuses_what_no_source_shows(self):
        t = evaluate('Table.TransformColumnTypes(#table({"c"}, {{3}, {#infinity}, {1.2345678901234567e19}, '
                     '{#datetime(2015, 11, 20, 10, 59, 0.5)}, {#time(1, 2, 3)}}), {{"c", type text}})')
        assert t.rows[0][0] == "3" and all(isinstance(r[0], CellError) for r in t.rows[1:])


class TestDesktopRefresh:
    """Rules a Desktop 2.157 refresh shows where Microsoft's reference is silent or says otherwise: Enter-data and
    #table values refreshed by Desktop and read back over its model (pivot-studio #104's probes)."""

    transform = TestConversions.transform

    def test_number_text(self):
        cases = {"1,23": 123, "(5)": -5, "(1,000)": -1000, "$-5": -5, "-$5": -5, "1,234.5": 1234.5, "+3": 3,
                 "5.": 5, " 12 ": 12}
        for text, value in cases.items():
            assert number_from_text(text, "en-US") == value, text
        assert number_from_text("1e400", "en-US") == float("inf")

    def test_empty_text_is_null(self):
        for ty in ("Int64.Type", "type number", "Currency.Type", "Percentage.Type", "type datetime", "type date",
                   "type logical"):
            assert self.transform(['""'], ty) == [None], ty

    def test_int64_and_currency_take_percent(self):
        assert self.transform(['"10%"'], "Int64.Type") == [0]
        assert self.transform(['"10%"'], "Currency.Type") == [0.1]
        assert self.transform(['"1,23"', '"(5)"', '" 7 "'], "Int64.Type") == [123, -5, 7]

    def test_datetime_text(self):
        assert self.transform(['"Nov 20, 2015"', '"11/20/15"', '"11/20/2015 12:00 AM"'], "type datetime") == [
            dt.datetime(2015, 11, 20)] * 3
        for text in ('"20/11/2015"', '"11/20/2015 24:00"'):
            assert isinstance(self.transform([text], "type datetime")[0], CellError), text

    def test_date_text_with_a_time_is_an_error(self):
        out = self.transform(['"11/20/2015 10:59"', '"2015-11-20"', '"11/20/2015"'], "type date")
        assert isinstance(out[0], CellError) and out[1:] == [dt.date(2015, 11, 20)] * 2

    def test_text_from(self):
        cells = ["0.1", "1e21", "1e15", "1e-5", "1e-4", "-0.0", "true", "#date(2015, 11, 20)",
                 "#datetime(2015, 11, 20, 10, 59, 0)", "1/3", "123456789.125", "-2.5", "#datetime(2015, 11, 20, 22, 5, 9)"]
        assert self.transform(cells, "type text") == [
            "0.1", "1000000000000000000000", "1000000000000000", "1E-05", "0.0001", "0", "true", "11/20/2015",
            "11/20/2015 10:59:00 AM", "0.33333333333333331", "123456789.125", "-2.5", "11/20/2015 10:05:09 PM"]

    def test_constructors(self):
        assert evaluate("#date(2015, 11, 20)") == dt.date(2015, 11, 20)
        assert evaluate("#datetime(2015, 11, 20, 10, 59, 30.25)") == dt.datetime(2015, 11, 20, 10, 59, 30, 250000)
        assert evaluate("#datetimezone(2015, 11, 20, 10, 59, 0, -5, 0)").utcoffset() == dt.timedelta(hours=-5)
        assert evaluate("#time(10, 59, 30)") == dt.time(10, 59, 30)
        assert evaluate("#duration(1, 2, 0, 0)") == dt.timedelta(days=1, hours=2)
        assert evaluate("#time(24, 0, 0)") == dt.time(0)                     # as a Desktop refresh gives it
        for m in ("#date(2015, 2, 30)", "#time(24, 0, 1)", "#datetime(2015, 1, 1, 0, 0, 60)", "#date(2015.5, 1, 1)"):
            with pytest.raises(MError):
                evaluate(m)


class TestDesktopLoad:
    """How Desktop's model stores M values (#104's load probes); see pbix_mcp.m.load."""

    def load(self, m, columns):
        return load_rows(evaluate(m), columns, errors_as_null=True)

    def test_a_value_not_of_its_column_type_is_a_failed_cell(self):
        assert self.load('#table(type table [v = Int64.Type], {{2.5}, {"5"}, {3}, {true}})',
                         [("v", "int64")]) == [[None], [None], [3], [None]]
        assert self.load('#table(type table [v = text], {{5}, {"x"}})', [("v", "string")]) == [[None], ["x"]]

    def test_int64(self):
        assert self.load('#table(type table [v = number], {{2.5}, {3.5}, {-2.5}, {2.4}, {2.6}, {-3.5}, {0.5}})',
                         [("v", "int64")]) == [[3], [4], [-3], [2], [3], [-4], [1]]
        assert [model_value(v, "int64") for v in (True, False, "2.5", " 7 ", "-3", "")] == [-1, 0, 3, 7, -3, 0]
        for v in ("1,234", "1e3", "$5", "(5)", "abc", 1e300, float("inf")):
            with pytest.raises(MError):
                model_value(v, "int64")

    def test_double(self):
        assert [model_value(v, "double") for v in ("2.5", "1,234", "1e3", "$5", " 7 ", "-0.5")] == [
            2.5, 1234, 1000, 5, 7, -0.5]
        assert model_value(float("inf"), "double") == float("inf")
        for v in ("10%", "", "abc"):
            with pytest.raises(MError):
                model_value(v, "double")

    def test_decimal_rounds_the_exact_binary_value_half_to_even(self):
        values = [1.23455, 1.23465, 0.00005, -0.00005, 1.00005, 2.00005, 0.00015, 0.00025, 0.00035, 123.45675,
                  0.03125, 0.09375, 0.15625, -0.03125]
        assert [model_value(v, "decimal") for v in values] == [
            1.2346, 1.2347, 0.0001, -0.0001, 1.0001, 2.0, 0.0001, 0.0003, 0.0003, 123.4567,
            0.0312, 0.0938, 0.1562, -0.0312]

    def test_a_currency_column_rounds_its_digits(self):
        assert self.load('#table(type table [v = Currency.Type], {{1.23455}, {1.23465}, {0.00005}, {-0.00005}, '
                         '{1.00015}, {0.00025}})', [("v", "decimal")]) == [
            [1.2346], [1.2346], [0.0], [0.0], [1.0002], [0.0002]]

    def test_boolean(self):
        assert [model_value(v, "boolean") for v in ("true", "True", "FALSE", "1", "0", 2.5, -1, 0)] == [
            True, True, False, True, False, True, True, False]
        for v in ("yes", ""):
            with pytest.raises(MError):
                model_value(v, "boolean")

    def test_strings_lose_trailing_spaces_and_fold_ascii_case(self):
        assert self.load('#table({"v"}, {{"a"}, {"A"}, {"x "}, {"x"}, {"B"}, {"b "}, {" "}, {" y"}, {"z#(tab)"}, '
                         '{"w#(00A0)"}, {"É"}, {"é"}, {"ß"}, {"SS"}, {"Ä"}, {"ä "}})', [("v", "string")]) == [
            ["a"], ["a"], ["x"], ["x"], ["B"], ["B"], [""], [" y"], ["z"], ["w\u00a0"], ["É"], ["é"], ["ß"], ["SS"],
            ["Ä"], ["ä"]]
        assert model_value(True, "string") == "TRUE" and model_value(False, "string") == "FALSE"

    def test_datetime(self):
        assert model_value(True, "dateTime") == dt.datetime(1899, 12, 29)
        assert model_value(evaluate("#datetimezone(2015, 11, 20, 23, 59, 0, -5, 0)"), "dateTime") == \
            dt.datetime(2015, 11, 20, 23, 59)
        assert model_value(dt.time(10, 59, 30), "dateTime") == dt.datetime(1899, 12, 30, 10, 59, 30)
        assert model_value(dt.timedelta(days=1, hours=2), "dateTime") == dt.datetime(1899, 12, 31, 2)
        assert model_value(60, "dateTime") == dt.datetime(1900, 2, 28)


class TestSecondReview:
    """The review's cases, each answered by a Desktop refresh (pivot-studio #104) or refused."""

    def test_number_text_edges(self):
        assert number_from_text("($5)", "en-US") == -5 and number_from_text("(1e3)", "en-US") == -1000
        assert number_from_text("9007199254740993", "en-US") == 9007199254740993
        assert number_from_text("1e99999", "en-US") == float("inf")
        assert number_from_text("-1e99999", "en-US") == float("-inf") and number_from_text("1e-99999", "en-US") == 0
        assert number_from_text("1e" + "9" * 5000, "en-US") == float("inf")
        assert number_from_text("9" * 5000, "en-US") == float("inf")
        for text in ("(5%)", "٥", "５"):
            with pytest.raises(MError):
                number_from_text(text, "en-US")

    def test_dates(self):
        t = TestConversions.transform
        assert t(self, ['"11/20/30"', '"11/20/49"', '"11/20/50"', '"11/20/99"', '"nov 20, 2015"',
                        '"November 20, 2015"', '"20 Nov 2015"'], "type datetime") == [
            dt.datetime(2030, 11, 20), dt.datetime(2049, 11, 20), dt.datetime(1950, 11, 20), dt.datetime(1999, 11, 20)
        ] + [dt.datetime(2015, 11, 20)] * 3
        assert t(self, ["#date(5, 1, 1)", "12345678901234567890", "2e21"], "type text") == [
            "1/1/0005", "12345678901234567890", "2000000000000000000000"]

    def test_datetime_columns_keep_three_hundredths(self):
        def at(seconds):
            return dt.datetime(2015, 11, 20, 10, 59) + dt.timedelta(seconds=seconds)
        cases = {0.0029: 1, 0.0099: 3, 0.0234: 7, 0.7777: 233, 0.0017: 0, 0.0049: 1, 0.005: 2, 59.9999: 18000}
        for seconds, ticks in cases.items():
            want = dt.datetime(2015, 11, 20, 10, 59) + dt.timedelta(microseconds=round(ticks * 1_000_000 / 300))
            assert model_value(at(seconds), "dateTime") == want, seconds
        assert model_value(1e-7, "dateTime") == dt.datetime(1899, 12, 30, 0, 0, 0, 10000)
        assert model_value(42328.00000005, "dateTime") == dt.datetime(2015, 11, 20, 0, 0, 0, 3333)
        assert model_value(-1.25, "dateTime") == dt.datetime(1899, 12, 29, 6)

    def test_load_text(self):
        assert [model_value(v, "int64") for v in ("+5", ".5", "   ", "5\n", "0.5", "-0.5")] == [5, 1, 0, 5, 1, -1]
        assert [model_value(v, "double") for v in ("-$5", "$-5", "1.", ".5", "+5")] == [-5, -5, 1, 0.5, 5]
        assert [model_value(v, "boolean") for v in ("2.5", "-1", " 1 ", float("nan"), float("inf"))] == [True] * 5
        for v, ty in (("1.", "int64"), ("５", "int64"), ("1e400", "double"), ("５", "double"),
                      (" TRUE ", "boolean")):
            with pytest.raises(MError):
                model_value(v, ty)
        assert [model_value(v, "string") for v in ("a\r", "b\n", "c　", "d\x0b")] == ["a", "b", "c　", "d"]

    def test_constructor_fractions_keep_their_microseconds(self):
        assert evaluate("#time(10, 59, 59.9999999)") == dt.time(10, 59, 59, 999999)
        assert evaluate("#datetimezone(2015, 1, 1, 0, 0, 0, 0, 120)").utcoffset() == dt.timedelta(hours=2)
