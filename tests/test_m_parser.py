"""The M lexer, parser and free-name analysis (pbix_mcp.m), against the M language
specification's grammar. Cases are written fresh from the specification; the
Enter-data documents come from this package's own builder."""
import random

import pytest

from pbix_mcp.builder import _build_enter_data_m
from pbix_mcp.m import MSyntaxError, ast, free_names, parse_document, parse_expression, tokenize, walk


def kinds(text):
    return [(t.kind, t.value) for t in tokenize(text)[:-1]]


class TestLexer:
    def test_dotted_identifier_is_one_token(self):
        assert kinds("Table.AddColumn") == [("identifier", "Table.AddColumn")]

    def test_range_keeps_its_dots(self):
        assert kinds("{1..3}") == [("punct", "{"), ("number", 1), ("punct", ".."), ("number", 3), ("punct", "}")]

    def test_dot_needs_an_identifier_after_it(self):
        assert kinds("Column1.1") == [("identifier", "Column1"), ("number", 0.1)]

    def test_quoted_identifier_and_escapes(self):
        assert kinds('#"Changed ""Type"""') == [("quoted", 'Changed "Type"')]

    def test_text_escapes(self):
        assert kinds('"a#(cr,lf)b#(tab)#(0041)#(#)"') == [("text", "a\r\nb\tA#(")]

    def test_hash_keywords(self):
        assert kinds("#table #date #shared") == [("keyword", "#table"), ("keyword", "#date"), ("keyword", "#shared")]

    def test_unknown_hash_word(self):
        with pytest.raises(MSyntaxError):
            tokenize("#foo")

    def test_numbers(self):
        assert kinds("0xff 12 1.5 .5 1e3 2E-2") == [("number", 255), ("number", 12), ("number", 1.5),
                                                    ("number", 0.5), ("number", 1000.0), ("number", 0.02)]

    def test_every_space_separator_is_whitespace(self):
        # Desktop's editor can leave U+00A0 between tokens.
        assert kinds("let a　= 1 in a") == kinds("let a = 1 in a")

    def test_comments(self):
        assert kinds("1 // one\n+ /* two */ 2") == [("number", 1), ("punct", "+"), ("number", 2)]

    @pytest.mark.parametrize("text", ['"abc', "/* x", '#"x', '"#(zz)"'])
    def test_unterminated(self, text):
        with pytest.raises(MSyntaxError):
            tokenize(text)

    def test_error_position(self):
        with pytest.raises(MSyntaxError) as e:
            parse_expression("let\n  a = 1,\n  b = \nin a")
        assert (e.value.line, e.value.column) == (4, 1)


class TestParser:
    def test_precedence(self):
        n = parse_expression("1 + 2 * 3 = 7 and not false or x ?? y")
        assert isinstance(n, ast.Binary) and n.op == "??"
        orr = n.left
        assert orr.op == "or" and orr.left.op == "and"
        eq = orr.left.left
        assert eq.op == "=" and eq.left.op == "+" and eq.left.right.op == "*"

    def test_left_associative(self):
        n = parse_expression("a - b - c")
        assert n.left.op == "-" and n.left.left == ast.Identifier("a")

    def test_meta_binds_tighter_than_arithmetic(self):
        n = parse_expression("a + b meta [x = 1]")
        assert n.op == "+" and n.right.op == "meta"

    def test_let(self):
        n = parse_expression('let #"A b" = 1, c = #"A b" + 1 in c')
        assert [b.name for b in n.bindings] == ["A b", "c"]

    def test_each_is_a_function_of_underscore(self):
        n = parse_expression("each [x] + _")
        assert isinstance(n, ast.Function) and n.each_ and [p.name for p in n.parameters] == ["_"]
        assert n.body.left == ast.FieldAccess(None, "x")

    def test_function_expression(self):
        n = parse_expression("(x as number, optional y as nullable text) as table => x")
        assert [(p.name, p.optional) for p in n.parameters] == [("x", False), ("y", True)]
        assert n.parameters[1].type == ast.NullableType(ast.PrimitiveType("text"))
        assert n.return_type == ast.PrimitiveType("table")

    def test_parenthesis_that_is_not_a_function(self):
        assert parse_expression("(x) + 1").op == "+"
        assert isinstance(parse_expression("((x) => x)(1)"), ast.Invoke)

    def test_postfix_chain(self):
        n = parse_expression('Source{[Item="Sheet1",Kind="Sheet"]}[Data]')
        assert isinstance(n, ast.FieldAccess) and n.name == "Data"
        assert isinstance(n.target, ast.ItemAccess)
        assert [f.name for f in n.target.index.fields] == ["Item", "Kind"]

    def test_optional_access(self):
        assert parse_expression("x{0}?").optional
        assert parse_expression("x[y]?").optional

    def test_generalized_field_names(self):
        assert parse_expression("x[Country Region]").name == "Country Region"
        assert parse_expression("[Sales.Sales Person]") == ast.FieldAccess(None, "Sales.Sales Person")
        assert [f.name for f in parse_expression("[Item = 1, Serialized.Text = true]").fields] == \
            ["Item", "Serialized.Text"]

    def test_projection(self):
        assert parse_expression("x[[a], [b c]]").names == ["a", "b c"]
        assert parse_expression("[[a]]") == ast.FieldProjection(None, ["a"])

    def test_list_with_ranges(self):
        n = parse_expression("{1..3, 7}")
        assert isinstance(n.items[0], ast.Range) and n.items[1] == ast.Literal("number", 7)

    def test_types(self):
        t = parse_expression('type table [A = number, #"B c" = Int64.Type, D]').type
        assert isinstance(t, ast.TableType)
        assert [(f.name, f.type) for f in t.row.fields] == [
            ("A", ast.PrimitiveType("number")),
            ("B c", ast.ExprType(ast.Identifier("Int64.Type"))),
            ("D", None)]
        assert parse_expression("type [a = number, optional b, ...]").type.open
        assert parse_expression("type {number}").type == ast.ListType(ast.PrimitiveType("number"))
        assert isinstance(parse_expression("type function (x as number) as text").type, ast.FunctionType)

    def test_enter_data_type_idiom(self):
        n = parse_expression("let _t = ((type nullable text) meta [Serialized.Text = true]) in type table [ID = _t]")
        assert n.bindings[0].value.op == "meta"
        assert n.body.type.row.fields[0].type == ast.ExprType(ast.Identifier("_t"))

    def test_as_and_is_take_a_primitive_type(self):
        assert parse_expression("x as nullable number").right == ast.NullableType(ast.PrimitiveType("number"))
        with pytest.raises(MSyntaxError):
            parse_expression("x as Int64.Type")

    def test_try(self):
        assert parse_expression("try 1/0 otherwise 2").otherwise == ast.Literal("number", 2)
        assert parse_expression("try x catch (e) => e[Message]").catch.parameters[0].name == "e"
        assert parse_expression("try x").otherwise is None

    def test_section_document(self):
        n = parse_document('[Version = "1.0"] section Section1;\nshared A = 1;\nB = A + 1;')
        assert isinstance(n, ast.Section) and n.name == "Section1"
        assert [(m.name, m.shared) for m in n.members] == [("A", True), ("B", False)]

    def test_section_access_and_inclusive_identifier(self):
        assert parse_expression("Section1!Query") == ast.SectionAccess("Section1", "Query")
        assert parse_expression("@f") == ast.Identifier("f", inclusive=True)

    def test_literals(self):
        assert parse_expression("null") == ast.Literal("null", None)
        assert parse_expression("true") == ast.Literal("logical", True)
        assert parse_expression('#!"x"') == ast.Literal("verbatim", "x")
        assert isinstance(parse_expression("..."), ast.NotImplementedExpr)

    def test_spans(self):
        text = "let a = Table.AddColumn(t, \"x\", each 1) in a"
        n = parse_expression(text)
        call = n.bindings[0].value
        assert text[call.start:call.end] == 'Table.AddColumn(t, "x", each 1)'

    @pytest.mark.parametrize("text", [
        "let in 1", "1 +", "[a = ]", "(x) => ", "{1..}", "x{}", "if a then b", "a b", "1 2", "x.",
        "type \"text\"", "type (text)", "1 + if a then b else c", "1 + each 1", "section S; A = 1",
        "type table [#\"A\" _t]", "",
    ])
    def test_rejects(self, text):
        with pytest.raises(MSyntaxError):
            parse_document(text)

    def test_depth(self):
        assert parse_expression("(" * 100 + "1" + ")" * 100) == ast.Literal("number", 1)
        with pytest.raises(MSyntaxError, match="nested too deeply"):
            parse_expression("(" * 5000 + "1" + ")" * 5000)


class TestFreeNames:
    def test_let_variables_are_bound(self):
        n = parse_document('let Source = Csv.Document(Web.Contents(Base, [RelativePath = Path])), '
                           '#"Promoted" = Table.PromoteHeaders(Source) in #"Promoted"')
        assert free_names(n) == ["Csv.Document", "Web.Contents", "Base", "Path", "Table.PromoteHeaders"]

    def test_each_binds_underscore(self):
        assert free_names(parse_expression("each [a] & _")) == []
        assert free_names(parse_expression("[a]")) == ["_"]

    def test_function_parameters_are_bound(self):
        n = parse_expression("(d as date, optional c as nullable text) => Date.ToText(d, Fmt, c)")
        assert free_names(n) == ["Date.ToText", "Fmt"]

    def test_record_fields_see_each_other(self):
        assert free_names(parse_expression("[a = 1, b = a + c]")) == ["c"]

    def test_section_members_are_bound(self):
        n = parse_document("section S; shared A = B + Other; B = Table.RowCount(A);")
        assert free_names(n) == ["Other", "Table.RowCount"]

    def test_type_expressions_and_keywords(self):
        n = parse_expression('#table(type table [a = Int64.Type], {{1}})')
        assert free_names(n) == ["#table", "Int64.Type"]


class TestBuilderOutput:
    """The Enter-data M this package writes parses, and refers only to the library."""

    def test_enter_data(self):
        m = _build_enter_data_m([{"name": "ID", "data_type": "Int64"}, {"name": "Name", "data_type": "String"},
                                 {"name": "When", "data_type": "DateTime"}],
                                [{"ID": 1, "Name": "a", "When": "2024-01-01T00:00:00"}, {"ID": 2}])
        n = parse_document(m)
        assert isinstance(n, ast.Let)
        names = free_names(n)
        assert "Table.FromRows" in names and "Table.TransformColumnTypes" in names
        assert all("." in x or x.startswith("#") for x in names), names

    def test_token_mutations_never_crash(self):
        m = _build_enter_data_m([{"name": "A b", "data_type": "Double"}], [{"A b": 1.5}, {"A b": 2}])
        toks = tokenize(m)[:-1]
        rng = random.Random(7)
        outcomes = set()
        for _ in range(500):
            i = rng.randrange(len(toks))
            j = min(len(toks) - 1, i + rng.randrange(3))
            mutant = m[:toks[i].start] + m[toks[j].end:]
            try:
                node = parse_document(mutant)
                list(walk(node))
                outcomes.add("ok")
            except MSyntaxError:
                outcomes.add("error")
        assert outcomes == {"ok", "error"}
