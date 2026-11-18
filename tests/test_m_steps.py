"""query_steps: the applied steps Power Query's editor lists for a query's text.

The first case is measured on Power BI Desktop 2.157 (2026-11-18): a synthetic workbook query that Desktop's editor
listed as Source, Navigation, Promoted, Typed, opened on Typed. The others are Power Query's rules, case by case.
"""
import pytest

from pbix_mcp.m.check import NoCheckResult, query_steps

ORDERS = ('let\n    Source = Excel.Workbook(File.Contents("C:\\\\data\\\\sales.xlsx"), null, true),\n'
          '    Orders_Sheet = Source{[Item="Orders",Kind="Sheet"]}[Data],\n'
          '    Promoted = Table.PromoteHeaders(Orders_Sheet, [PromoteAllScalars=true]),\n'
          '    Typed = Table.TransformColumnTypes(Promoted,{{"id", Int64.Type}, {"amount", type number}})\nin\n    Typed')


def shown(q):
    return [("Navigation" if s.navigation else s.name) for s in q.steps if not s.hidden]


def test_measured_on_desktop():
    q = query_steps(ORDERS)
    assert q.has_steps
    assert [s.name for s in q.steps] == ["Source", "Orders_Sheet", "Promoted", "Typed"]
    assert shown(q) == ["Source", "Navigation", "Promoted", "Typed"]
    assert q.selected == 3
    assert q.steps[3].text.startswith("Table.TransformColumnTypes(Promoted,")
    assert q.steps[1].text == 'Source{[Item="Orders",Kind="Sheet"]}[Data]'
    assert ORDERS[q.steps[1].start:q.steps[1].end] == q.steps[1].text


@pytest.mark.parametrize("text", [
    "1",
    "let a = 1 in a + 1",          # the body isn't a name
    "let a = 1 in @a",             # an inclusive name
    "let a = 1, b = 2 in a",       # not the last variable
    "let in 1",                    # no variables
    "let a = ",                    # doesn't read: stored as a verbatim literal
])
def test_no_steps(text):
    q = query_steps(text)
    assert (q.has_steps, q.steps, q.selected) == (False, [], -1)


def test_names_unescaped_and_the_body_compared_by_name():
    q = query_steps('let #"Changed Type" = 1, b = 2, #"b c" = 3 in #"b c"')
    assert [s.name for s in q.steps] == ["Changed Type", "b", "b c"]
    q = query_steps('let #"a" = 1 in a')
    assert [s.name for s in q.steps] == ["a"]


def test_a_navigation_run_shows_as_its_last_step():
    q = query_steps("let Source = S, A = Source{0}, B = A{[k = 1]}[Data], C = B[x], D = Table.F(C) in D")
    assert [(s.name, s.hidden, s.navigation) for s in q.steps] == [
        ("Source", False, False), ("A", True, False), ("B", True, False), ("C", False, True), ("D", False, False)]


def test_no_folding_when_the_second_step_isnt_navigation():
    q = query_steps("let S = 1, T = Table.F(S), U = T{0} in U")
    assert [(s.hidden, s.navigation) for s in q.steps] == [(False, False)] * 3


def test_every_step_navigation():
    q = query_steps("let S = X, T = S[a] in T")
    assert shown(q) == ["S", "Navigation"]


@pytest.mark.parametrize("expr, nav", [
    ("S{[a = 1, b = \"x\"]}[Data]", True),
    ("S{0}[Data]", True),
    ("S[Data]", True),
    ("S{0}", True),
    ("(S){0}", True),                  # an item with a constant key: any collection
    ("Table.F(S){0}", True),
    ("S{[a]}[Data]", False),           # [a] is _'s field, not a record
    ("S{-1}", False),                  # -1 is no constant: a negation
    ("Table.F(S)[Data]", False),       # the field's target isn't a name
    ("(S){0}[Data]", False),           # the item's collection isn't a name
    ("S{x}[Data]", False),
    ("S[[a], [b]]", False),            # a projection
])
def test_navigation_expressions(expr, nav):
    q = query_steps(f"let S = 1, N = {expr}, Z = Table.F(N) in Z")
    assert q.steps[1].navigation is nav


def test_descriptions_are_leading_comments():
    text = "let\r\n    // first\r\n    /* two\r\n   lines */\r\n    A = 1,\r\n    // b\r\n    B = 2, // after\r\n    C = 3\r\nin C"
    q = query_steps(text)
    assert [s.description for s in q.steps] == ["first\r\ntwo\r\nlines", "b", "after"]


def test_a_step_without_comments_has_none():
    assert query_steps("let A = 1 in A").steps[0].description == ""


def test_text_as_written_with_its_inner_comments():
    q = query_steps("let A = 1 /* c */ + 2, B = A in B")
    assert q.steps[0].text == "1 /* c */ + 2"


def test_a_verbatim_step_shows_its_text_and_its_error():
    q = query_steps('let A = 1, B = #!"1 +" in B')
    b = q.steps[1]
    assert b.text == "1 +"
    assert (b.error.text, b.error.startPosition, b.error.endPosition) == ("Token Literal expected.", 3, 3)
    assert q.selected == 1


def test_a_verbatim_step_that_reads_has_no_error():
    q = query_steps('let A = #!"1 + 1", B = A in B')
    assert (q.steps[0].text, q.steps[0].error, q.selected) == ("1 + 1", None, 1)


def test_offsets_are_utf16():
    q = query_steps('let #"\U0001F600" = 1, b = 2 in b')
    s = q.steps[0]
    assert (s.name, s.name_start, s.name_end) == ("\U0001F600", 4, 9)
    assert q.steps[1].name_start == 15


def test_opens_on_the_first_step_with_an_error_not_the_last():
    q = query_steps('let A = 1, B = #!"1 +", C = #!"2 +", D = A in D')
    assert q.selected == 1


# --- a review's findings (2026-11-19) and the gaps it showed ---------------------------------------------------

def test_no_steps_keeps_the_items_error():
    q = query_steps("let a = ")             # stored as #!"let a = ": the item's error is the text's
    assert (q.has_steps, q.error.text) == (False, "Token Literal expected.")
    q = query_steps('#!"1 +"')
    assert (q.error.text, q.error.startPosition) == ("Token Literal expected.", 3)
    assert query_steps("1 + 1").error is None


def test_a_name_twice_answers_with_its_first():
    q = query_steps('let a = 1, a = #!"1 +" in a')
    assert [(s.text, s.error) for s in q.steps] == [("1", None), ("1", None)]
    assert q.selected == 0
    q = query_steps("let S = 1, N = X[a], N = F(1), Z = N in Z")
    assert [s.navigation for s in q.steps] == [False, False, True, False]   # the second N answers as X[a]


@pytest.mark.parametrize("text", ["let a = #!1, b = a in b", "#!1", "#!null"])
def test_a_verbatim_holding_no_text_gets_no_answer(text):
    with pytest.raises(NoCheckResult):
        query_steps(text)


def test_comments_trimmed_as_net_trims():
    q = query_steps("let\r\n    //\x1cx\x1c  \r\n    A = 1\r\nin A")
    assert q.steps[0].description == "\x1cx\x1c"


def test_block_comments_split_at_crlf_only():
    q = query_steps("let\n    /* a\n   b */\n    A = 1\nin A")
    assert q.steps[0].description == "a\n   b"


@pytest.mark.parametrize("expr, nav", [
    ("[Data]", False),                 # _'s field: an implicit name, no navigation
    ("S{(0)}", False),                 # a parenthesised key is no constant
    ("S{0}?[Data]", True), ("S[Data]?", True), ("S{0}?", True),
    ("@S[Data]", True),                # an inclusive name is a name
    ("Section1!S[Data]", False), ("#shared[Data]", False),
    ("S{true}", True), ("S{null}", True), ('S{"a"}', True), ("S{#infinity}", True),
    ("S{#date(2020, 1, 1)}", False),   # an invocation
])
def test_more_navigation_expressions(expr, nav):
    q = query_steps(f"let S = 1, N = {expr}, Z = Table.F(N) in Z")
    assert q.steps[1].navigation is nav


def test_one_step():
    q = query_steps("let A = 1 in A")
    assert [(s.hidden, s.navigation) for s in q.steps] == [(False, False)] and q.selected == 0
