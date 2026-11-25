"""Table.Sort, Table.Distinct, Table.Buffer, Table.AddIndexColumn, Table.RowCount and Table.ExpandRecordColumn
(pbix_mcp.m.table_ops): Power Query's rules as its engine states them and Microsoft's function reference gives
them. Each expected value is written out by hand from those rules, never computed by the code under test."""
import datetime as dt
import math

import pytest

from pbix_mcp.m import MError, evaluate
from pbix_mcp.m.evaluate import force_table
from pbix_mcp.m.values import ANY, MType, Table

T3 = '#table({"k", "v"}, {{2, "b"}, {1, "a"}, {3, "c"}})'


def rows(expr: str) -> list[list]:
    t = evaluate(expr)
    assert isinstance(t, Table)
    return force_table(t, errors_as_null=False)


def err(expr: str) -> MError:
    with pytest.raises(MError) as e:
        t = evaluate(expr)
        if isinstance(t, Table):
            force_table(t, errors_as_null=False)
    return e.value


def col(expr: str, name: str = "k") -> list:
    t = evaluate(expr)
    return [r[t.columns.index(name)] for r in force_table(t, errors_as_null=False)]


class TestSortCriteria:
    def test_a_column_name_sorts_ascending(self):
        assert col(f'Table.Sort({T3}, "k")') == [1, 2, 3]

    def test_one_pair_is_one_criterion(self):
        assert col(f'Table.Sort({T3}, {{"k", Order.Descending}})') == [3, 2, 1]

    def test_a_list_of_pairs(self):
        assert col(f'Table.Sort({T3}, {{{{"k", Order.Descending}}}})') == [3, 2, 1]

    def test_a_list_of_names_is_one_criterion_each(self):
        t = '#table({"a", "b"}, {{1, 2}, {0, 9}, {1, 1}})'
        assert rows(f'Table.Sort({t}, {{"a", "b"}})') == [[0, 9], [1, 1], [1, 2]]

    def test_several_criteria_in_turn_with_their_directions(self):
        t = '#table({"a", "b"}, {{1, 2}, {0, 9}, {1, 1}})'
        assert rows(f'Table.Sort({t}, {{{{"a", Order.Descending}}, {{"b", Order.Ascending}}}})') == \
            [[1, 1], [1, 2], [0, 9]]

    def test_a_key_selector(self):
        assert col(f'Table.Sort({T3}, each -[k])') == [3, 2, 1]
        assert col(f'Table.Sort({T3}, {{each [v], Order.Descending}})') == [3, 2, 1]

    def test_a_two_parameter_function_compares_rows(self):
        cmp = "(x, y) => if x[k] < y[k] then 1 else if x[k] > y[k] then -1 else 0"
        assert col(f'Table.Sort({T3}, {cmp})') == [3, 2, 1]
        assert col(f'Table.Sort({T3}, (x, y) => 2.0 * (if x[k] < y[k] then -1 else 1))') == [1, 2, 3]

    @pytest.mark.parametrize("direction", ["0", "0.0"])
    def test_a_direction_equal_to_order_ascending(self, direction):
        assert col(f'Table.Sort({T3}, {{"k", {direction}}})') == [1, 2, 3]

    @pytest.mark.parametrize("criteria", ['{"k", 2}', '{"k", true}', '{"nope", Order.Ascending}', '"nope"',
                                          '{{"k", 0, 1}}', '{{"k"}}', '{{"k", true}}', '{{"k", 2}}',
                                          'null', '{null}'])
    def test_an_invalid_criterion_is_the_criteria_error(self, criteria):
        e = err(f'Table.Sort({T3}, {criteria})')
        assert (e.reason, e.message) == ("Expression.Error", "The specified sort criteria is invalid.")

    def test_the_inner_error_is_kept(self):
        e = err(f'Table.Sort({T3}, {{"k", 2}})')
        assert e.__cause__.message == "The specified sort direction is invalid."
        e = err(f'Table.Sort({T3}, "nope")')
        assert e.__cause__.message == "The column 'nope' of the table wasn't found."

    def test_no_criteria(self):
        e = err(f'Table.Sort({T3}, {{}})')
        assert e.message == "'Table.Sort' requires at least one sort criterion."

    def test_not_a_table(self):
        assert "table" in err('Table.Sort({1}, "k")').message


class TestSortOrder:
    def test_mixed_kinds_order_by_kind(self):
        # null < time < date < datetime < datetimezone < duration < number < logical < text
        t = ('#table({"k"}, {{"a"}, {true}, {5}, {#duration(1, 0, 0, 0)}, {#datetimezone(2020, 1, 1, 0, 0, 0, 0, 0)}, '
             '{#datetime(2020, 1, 1, 0, 0, 0)}, {#date(2020, 1, 1)}, {#time(1, 0, 0)}, {null}})')
        assert col(f'Table.Sort({t}, "k")') == [None, dt.time(1), dt.date(2020, 1, 1), dt.datetime(2020, 1, 1),
                                                dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc), dt.timedelta(days=1),
                                                5, True, "a"]

    def test_nulls_last_descending(self):
        t = '#table({"k"}, {{1}, {null}, {2}})'
        assert col(f'Table.Sort({t}, {{"k", Order.Descending}})') == [2, 1, None]

    def test_text_is_ordinal(self):
        t = '#table({"k"}, {{"a"}, {"B"}, {"Z"}, {"b"}})'
        assert col(f'Table.Sort({t}, "k")') == ["B", "Z", "a", "b"]

    def test_text_by_utf16_code_units(self):
        # U+1F600 is the surrogate pair D83D DE00, below U+FF01 in UTF-16 (code points put it above)
        t = '#table({"k"}, {{"！"}, {"\U0001F600"}})'
        assert col(f'Table.Sort({t}, "k")') == ["\U0001F600", "！"]

    def test_nan_below_every_number(self):
        t = '#table({"k"}, {{1}, {#nan}, {-#infinity}})'
        out = col(f'Table.Sort({t}, "k")')
        assert math.isnan(out[0]) and out[1:] == [-math.inf, 1]

    def test_datetimezones_by_instant(self):
        # 09:00+10:00 is 23:00 UTC the day before, so it sorts before 00:00+00:00
        t = '#table({"k"}, {{#datetimezone(2020, 1, 1, 0, 0, 0, 0, 0)}, {#datetimezone(2020, 1, 1, 9, 0, 0, 10, 0)}})'
        out = col(f'Table.Sort({t}, "k")')
        assert [v.utcoffset() for v in out] == [dt.timedelta(hours=10), dt.timedelta(0)]

    def test_ties_keep_their_order_in_a_small_table(self):
        t = '#table({"k", "v"}, {{1, "x"}, {0, "y"}, {1, "z"}, {0, "w"}})'
        assert col(f'Table.Sort({t}, "k")', "v") == ["y", "w", "x", "z"]

    def test_a_structured_key_fails_the_sort(self):
        # a list as the left operand always fails (as the right one, which side depends on the sort: a NUC probe)
        t = '#table({"k"}, {{{1}}, {{2}}})'
        e = err(f'Table.Sort({t}, "k")')
        assert e.message == "We cannot apply operator < to types List and List."


class TestSortLaziness:
    def test_a_failed_key_fails_the_whole_table_when_read(self):
        t = '#table({"k", "v"}, {{1, 1}, {error "bad", 2}})'
        s = evaluate(f'Table.Sort({t}, "k")')           # the criteria are fine: the call itself succeeds
        with pytest.raises(MError, match="bad"):
            force_table(s, errors_as_null=True)

    def test_a_lone_row_is_never_compared(self):
        assert rows('Table.Sort(#table({"k"}, {{{1}}}), "k")') == [[[1]]]
        t = evaluate('Table.Sort(#table({"k"}, {{error "bad"}}), "k")')
        assert len(t.rows) == 1                       # its failed key isn't read

    @pytest.mark.parametrize("result, message", [("0.5", "The number is out of range of a 32 bit integer value."),
                                                 ('"1"', 'We cannot convert the value "1" to type Number.')])
    def test_a_comparer_answers_a_whole_number(self, result, message):
        assert err(f'Table.Sort({T3}, (x, y) => {result})').message == message

    def test_row_count_of_a_sort_does_not_sort(self):
        t = '#table({"k"}, {{1}, {error "bad"}})'
        assert evaluate(f'Table.RowCount(Table.Sort({t}, "k"))') == 2

    def test_the_type_is_kept(self):
        t = evaluate('Table.Sort(Table.TransformColumnTypes(#table({"k"}, {{"2"}, {"1"}}), {{"k", Int64.Type}}), "k")')
        assert t.types[0].facet == "Int64.Type"


class TestDistinct:
    T = '#table({"id", "v"}, {{1, "a"}, {2, "b"}, {1, "c"}, {2, "b"}})'

    def test_every_column_by_default(self):
        assert rows(f'Table.Distinct({self.T})') == [[1, "a"], [2, "b"], [1, "c"]]

    def test_one_column_first_row_wins(self):
        assert rows(f'Table.Distinct({self.T}, "id")') == [[1, "a"], [2, "b"]]
        assert rows(f'Table.Distinct({self.T}, {{"id"}})') == [[1, "a"], [2, "b"]]

    def test_no_columns_keeps_the_first_row(self):
        assert rows(f'Table.Distinct({self.T}, {{}})') == [[1, "a"]]

    def test_equality(self):
        # 1 = 1.0; "a" <> "A"; 1 <> "1"; null = null; NaN equals nothing; one instant, two offsets
        t = ('#table({"k"}, {{1}, {1.0}, {"a"}, {"A"}, {"1"}, {null}, {null}, {#nan}, {#nan}, '
             '{#datetimezone(2020, 1, 1, 10, 0, 0, 10, 0)}, {#datetimezone(2020, 1, 1, 0, 0, 0, 0, 0)}})')
        out = col(f'Table.Distinct({t})')
        assert out[:5] == [1, "a", "A", "1", None]
        assert sum(isinstance(v, float) and math.isnan(v) for v in out) == 2
        assert [v.utcoffset() for v in out if isinstance(v, dt.datetime)] == [dt.timedelta(hours=10)]
        assert len(out) == 8

    def test_records_and_lists_by_value(self):
        t = '#table({"k"}, {{[a = 1, b = 2]}, {[b = 2, a = 1]}, {{1, 2}}, {{1, 2}}, {{2, 1}}})'
        assert len(rows(f'Table.Distinct({t})')) == 3

    @pytest.mark.parametrize("criteria", ['"nope"', '{"id", "nope"}', '{1}', 'each [id]', '{"id", each _}',
                                          '{{"id", each _}}', '5'])
    def test_an_invalid_criterion_is_the_criteria_error(self, criteria):
        e = err(f'Table.Distinct({self.T}, {criteria})')
        assert (e.reason, e.message) == ("Expression.Error", "The specified distinct criteria is invalid.")

    def test_a_custom_comparer_is_refused(self):
        e = err(f'Table.Distinct({self.T}, each [id])')
        assert e.__cause__.message == "A custom comparer cannot be used in this context."

    def test_a_failed_key_fails_when_read_and_other_cells_are_not_read(self):
        assert rows('Table.Distinct(#table({"k", "x"}, {{1, 1}, {1, 2}}), "k")') == [[1, 1]]
        t = evaluate('Table.Distinct(#table({"k", "x"}, {{1, error "x"}, {1, 2}}), "k")')
        assert len(t.rows) == 1
        with pytest.raises(MError, match="bad"):
            evaluate('Table.RowCount(Table.Distinct(#table({"k"}, {{1}, {error "bad"}}), "k"))')

    def test_distinct_over_a_sort_runs_below_it(self):
        # the kept row is the first in the unsorted input ("b"), then sorted
        t = '#table({"id", "v"}, {{1, "b"}, {1, "a"}, {0, "z"}})'
        assert rows(f'Table.Distinct(Table.Sort({t}, "v"), "id")') == [[1, "b"], [0, "z"]]
        # a buffer between them stops the rewrite: the first after sorting ("a")
        assert rows(f'Table.Distinct(Table.Buffer(Table.Sort({t}, "v")), "id")') == [[1, "a"], [0, "z"]]


class TestBuffer:
    def test_the_same_table_and_type(self):
        t = evaluate('Table.Buffer(Table.TransformColumnTypes(#table({"k"}, {{"1"}}), {{"k", Int64.Type}}))')
        assert (t.columns, force_table(t, errors_as_null=False), t.types[0].facet) == (["k"], [[1]], "Int64.Type")

    def test_eager_reads_the_rows_at_the_step(self):
        with pytest.raises(MError, match="bad"):
            evaluate('Table.Buffer(Table.Sort(#table({"k"}, {{1}, {error "bad"}}), "k"))')

    def test_delayed_reads_them_when_read(self):
        t = evaluate('Table.Buffer(Table.Sort(#table({"k"}, {{1}, {error "bad"}}), "k"), [BufferMode = BufferMode.Delayed])')
        with pytest.raises(MError, match="bad"):
            t.rows

    def test_a_failed_cell_stays_a_failed_cell(self):
        assert evaluate('Table.RowCount(Table.Buffer(#table({"k"}, {{1}, {error "bad"}})))') == 2

    def test_options(self):
        assert err('Table.Buffer(#table({"k"}, {}), [Foo = 1])').message == \
            "This function doesn't support the query option 'Foo' with value '1'."
        assert err('Table.Buffer(#table({"k"}, {}), [BufferMode = 3])').message == \
            "The value for a 'BufferMode.Type' enumeration was not one of the allowed values: " \
            "'BufferMode.Eager, BufferMode.Delayed'."
        assert evaluate('Table.RowCount(Table.Buffer(#table({"k"}, {{1}}), [BufferMode = null]))') == 1


class TestAddIndexColumn:
    def test_defaults(self):
        t = evaluate(f'Table.AddIndexColumn({T3}, "i")')
        assert t.columns == ["k", "v", "i"] and [r[2] for r in t.rows] == [0, 1, 2]
        assert t.types[2] == MType("number", False)

    def test_initial_and_increment(self):
        assert col(f'Table.AddIndexColumn({T3}, "i", 10, -5)', "i") == [10, 5, 0]
        assert col(f'Table.AddIndexColumn({T3}, "i", 1, 0)', "i") == [1, 1, 1]

    def test_increments_accumulate(self):
        # 0.1 added nine times (doubles), not 0.1 times the row number: 0.7999999999999999, not 0.8
        t = '#table({"k"}, {{1}, {2}, {3}, {4}, {5}, {6}, {7}, {8}, {9}, {10}})'
        assert col(f'Table.AddIndexColumn({t}, "i", 0, 0.1)', "i") == \
            [0, 0.1, 0.2, 0.30000000000000004, 0.4, 0.5, 0.6, 0.7, 0.7999999999999999, 0.8999999999999999]

    def test_the_type_is_ascribed(self):
        t = evaluate(f'Table.AddIndexColumn({T3}, "i", 0, 1, type text)')
        assert t.types[2].kind == "text" and [r[2] for r in t.rows] == [0, 1, 2]

    def test_argument_errors(self):
        assert err(f'Table.AddIndexColumn({T3}, "k")').message == "The field 'k' already exists in the record."
        assert err(f'Table.AddIndexColumn({T3}, "i", "0")').message == 'We cannot convert the value "0" to type Number.'
        assert err(f'Table.AddIndexColumn({T3}, "i", 0, true)').message == "We cannot convert the value true to type Number."
        assert err(f'Table.AddIndexColumn({T3}, 1)').message == "We cannot convert the value 1 to type Text."

    def test_a_failed_cell_keeps_its_index(self):
        t = evaluate('Table.AddIndexColumn(#table({"k"}, {{error "bad"}}), "i")')
        assert t.rows[0][1] == 0


class TestRowCount:
    def test_counts(self):
        assert evaluate(f'Table.RowCount({T3})') == 3
        assert evaluate('Table.RowCount(#table({"k"}, {}))') == 0

    def test_reads_no_cell(self):
        assert evaluate('Table.RowCount(#table({"k"}, {{error "bad"}}))') == 1


class TestExpandRecordColumn:
    T = '#table({"a", "r", "z"}, {{1, [x = 1, y = 2, w = 0], 9}, {2, [y = 3], 8}, {3, null, 7}})'

    def test_in_place_named_as_the_fields(self):
        t = evaluate(f'Table.ExpandRecordColumn({self.T}, "r", {{"x", "y"}})')
        assert t.columns == ["a", "x", "y", "z"]
        assert force_table(t, errors_as_null=False) == [[1, 1, 2, 9], [2, None, 3, 8], [3, None, None, 7]]
        assert t.types == [ANY] * 4

    def test_new_names(self):
        assert evaluate(f'Table.ExpandRecordColumn({self.T}, "r", {{"x"}}, {{"r"}})').columns == ["a", "r", "z"]
        assert evaluate(f'Table.ExpandRecordColumn({self.T}, "r", {{"x"}}, null)').columns == ["a", "x", "z"]

    def test_no_fields_removes_the_column(self):
        assert evaluate(f'Table.ExpandRecordColumn({self.T}, "r", {{}})').columns == ["a", "z"]

    def test_cells_that_are_not_records(self):
        t = evaluate('Table.ExpandRecordColumn(#table({"r"}, {{"text"}, {error "bad"}, {[x = error "inner"]}}), "r", {"x", "y"})')
        assert [type(c).__name__ for r in t.rows for c in r] == ["CellError"] * 4 + ["CellError", "NoneType"]
        assert t.rows[0][0].error.message == 'We cannot convert the value "text" to type Record.'
        assert t.rows[1][0].error.message == "bad"
        assert t.rows[2][0].error.message == "inner"

    def test_argument_errors(self):
        assert err(f'Table.ExpandRecordColumn({self.T}, "nope", {{"x"}})').message == \
            "The column 'nope' of the table wasn't found."
        e = err(f'Table.ExpandRecordColumn({self.T}, "r", {{"x", "y"}}, {{"p"}})')
        assert e.message == "We expected newColumnNames to have the same number of items as fieldNames."
        assert err(f'Table.ExpandRecordColumn({self.T}, "r", {{"x", "y"}}, {{"p", "q", "s"}})').message == e.message
        assert err(f'Table.ExpandRecordColumn({self.T}, "r", {{"x", "x"}})').message == \
            "The field 'x' already exists in the record."
        assert err(f'Table.ExpandRecordColumn({self.T}, "r", {{"a"}})').message == \
            "The field 'a' already exists in the record."
        assert err(f'Table.ExpandRecordColumn({self.T}, "r", null)').message == \
            "We cannot convert the value null to type List."
        assert err(f'Table.ExpandRecordColumn({self.T}, "r", {{1}})').message == \
            "We cannot convert the value 1 to type Text."

    def test_a_record_type_gives_the_field_types(self):
        t = evaluate('Table.ExpandRecordColumn(#table(type table [r = [x = number, y = text]], {{[x = 1, y = "a"]}}), '
                     '"r", {"x", "q"})')
        assert [ty.kind for ty in t.types] == ["number", "any"]


class TestThePerformanceAnalyzersSteps:
    """The public Microsoft sample's steps, over made-up events."""

    def test_events(self):
        m = '''let
            events = {[name = "Query", component = "DSE", start = #datetimezone(2024, 1, 1, 0, 0, 2, 0, 0), id = "b"],
                      [name = "Render", component = "Visual", start = #datetimezone(2024, 1, 1, 0, 0, 1, 0, 0), id = "a"],
                      [name = "Query", component = "DSE", start = #datetimezone(2024, 1, 1, 0, 0, 3, 0, 0), id = "a"]},
            T = Table.FromList(events, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
            E = Table.ExpandRecordColumn(T, "Column1", {"name", "component", "start", "id"}, {"name", "component", "start", "id"}),
            S = Table.Sort(E, {{"start", Order.Ascending}}),
            A = Table.AddColumn(S, "event_type", each [component] & "_" & [name], type text),
            Raw = Table.Buffer(A),
            I = Table.AddIndexColumn(Raw, "Index", 0, 1),
            R = Table.RenameColumns(I, {{"Index", "RawIndex"}}),
            D = Table.Distinct(R, {"id"}),
            Types = Table.Distinct(Table.SelectColumns(Raw, {"name", "component", "event_type"}), {"event_type"})
        in
            [d = D, raw = Table.RowCount(Raw), unique = Table.RowCount(D), types = Types]'''
        r = evaluate(m)
        d = force_table(r["d"], errors_as_null=False)
        ids, index = r["d"].columns.index("id"), r["d"].columns.index("RawIndex")
        assert [(x[ids], x[index]) for x in d] == [("a", 0), ("b", 1)]
        assert (r["raw"], r["unique"]) == (3, 2)
        assert [x[2] for x in force_table(r["types"], errors_as_null=False)] == ["Visual_Render", "DSE_Query"]
