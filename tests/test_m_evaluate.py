"""The M evaluator (pbix_mcp.m.evaluate, library, load): language semantics from the
M specification, conversions from Microsoft's function reference, and this
package's own Enter-data M evaluated back to the rows it was built from."""
import datetime as dt

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
            evaluate("Excel.Workbook")
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

    @pytest.mark.parametrize("text", ["", "abc", "1,2", "12,34.5", "1.2.3", "$"])
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

    def test_library_is_exactly_tier_one(self):
        assert LIBRARY_NAMES == {
            "Binary.FromText", "BinaryEncoding.Base64", "BinaryEncoding.Hex", "Binary.Decompress", "Compression.GZip",
            "Compression.Deflate", "Json.Document", "Table.FromRows", "#table", "Table.TransformColumnTypes",
            "Table.Combine", "Int64.Type", "Currency.Type", "Percentage.Type"}


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
        with pytest.raises(MError):
            model_value(1.5, "int64")

    def test_load_rows_maps_columns_and_errors(self):
        t = evaluate('Table.TransformColumnTypes(#table({"a", "b"}, {{"1", "x"}, {"y", "z"}}), {{"a", Int64.Type}})')
        assert load_rows(t, [("b", "string"), ("a", "double")], errors_as_null=True) == [["x", 1.0], ["z", None]]
