"""Issue #241: a Fixed Decimal (Currency) column adds exactly, as Desktop does.

The decoder reads a Currency value -- stored as a whole count of 1/10,000 --
as a plain Double (the count over 10,000), and the tables dict the engine
evaluates carried no data types. SUM, SUMX and AVERAGE over a Currency column
therefore added Doubles, rounding, where Desktop adds the counts exactly: over
ten rows of 0.1, (SUMX(TCur, TCur[v]) - 1) * 1E17 was -11.102230246251565.

The server now records each table's Currency columns from the model's schema
(engine.mark_currency_columns, the table's ``currency_columns``), and SUM,
AVERAGE, AVERAGEA, and SUMX / AVERAGEX over such a column add its values as
whole counts (engine._currency_total). The values stay floats: typed as
_Currency they make every row dict holding one GC-tracked, which slowed
iterations over the table that never read the column by 7-37%.

Expected values:
- DESKTOP: Power BI Desktop 2.152 over ADOMD (battery build_b234.py), TCur =
  a Fixed Decimal column of ten rows of 0.1.
- DOUBLE: the same ten rows as Doubles keep a Double column's rules, through
  the same file: SUM / AVERAGE correctly rounded (issue #246), SUMX left to
  right (Desktop, build_b210.py).
- The rest are derived: an exact sum does not depend on the row order and is
  the Double nearest the exact total (checked against Fraction arithmetic);
  an iterator over any table that hands it the column's own values (FILTER,
  VALUES, a row transition, a bare [v] in the row context) adds the same
  Currency values; AVERAGEA over a column of numbers is AVERAGE; a sum of
  Currency values is Currency, as CURRENCY() is. Arithmetic on a Currency
  value is not measured on Desktop and stays Double arithmetic.
"""
from __future__ import annotations

import json
import operator
import random
import warnings
from fractions import Fraction
from functools import reduce

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

DESKTOP = {
    "c_sum_eq1": ("IF(SUM(TCur[v]) = 1, 1, 0)", 1),
    "c_sum_bits": ("(SUM(TCur[v]) - 1) * 1E17", 0),
    "c_sumx_bits": ("(SUMX(TCur, TCur[v]) - 1) * 1E17", 0),
    "c_avg_bits": ("(AVERAGE(TCur[v]) - 0.1) * 1E18", 0),
}
# the same ten rows as Doubles: SUM / AVERAGE correctly rounded (issue #246),
# SUMX left to right as Desktop adds them (build_b210.py, T10)
DOUBLE = {
    "d_sum_eq1": ("IF(SUM(TDbl[v]) = 1, 1, 0)", 1),
    "d_sum_bits": ("(SUM(TDbl[v]) - 1) * 1E17", 0),
    "d_sumx_bits": ("(SUMX(TDbl, TDbl[v]) - 1) * 1E17", -11.102230246251565),
    "d_avg_bits": ("(AVERAGE(TDbl[v]) - 0.1) * 1E18", 0),
}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The Desktop model through the builder, the reader and the server: the
    column's type comes from the file."""
    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    warnings.simplefilter("ignore")
    path = str(tmp_path_factory.mktemp("i241") / "b241.pbix")
    b = PBIXBuilder("b241")
    b.add_table("TCur", [{"name": "v", "data_type": "Decimal"}, {"name": "k", "data_type": "Int64"}],
                rows=[{"v": 0.1, "k": i} for i in range(10)])
    b.add_table("TDbl", [{"name": "v", "data_type": "Double"}], rows=[{"v": 0.1}] * 10)
    for name, (dax, _want) in {**DESKTOP, **DOUBLE}.items():
        b.add_measure("TCur", name, dax)
    b.add_page("Page 1")
    b.save(path)
    assert json.loads(S.pbix_open(path, "i241"))["success"]
    try:
        out = json.loads(S.pbix_evaluate_dax("i241", ",".join({**DESKTOP, **DOUBLE}),
                                             apply_default_filters=False))
        ctx = S._get_dax_context("i241")
    finally:
        S.pbix_close("i241")
    return {x["name"]: x.get("value") for x in out["results"]}, ctx


@pytest.mark.parametrize("name", list(DESKTOP))
def test_desktop_answers_through_the_server(built, name):
    got, _ctx = built
    assert got[name] == DESKTOP[name][1], f"{name}: {got[name]!r}, Desktop {DESKTOP[name][1]!r}"


@pytest.mark.parametrize("name", list(DOUBLE))
def test_a_double_column_keeps_its_rules(built, name):
    got, _ctx = built
    assert got[name] == DOUBLE[name][1]


def test_the_server_records_only_the_currency_column(built):
    _got, ctx = built
    cur, dbl = ctx["tables"]["TCur"], ctx["tables"]["TDbl"]
    assert cur["currency_columns"] == frozenset({"v"})
    assert "currency_columns" not in dbl
    # the values stay floats: no GC-tracked objects in the rows
    i = cur["columns"].index("v")
    assert all(type(r[i]) is float for r in cur["rows"])


# ---- the engine, over a tables dict ------------------------------------------------------

def _tables(values, extra=None):
    rows = [[v, g] for v, g in zip(values, extra or ["a"] * len(values))]
    tables = {"T": {"columns": ["v", "g"], "rows": rows}}
    assert de.mark_currency_columns(tables, {"T": ["v"]}) == 1
    return tables


def _eval(measures: dict, tables: dict, fc: dict | None = None) -> dict:
    return de.evaluate_measures_batch(list(measures), tables, dict(measures), dict(fc or {}))


def _fold(values):
    """The values added left to right, rounding at every step."""
    return reduce(operator.add, values, 0)


def _exact(values) -> float:
    """The Double nearest the exact total of 4-decimal values."""
    return float(sum(Fraction(str(v)) for v in values))


def test_the_four_desktop_probes_in_memory():
    tables = {"TCur": {"columns": ["v"], "rows": [[0.1]] * 10}}
    de.mark_currency_columns(tables, {"TCur": ["v"]})
    got = _eval({k: dax for k, (dax, _w) in DESKTOP.items()}, tables)
    assert got == {k: w for k, (_d, w) in DESKTOP.items()}


@pytest.mark.parametrize("seed", range(4))
def test_exact_in_any_row_order(seed):
    rng = random.Random(seed)
    values = [round(rng.uniform(-1e6, 1e6), rng.randint(0, 4)) for _ in range(400)]
    measures = {"s": "SUM(T[v])", "x": "SUMX(T, T[v])", "a": "AVERAGE(T[v])",
                "ax": "AVERAGEX(T, T[v])", "aa": "AVERAGEA(T[v])"}
    want = _exact(values)
    first = _eval(measures, _tables(values))
    rng.shuffle(values)
    again = _eval(measures, _tables(values))
    assert first == again
    assert first["s"] == first["x"] == want
    assert first["a"] == first["ax"] == first["aa"] == want / len(values)
    # the data tells exact from row order apart
    assert want != _fold(values)


def test_large_counts_stay_exact():
    # counts up to 2^50: 112,589,990,684.2624 -- still exact
    values = [112589990684.2624, 0.0001, -112589990684.2624, 0.0003] * 50
    got = _eval({"s": "SUM(T[v])", "x": "SUMX(T, T[v])"}, _tables(values))
    assert got["s"] == got["x"] == 0.02
    assert _fold(values) != 0.02


@pytest.mark.parametrize("dax", [
    "SUMX(T, T[v])",
    "SUMX(T, [v])",
    "SUMX('T', 'T'[v])",
    "SUMX(T, t[V])",
    'SUMX(FILTER(T, T[g] <> "z"), T[v])',
    "SUMX(T, CALCULATE(SUM(T[v])))",
    "SUM(t[V])",
], ids=["column", "bare", "quoted", "other-case", "filter", "transition", "sum-other-case"])
def test_iterators_over_the_columns_values(dax):
    values = [0.1] * 10 + [0.2] * 5
    got = _eval({"m": dax}, _tables(values, [f"k{i}" for i in range(15)]))
    assert got["m"] == 2.0                       # in Doubles, row by row: 1.9999999999999998
    assert _fold(values) == 1.9999999999999998


def test_values_and_filters_add_exactly():
    values = [0.1] * 10 + [0.2] * 5
    tables = _tables(values, ["a"] * 10 + ["b"] * 5)
    got = _eval({
        "vals": "SUMX(VALUES(T[v]), T[v])",                       # 0.1 + 0.2
        "filt": "CALCULATE(SUM(T[v]), T[v] = 0.1)",
        "rows": "CALCULATE(COUNTROWS(T), T[v] = 0.2)",
        "grp": 'CALCULATE(SUM(T[v]), T[g] = "a")',
        "per_g": "SUMX(VALUES(T[g]), CALCULATE(SUM(T[v])))",
        "min": "MIN(T[v])", "max": "MAX(T[v])",
    }, tables)
    assert got == {"vals": 0.3, "filt": 1.0, "rows": 5, "grp": 1.0, "per_g": 2.0,
                   "min": 0.1, "max": 0.2}
    assert 0.1 + 0.2 != 0.3                       # the Double sum differs


def test_a_sum_of_currency_is_currency():
    got = _eval({"cur": "IF(ISCURRENCY(SUM(T[v])), 1, 0)",
                 "dbl": "IF(ISDOUBLE(SUM(T[v])), 1, 0)",
                 "x": "IF(ISCURRENCY(SUMX(T, T[v])), 1, 0)"}, _tables([0.1, 0.25]))
    assert got == {"cur": 1, "dbl": 0, "x": 1}


def test_per_dimension_buckets_add_exactly():
    values = [0.1] * 10 + [0.7] * 3
    tables = _tables(values, ["p"] * 10 + ["q"] * 3)
    measures = {"s": "SUM(T[v])", "a": "AVERAGE(T[v])"}
    fast = de.evaluate_per_dimension(["s", "a"], tables, measures, None, "T.g", "T", "g", ["p", "q"])
    assert fast["s"] == {"p": 1.0, "q": 2.1}
    assert fast["a"] == {"p": 0.1, "q": 2.1 / 3}


def test_currency_values_an_iterator_computes_add_exactly():
    # CURRENCY() returns a Currency, so SUMX and AVERAGEX over it add exactly
    tables = {"D": {"columns": ["d"], "rows": [[0.1]] * 10}}
    got = _eval({"x": "SUMX(D, CURRENCY(D[d]))", "ax": "AVERAGEX(D, CURRENCY(D[d]))",
                 "dbl": "SUMX(D, D[d])"}, tables)
    assert got == {"x": 1.0, "ax": 0.1, "dbl": 0.9999999999999999}


def test_doubles_and_mixed_values():
    # Doubles an iterator computes over a Currency table are Doubles: SUMX
    # adds them left to right
    got = _eval({"dbl": 'SUMX(T, IF(T[g] = "a", 0.1, T[v]))'}, _tables([0.1] * 10))
    assert got["dbl"] == 0.9999999999999999
    # a Currency value next to a Double: a Double total, correctly rounded
    for mixed in ([de._Currency(0.1)] * 5 + [0.1] * 5, [0.1] * 5 + [de._Currency(0.1)] * 5):
        assert de._sum_numbers(mixed) == 1.0
        assert type(de._sum_numbers(mixed)) is float
    # Currency values alone: their exact total, a Currency, from any iterable
    assert de._sum_numbers(iter([de._Currency(0.1)] * 10)) == 1.0
    assert type(de._sum_numbers([de._Currency(0.1)] * 10)) is de._Currency


def test_sum_of_no_rows_is_still_blank():
    got = _eval({"e": "CALCULATE(SUM(T[v]), FILTER(T, FALSE()))",
                 "ex": "SUMX(FILTER(T, FALSE()), T[v])",
                 "ea": "CALCULATE(AVERAGE(T[v]), FILTER(T, FALSE()))"}, _tables([0.1] * 3))
    assert got == {"e": None, "ex": None, "ea": None}


def test_mark_currency_columns_records_names_only():
    rows = [[0.1, "x"], [None, "y"], [3, "z"]]
    tables = {"T": {"columns": ["v", "s", "w"], "rows": rows}, "U": {"columns": ["u"], "rows": []}}
    assert de.mark_currency_columns(tables, {"T": ["v", "missing"], "U": ["u"], "Gone": ["v"]}) == 2
    assert tables["T"]["currency_columns"] == frozenset({"v"})
    assert tables["U"]["currency_columns"] == frozenset({"u"})
    assert tables["T"]["rows"] is rows and rows == [[0.1, "x"], [None, "y"], [3, "z"]]
    assert de.mark_currency_columns(tables, {"T": ["w"]}) == 1          # adds to the set
    assert tables["T"]["currency_columns"] == frozenset({"v", "w"})


def test_currency_total_helper():
    assert de._currency_total([0.1] * 10) == 1.0
    assert type(de._currency_total([0.1] * 10)) is de._Currency
    assert de._currency_total([]) == 0.0
    assert de._currency_total([3, -1.5, 0.0001]) == 1.5001
