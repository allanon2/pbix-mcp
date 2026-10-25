"""Issue #234: EXCEPT and INTERSECT compare rows by their values, not by text.

Both functions keyed a row by str(): a single-column row by the text of its
value, so BLANK and the text "None" were one value, and any other row by
the text of the whole row dict, its table and column names included, so two
tables of the same values in differently named columns shared no row. They
now compare rows column by column, by position, each value with its type,
as DISTINCT of a table does (_row_key).

Expected values: Power BI Desktop 2.152 over ADOMD (battery
F:/_desktop_oracle/build_b234.py, measures x_except, x_intersect,
x_except_cat and x_dc; table TA with S = "None", BLANK, "x" and TX with
S = "None", "y"). DISTINCTCOUNT already matched (3) and is pinned here so it
stays so. The other cases follow the DAX reference's rule for these
functions (columns by position, no type coercion) and DISTINCT's text rule
(issue #107); they are not measured.

A row is all its cells (DAXEngine._row_cells): an ADDCOLUMNS or GENERATE
row over a single-column table holds that column as ``__value__`` beside the
columns added, and a first version of this fix read such rows by
_row_values, which dropped the column (and read a GENERATE row's inner
table and column names as values): over M[a] = 1, 2, 3 and M2[c] = 1, 5,
COUNTROWS(EXCEPT(ADDCOLUMNS(VALUES(M[a]), "z", 1), ADDCOLUMNS(VALUES(M2[c]),
"z", 1))) was BLANK; the columns-by-position rule keeps 2 and 3. 0.9.146
compared ``__value__`` alone, which got those right and missed a difference
in an added column, and its DISTINCT of such a table counted 1 row for 3.
NaN is one value to these functions as to VALUES (_distinct_cells; not
measured). BLANK against "" is not measured and not pinned.
"""
from __future__ import annotations

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {
    "TA": {"columns": ["S", "A"], "rows": [["None", 1], [None, 2], ["x", 4]]},
    "TX": {"columns": ["S"], "rows": [["None"], ["y"]]},
    # two tables of the same values in columns of other names
    "P": {"columns": ["k", "n"], "rows": [[1, "a"], [2, "b"], [3, "c"], [3, "c"]]},
    "Q": {"columns": ["id", "label"], "rows": [[2.0, "B"], [3, "c"], [4, "d"]]},
    "N": {"columns": ["v"], "rows": [[1], [2], [3]]},
    # single-column tables under ADDCOLUMNS / GENERATE (review of #234)
    "M": {"columns": ["a", "b"], "rows": [[1, "x"], [2, "y"], [3, "z"], [3, "w"]]},
    "M2": {"columns": ["c"], "rows": [[1], [5]]},
    "DM": {"columns": ["a", "n"], "rows": [[1, "one"], [2, "two"], [3, "three"]]},
    # a NaN cell each, two NaN objects
    "NA": {"columns": ["x"], "rows": [[float("nan")], [1.0]]},
    "NB": {"columns": ["x"], "rows": [[float("nan")], [2.0]]},
}
RELS = [{"FromTable": "M", "FromColumn": "a", "ToTable": "DM", "ToColumn": "a", "IsActive": 1}]


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr}, relationships=RELS)


DESKTOP = [
    ("x_except", "COUNTROWS(EXCEPT(VALUES(TA[S]), VALUES(TX[S])))", 2),
    ("x_intersect", "COUNTROWS(INTERSECT(VALUES(TA[S]), VALUES(TX[S])))", 1),
    ("x_except_cat",
     'CONCATENATEX(EXCEPT(VALUES(TA[S]), VALUES(TX[S])), IF(ISBLANK(TA[S]), "<b>", TA[S]), ",", TA[S])',
     "<b>,x"),
    ("x_dc", "DISTINCTCOUNT(TA[S])", 3),
]


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


def test_intersect_keeps_the_none_text_alone():
    got = _ev('CONCATENATEX(INTERSECT(VALUES(TA[S]), VALUES(TX[S])), IF(ISBLANK(TA[S]), "<b>", TA[S]), ",")')
    assert got == "None"


def test_rows_compare_by_position_not_by_column_name():
    p = 'SELECTCOLUMNS(P, "k", P[k], "n", P[n])'
    q = 'SELECTCOLUMNS(Q, "id", Q[id], "label", Q[label])'
    # (2, "b") is (2.0, "B"): a number by value, text ignoring case; (3, "c") twice, kept twice
    assert _ev(f"COUNTROWS(INTERSECT({p}, {q}))") == 3
    assert _ev(f'CONCATENATEX(EXCEPT({p}, {q}), [n], ",")') == "a"


def test_no_type_coercion():
    # the number 1 is not the text "1", TRUE is not 1
    assert _ev('COUNTROWS(INTERSECT(VALUES(N[v]), {"1", "2"}))') is None
    assert _ev("COUNTROWS(INTERSECT(VALUES(N[v]), {TRUE()}))") is None
    assert _ev("COUNTROWS(INTERSECT(VALUES(N[v]), {1.0, 3}))") == 2


def test_except_keeps_duplicates_of_the_first_table():
    assert _ev('COUNTROWS(EXCEPT(SELECTCOLUMNS(P, "n", P[n]), {"a"}))') == 3
    assert _ev('COUNTROWS(INTERSECT(SELECTCOLUMNS(P, "n", P[n]), {"C"}))') == 2


@pytest.fixture
def text_key_calls(monkeypatch):
    """The texts _text_key is asked for while a test runs."""
    calls = []
    real = de._text_key

    def counting(s):
        calls.append(s)
        return real(s)

    monkeypatch.setattr(de, "_text_key", counting)
    return calls


def test_no_collation_key_for_exact_or_ascii_rows(text_key_calls):
    """An exact match, a row without text, and printable ASCII texts decide
    without a collation key (_rows_in, _RowSet): _text_key costs about 30 us
    per text it has not met, and EXCEPT's misses are the rows it is asked
    for."""
    assert _ev("COUNTROWS(EXCEPT(VALUES(N[v]), {2}))") == 2
    assert _ev("COUNTROWS(INTERSECT(VALUES(TX[S]), VALUES(TX[S])))") == 2
    # "y" and "none" miss the exact values; ASCII texts compare folded
    assert _ev('COUNTROWS(EXCEPT(VALUES(TX[S]), {"none", "Z"}))') == 1
    # rows of several columns take the general path, with the same promise
    p = 'SELECTCOLUMNS(P, "k", P[k], "n", P[n])'
    assert _ev(f"COUNTROWS(INTERSECT({p}, {p}))") == 4
    assert _ev(f'COUNTROWS(EXCEPT({p}, SELECTCOLUMNS(Q, "id", Q[id], "label", Q[label])))') == 1
    assert text_key_calls == []


def test_collation_keys_where_a_text_is_not_ascii(text_key_calls):
    """A text that is not printable ASCII compares in the model's collation,
    as DISTINCT compares it ("ae" is "æ", "Straße" "STRASSE", "äpfel"
    "Äpfel"; not measured on Desktop for EXCEPT / INTERSECT)."""
    assert _ev('COUNTROWS(DISTINCT({"ae", "æ"}))') == 1
    text_key_calls.clear()
    # an ASCII miss needs keys for itself and the other side's non-ASCII texts only
    assert _ev('COUNTROWS(INTERSECT({"ae", "x"}, {"æ", "q"}))') == 1
    assert sorted(text_key_calls) == ["ae", "x", "æ"]
    text_key_calls.clear()
    # a non-ASCII miss needs keys for all the other side's texts
    assert _ev('COUNTROWS(INTERSECT({"Straße"}, {"STRASSE", "q"}))') == 1
    assert sorted(text_key_calls) == ["STRASSE", "Straße", "q"]
    assert _ev('COUNTROWS(EXCEPT({"äpfel", "x"}, {"Äpfel"}))') == 1
    assert _ev('COUNTROWS(INTERSECT({("ae", 1)}, {("æ", 1.0)}))') == 1
    assert _ev('COUNTROWS(INTERSECT({("ae", 1)}, {("æ", 2)}))') is None


def test_ascii_texts_compare_folded_in_the_collation():
    """The assumption _ascii_text / _RowSet rest on, on a sample (the full
    check covered every text of up to three printable ASCII characters):
    two printable ASCII texts have one _text_key exactly when they are one
    text with their ASCII letters folded."""
    chars = [chr(c) for c in range(0x20, 0x7F)]
    texts = chars + [a + b for a in chars for b in "aZ0 -'_~"] + ["co-op", "coop", "co op", "a b", "ab", "ab "]
    by_key: dict = {}
    by_fold: dict = {}
    for t in texts:
        k, f = de._text_key(t), de._column_text_key(t)
        assert by_key.setdefault(k, f) == f, t
        assert by_fold.setdefault(f, k) == k, t
    assert de._ascii_text("Customer 01 -'~") and de._ascii_text("")
    assert not de._ascii_text("æ") and not de._ascii_text("a\tb") and not de._ascii_text("a\x7f")


def test_plain_column_values():
    rows = [{"__table__": "T", "__column__": "C", "__value__": v} for v in (1, 2.5, "a", None)]
    assert de._plain_column_values(rows) == [1, 2.5, "a", None]
    assert de._plain_column_values([]) == []
    # a bool, a date, a row with a column of its own, a row that is no dict: the general path
    assert de._plain_column_values(rows + [{"__table__": "T", "__column__": "C", "__value__": True}]) is None
    assert de._plain_column_values([{"__table__": "T", "__value__": 1, "x": 2}]) is None
    assert de._plain_column_values([{"__table__": "T", "__column__": "C"}]) is None
    assert de._plain_column_values([1, 2]) is None
    assert de._plain_column_values([("__value__",)]) is None


def test_row_key_helpers():
    assert de._row_key([None]) != de._row_key(["None"])
    assert de._row_key(["North"]) == de._row_key(["north"])
    assert de._row_key([1]) == de._row_key([1.0])
    assert de._row_key([1]) != de._row_key(["1"])
    assert de._row_exact_key(["North"]) != de._row_exact_key(["north"])
    assert de._row_exact_key([2, None]) == de._row_key([2.0, None])


# (expression, the columns-by-position answer): EXCEPT / INTERSECT / DISTINCT
# over rows that hold their source column as __value__ beside the columns
# ADDCOLUMNS or GENERATE added
EVERY_COLUMN = [
    ('COUNTROWS(EXCEPT(ADDCOLUMNS(VALUES(M[a]), "z", 1), ADDCOLUMNS(VALUES(M2[c]), "z", 1)))', 2),
    ('COUNTROWS(INTERSECT(ADDCOLUMNS(VALUES(M[a]), "z", 1), ADDCOLUMNS(VALUES(M2[c]), "z", 1)))', 1),
    ('COUNTROWS(EXCEPT(ADDCOLUMNS(VALUES(M[a]), "z", 0), '
     'ADDCOLUMNS(FILTER(VALUES(M[a]), M[a] = 1), "z", 0)))', 2),
    ('COUNTROWS(INTERSECT(ADDCOLUMNS(VALUES(M[a]), "z", 0), '
     'ADDCOLUMNS(FILTER(VALUES(M[a]), M[a] = 1), "z", 0)))', 1),
    ('COUNTROWS(INTERSECT(GENERATE(VALUES(M[a]), ROW("q", 1)), GENERATE(VALUES(M2[c]), ROW("q", 1))))', 1),
    ('COUNTROWS(EXCEPT(GENERATE(VALUES(M[a]), ROW("q", 1)), GENERATE(VALUES(M2[c]), ROW("q", 1))))', 2),
    # the added column counts too: (1, FALSE) is not (1, TRUE)
    ('COUNTROWS(EXCEPT(ADDCOLUMNS(VALUES(N[v]), "z", N[v] > 1), ADDCOLUMNS(VALUES(N[v]), "z", TRUE())))', 1),
    ('COUNTROWS(INTERSECT(GENERATE(VALUES(M[a]), ROW("q", M[a] > 1)), GENERATE(VALUES(M2[c]), ROW("q", TRUE()))))',
     None),
    ('COUNTROWS(DISTINCT(ADDCOLUMNS(VALUES(M[a]), "z", 0)))', 3),
    ('COUNTROWS(DISTINCT(GENERATE(VALUES(M[a]), ROW("q", 0))))', 3),
    ('COUNTROWS(DISTINCT(ADDCOLUMNS(CROSSJOIN(VALUES(M[a]), VALUES(M2[c])), "z", M[a] = 3)))', 6),
    ('COUNTROWS(DISTINCT(SUMMARIZE(M, M[a], "s", 0)))', 3),
    ('COUNTROWS(DISTINCT(SELECTCOLUMNS(VALUES(M[a]), "x", 0)))', 1),
]


@pytest.mark.parametrize("expr,want", EVERY_COLUMN, ids=range(len(EVERY_COLUMN)))
def test_rows_of_addcolumns_and_generate_compare_every_column(expr, want):
    assert _ev(expr) == want


def test_row_cells():
    """DAXEngine._row_cells reads every row shape as its cells, by position."""
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {})
    shapes = {
        'VALUES(M[a])': [1], 'M': [1, "x"], '{(1, 2)}': [1, 2], 'ROW("q", 1, "r", 2)': [1, 2],
        'ADDCOLUMNS(VALUES(M[a]), "z", 9)': [1, 9],
        'ADDCOLUMNS(ADDCOLUMNS(VALUES(M[a]), "z", 9), "w", 8)': [1, 9, 8],
        'ADDCOLUMNS(GENERATESERIES(1, 3), "z", 9)': [1, 9],
        'ADDCOLUMNS(SUMMARIZE(M, M[a]), "z", 9)': [1, 9],
        'SUMMARIZE(M, M[a], M[b], "s", 5)': [1, "x", 5],
        # a SELECTCOLUMNS row carries its source column as lineage only
        'SELECTCOLUMNS(VALUES(M[a]), "x", M[a] * 10)': [10],
        'SELECTCOLUMNS(ADDCOLUMNS(VALUES(M[a]), "z", 9), "z", [z])': [9],
        'ADDCOLUMNS(SELECTCOLUMNS(VALUES(M[a]), "x", M[a] * 10), "z", 9)': [10, 9],
        'ADDCOLUMNS({(1, 2)}, "z", 9)': [1, 2, 9],
        'ADDCOLUMNS(CROSSJOIN(VALUES(M[a]), VALUES(M2[c])), "z", 9)': [1, 1, 9],
        'CROSSJOIN(ADDCOLUMNS(VALUES(M[a]), "z", 9), VALUES(M2[c]))': [1, 9, 1],
        'GENERATE(VALUES(M[a]), ROW("q", 7))': [1, 7],
        'GENERATE(M, ROW("q", 7))': [1, "x", 7],
        'ADDCOLUMNS(GENERATE(VALUES(M[a]), ROW("q", 7)), "z", 9)': [1, 7, 9],
        'GENERATE(ADDCOLUMNS(VALUES(M[a]), "z", 9), ROW("q", 7))': [1, 9, 7],
        'GENERATE(GENERATE(VALUES(M[a]), ROW("q", 7)), ROW("r", 8))': [1, 7, 8],
        'GENERATE(VALUES(M[a]), GENERATE(VALUES(M2[c]), ROW("r", 8)))': [1, 1, 8],
        'GENERATE(CROSSJOIN(VALUES(M[a]), VALUES(M2[c])), ROW("q", 7))': [1, 1, 7],
        'GENERATE(VALUES(M[a]), CROSSJOIN(VALUES(M2[c]), ROW("q", 7)))': [1, 1, 7],
        'CROSSJOIN(GENERATE(VALUES(M[a]), ROW("q", 7)), VALUES(M2[c]))': [1, 7, 1],
        'GENERATE(VALUES(M[a]), {(7, 8)})': [1, 7, 8],
        'GENERATE(SELECTCOLUMNS(VALUES(M[a]), "x", M[a] * 10), ROW("q", 7))': [10, 7],
        'GENERATE(VALUES(M[a]), SELECTCOLUMNS(VALUES(M2[c]), "x", M2[c] * 10))': [1, 10],
    }
    mixed = ['UNION(VALUES(M[a]), VALUES(M2[c]))',
             'UNION(SUMMARIZE(M, M[a]), ADDCOLUMNS(VALUES(M[b]), "a", 1))',
             'UNION(ADDCOLUMNS(VALUES(M[a]), "z", 1), ROW("q", 5, "z", 6))']
    for expr in list(shapes) + mixed:
        rows = eng._eval_expr(expr, ctx)
        if expr in shapes:
            assert de.DAXEngine._row_cells(rows[0]) == shapes[expr], expr
        # read by layout, every row as _row_cells reads it
        assert [list(c) for c in de.DAXEngine._rows_cells(rows)] == [de.DAXEngine._row_cells(r) for r in rows]
    assert de.DAXEngine._row_cells(5) == [5]
    assert de.DAXEngine._row_cells({"__value__": 5}) == [5]
    assert [list(c) for c in de.DAXEngine._rows_cells([5, "ab"])] == [[5], ["ab"]]


def test_rows_of_one_layout_are_read_by_it(monkeypatch):
    """Rows that share a layout are read with one itemgetter
    (DAXEngine._rows_cells), not row by row: _row_cells cost as much as the
    rest of EXCEPT's comparison."""
    calls = []
    real = de.DAXEngine._row_cells.__func__

    def counting(cls, row):
        calls.append(row)
        return real(cls, row)

    monkeypatch.setattr(de.DAXEngine, "_row_cells", classmethod(counting))
    assert _ev('COUNTROWS(EXCEPT(ADDCOLUMNS(VALUES(M[a]), "z", 1), ADDCOLUMNS(VALUES(M2[c]), "z", 1)))') == 2
    assert _ev('COUNTROWS(DISTINCT(ADDCOLUMNS(VALUES(M[a]), "z", 0)))') == 3
    assert _ev('COUNTROWS(INTERSECT(SELECTCOLUMNS(P, "k", P[k], "n", P[n]), '
               'SELECTCOLUMNS(Q, "id", Q[id], "label", Q[label])))') == 3
    assert calls == []
    # rows of two columns (two __column__s) are read one by one
    assert _ev("COUNTROWS(DISTINCT(UNION(VALUES(M[a]), VALUES(M2[c]))))") == 4
    assert len(calls) == 3 + 2


def test_generate_keeps_its_left_rows_kind():
    """GENERATE copies its outer row as ADDCOLUMNS does, of its own kind: a
    row of columns stays one (_generate_outer), so its context transition
    filters no expanded table, as Desktop answers for the rows themselves
    (SUMX(ALL(Orders2[Region], Orders2[Qty]), CALCULATE(COUNTROWS(Dim))) is
    15, build_b117.py, issue #152) and as ADDCOLUMNS over them already
    answered. GENERATE over them is not measured; it answered 4 here."""
    for rows in ('ALL(M[a], M[b])', 'SELECTCOLUMNS(M, "a", M[a], "b", M[b])'):
        bare = _ev(f"SUMX({rows}, CALCULATE(COUNTROWS(DM)))")
        assert bare == 12
        assert _ev(f'SUMX(ADDCOLUMNS({rows}, "q", 1), CALCULATE(COUNTROWS(DM)))') == bare
        assert _ev(f'SUMX(GENERATE({rows}, ROW("q", 1)), CALCULATE(COUNTROWS(DM)))') == bare
    # a row of the table itself still filters its expanded table
    assert _ev('SUMX(GENERATE(M, ROW("q", 1)), CALCULATE(COUNTROWS(DM)))') == 4


def test_nan_is_one_value_as_in_values():
    assert _ev("COUNTROWS(VALUES(NA[x]))") == 2
    assert _ev("COUNTROWS(INTERSECT(VALUES(NA[x]), VALUES(NB[x])))") == 1
    assert _ev("COUNTROWS(EXCEPT(VALUES(NA[x]), VALUES(NB[x])))") == 1
    assert _ev("COUNTROWS(DISTINCT(UNION(VALUES(NA[x]), VALUES(NB[x]))))") == 3
    assert _ev('COUNTROWS(INTERSECT(ADDCOLUMNS(VALUES(NA[x]), "z", 1), ADDCOLUMNS(VALUES(NB[x]), "z", 1)))') == 1
    assert _ev('COUNTROWS(DISTINCT(UNION(ADDCOLUMNS(VALUES(NA[x]), "z", 1), '
               'ADDCOLUMNS(VALUES(NB[x]), "z", 1))))') == 3
    assert de._one_nan([1, "a"]) == [1, "a"]
    a, b = de._one_nan([float("nan"), 2]), de._one_nan([float("nan"), 2])
    assert de._row_key(a) == de._row_key(b) and de._row_exact_key(a) == de._row_exact_key(b)
