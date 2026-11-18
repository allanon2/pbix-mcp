"""check_query: what Power Query's Advanced Editor reports for a query's text.

Each case pins the message, the range (UTF-16 offsets, 0-based rows and columns) and whether the
query may be saved, as Power Query's editor reports them.
"""
import pytest

from pbix_mcp.m.check import NO_SYNTAX_ERRORS, check_query, escape_source_text


def _err(text):
    r = check_query(text)
    e = r.sourceError
    return (None if e is None else (e.text, e.startPosition, e.endPosition)), r.canCommit


# --- reads cleanly ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "",
    "let a = 1 in a",
    "let a = 1 in a // trailing",
    "let x = NoSuchName in x",            # an unknown name is found only when the query runs
    "let in 1",                           # a let with no variables reads
    "#table",
    "#date(2020, 1, 2)",
    "[a = 1][a]",
    "{1..3}",
    "type table [a = number, optional b = text]",
    "type function (x as number) as text",
    "(x as number, optional y as text) as any => x",
    "try 1 catch (e) => e[Message]",
    "try 1 catch () => 2",
    "try 1 otherwise 2",
    "a ?? b",
    "#!\"section S;\"",                   # a verbatim literal holding a section: not an expression, no error
    "Table.SelectRows(#\"Changed Type\", each [x] > 0)",
    "\"a#(cr,lf)b\"",
    "@f(1)",
    "S!x",
    "1e5 + .5 + 0x1F",
])
def test_reads_cleanly(text):
    r = check_query(text)
    assert r.sourceError is None and r.canCommit
    assert r.message == NO_SYNTAX_ERRORS


# --- syntax errors: shown, and the query may still be saved ----------------------------------

def test_ends_early():
    t = "let a = 1, b = 2 in a +"
    assert _err(t) == (("Token Literal expected.", len(t), len(t)), True)


def test_missing_comma_between_variables():
    assert _err("let a = 1 b = 2 in a") == (("Token ',' expected.", 10, 11), True)


def test_unterminated_string_is_an_invalid_literal_to_the_end():
    t = 'let a = "abc in a'
    assert _err(t) == (("Invalid literal.", 8, len(t)), True)


def test_unterminated_block_comment_is_a_token():
    t = "let a = 1 in a /* open"
    assert _err(t) == (("Token Eof expected.", 15, len(t)), True)


def test_bad_character():
    assert _err("let a = 1 in a $") == (("Token Eof expected.", 15, 16), True)


def test_error_at_offset_zero():
    assert _err(")") == (("Token Literal expected.", 0, 1), True)


def test_section_document_is_an_invalid_expression():
    t = "section S; a = 1;"
    assert _err(t) == (("Invalid expression.", 0, len(t)), True)


@pytest.mark.parametrize("text, terminator, comma", [
    ("let a = 1, in a", "'in'", 9),
    ("{1, 2,}", "'}'", 5),
    ("[a = 1,]", "']'", 6),
    ("f(1,)", "')'", 3),
])
def test_comma_before_terminator(text, terminator, comma):
    assert _err(text) == ((f"A ',' cannot precede a {terminator}.", comma, comma + 1), True)


@pytest.mark.parametrize("text, start, end", [
    ("1d", 0, 2),
    ("0x", 0, 2),
    ("#foo", 0, 4),
    ('"a#(zz)b"', 0, 9),
    ('"a#(cr,)b"', 0, 10),
    ('"a#()"', 0, 6),
])
def test_invalid_literals(text, start, end):
    assert _err(text) == (("Invalid literal.", start, end), True)


def test_trailing_dot_name_is_invalid():
    # A dot followed by any character but a dot joins the word, so "a." is one (invalid) name.
    assert _err("let a = 1 in a. ") == (("Invalid identifier.", 13, 15), True)


def test_dot_at_the_very_end_is_a_token_of_its_own():
    assert _err("let a = 1 in a.") == (("Token Eof expected.", 14, 15), True)


def test_unterminated_quoted_name_is_invalid():
    t = 'let #"a = 1 in a'
    assert _err(t) == (("Invalid identifier.", 4, len(t)), True)


def test_character_beyond_the_bmp_is_no_letter():
    # U+1D4B3 is a surrogate pair to Power Query: two code units, neither a letter.
    assert _err("let \U0001D4B3 = 1 in 1") == (("Invalid identifier.", 4, 6), True)


def test_invalid_type():
    assert _err("x as foo") == (("The type identifier is invalid.", 5, 8), True)


def test_required_after_optional():
    assert _err("(optional a, b) => a") == (
        ("You cannot have a required argument following an optional argument.", 13, 14), True)


def test_bad_catch():
    t = "try 1 catch (e as number) => e"
    msg = ("The 'catch' clause of a try/catch expression must be followed by a function definition with 0 or 1 "
           "arguments and no type constraints.")
    assert _err(t) == ((msg, len(t), len(t)), True)


def test_too_deep():
    t = "(" * 1000 + "1" + ")" * 1000
    (msg, start, _end), can = _err(t)
    assert msg == "This query is too complicated to run." and can
    assert 0 < start < 1000


def test_not_too_deep():
    t = "(" * 300 + "1" + ")" * 300
    assert check_query(t).sourceError is None


def test_rows_and_columns():
    r = check_query("let\r\n  a = 1\r\n  b = 2\r\nin a")
    e = r.sourceError
    assert (e.text, e.startRow, e.startColumn, e.endRow, e.endColumn) == ("Token ',' expected.", 2, 2, 2, 3)


def test_lone_cr_ends_a_row():
    e = check_query("let\ra = 1\rb = 2 in a").sourceError
    assert (e.startRow, e.startColumn) == (2, 0)


# --- checks after reading: shown, and the query may not be saved ---------------------------

def test_duplicate_variable():
    assert _err("let a = 1, a = 2 in a") == (
        ("The variable named 'a' is already defined in this scope.", 11, 12), False)


def test_duplicate_parameter():
    assert _err("(x, x) => x") == (("The parameter name 'x' is a duplicate.", 4, 5), False)


def test_duplicate_field():
    assert _err("[a = 1, a = 2]") == (("The field named 'a' is already defined in this scope.", 8, 9), False)


def test_duplicate_field_quoted_and_plain_are_one_name():
    assert _err('[a = 1, #"a" = 2]') == (("The field named 'a' is already defined in this scope.", 8, 12), False)


def test_duplicate_record_type_field():
    assert _err("type [a = number, a = text]") == (
        ("The field named 'a' is already defined in this scope.", 18, 19), False)


def test_duplicate_function_type_parameter():
    assert _err("type function (x as number, x as text) as any") == (
        ("The parameter name 'x' is a duplicate.", 28, 29), False)


def test_outermost_duplicate_first():
    # The let's duplicate is reported before the record's, though the record's comes first in the text.
    assert _err("let a = [x = 1, x = 2], a = 1 in a") == (
        ("The variable named 'a' is already defined in this scope.", 24, 25), False)


def test_verbatim_that_reads():
    msg = 'Consider removing the verbatim literal brackets ( #!" ... " ) to enable evaluation of the bracketed expression.'
    assert _err('#!"1 + 1"') == ((msg, 0, 9), False)


def test_verbatim_that_does_not_read_is_no_error():
    assert _err('#!"1 +"') == (None, True)


def test_unpaired_surrogate_in_a_variable_name():
    assert _err('let #"a\ud800" = 1 in 1') == (("There is an invalid sequence in identifier.", 4, 9), False)


# --- what is stored ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, stored", [
    ("1 + 1", "1 + 1"),
    ("let a = ", '#!"let a = "'),
    ('say "hi"', '#!"say ""hi"""'),
    ("section S; a = 1;", '#!"section S; a = 1;"'),
    ("a // c", "a // c\r\n"),
    ("a /* c */", "a /* c */"),
])
def test_escape_source_text(text, stored):
    assert escape_source_text(text) == stored


def test_json_shape():
    assert check_query("let a = 1 b = 2 in a").to_json() == {
        "sourceError": {"text": "Token ',' expected.", "startPosition": 10, "endPosition": 11,
                        "startRow": 0, "startColumn": 10, "endRow": 0, "endColumn": 11},
        "canCommit": True,
    }
