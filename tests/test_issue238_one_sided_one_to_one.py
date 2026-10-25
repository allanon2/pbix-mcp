"""Issue #238: a one-to-one relationship whose keys only one side lacks gave
BOTH sides a blank row.

With A1(k) = 5, 2 one-to-one B1(k, z) = (5, "p"), B1 lacks A1's 2, so B1 has
a blank row standing for A1's row 2. blank_row_tables then carried that blank
row back across the same relationship, as it carries a blank row one level up
a snowflake, and gave A1 one too -- although every key of B1 is A1's.
COUNTROWS(VALUES(A1[k])) was 3, and under A1[k] <> 2 A1's (kept) blank row
selected B1's blank row: CALCULATE(COUNTROWS(VALUES(B1[z])), A1[k] <> 2) was
2. A blank row now crosses every relationship but the 1:1 it came across.

Expected values:
- DESKTOP: Power BI Desktop 2.152 over ADOMD (build_b234.py's o_* probes),
  the model built below.
- The one-to-one fuzz: a reference that joins A and B fully outer, each
  side's blank row standing in for the keys the other side holds and it
  lacks (and pairing with the other's blank row when both have one). A filter
  on either table filters that join -- each table's expanded table is all of
  it -- a table filter FILTER(T, c) keeping the pairs whose T part is a kept
  real row of T, a column predicate taking in T's blank row too. It is the
  rule Desktop shows above and for issue #227's FC / EC (build_b210.py:
  keys lacking on both sides give both sides a blank row).
- The model fuzz: a table has a blank row when a row of some table's
  EXPANDED table (the one sides it reaches many-to-one, both ends of a 1:1,
  transitively) reaches it with no partner, its key or the blank keys of a
  blank row matching none of the table's -- the reading under issue #82's
  Desktop measurements (an orphan key gives the one side a blank row, which
  carries one level up a snowflake) and the cases above. A blank row that
  came from elsewhere still crosses a 1:1 in the middle of a snowflake; no
  Desktop measurement reaches that case.
"""
from __future__ import annotations

import random

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

A1 = [5, 2]
B1 = [(5, "p")]
DESKTOP = {
    "o_values_a": ("COUNTROWS(VALUES(A1[k]))", 2),
    "o_values_bk": ("COUNTROWS(VALUES(B1[k]))", 2),
    "o_values_bz": ("COUNTROWS(VALUES(B1[z]))", 2),
    "o_bz_under_a": ("CALCULATE(COUNTROWS(VALUES(B1[z])), A1[k] <> 2)", 1),
    "o_a_under_b": ('CALCULATE(COUNTROWS(A1), B1[z] = "p")', 1),
    "o_rows_a": ("COUNTROWS(A1)", 2),
}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The Desktop model through the builder and reader: the relationship's
    cardinalities and direction come from the file."""
    import warnings

    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    warnings.simplefilter("ignore")
    path = str(tmp_path_factory.mktemp("i238") / "b238.pbix")
    b = PBIXBuilder("b238")
    b.add_table("A1", [{"name": "k", "data_type": "Int64"}], rows=[{"k": k} for k in A1])
    b.add_table("B1", [{"name": "k", "data_type": "Int64"}, {"name": "z", "data_type": "String"}],
                rows=[{"k": k, "z": z} for k, z in B1])
    b.add_relationship("A1", "k", "B1", "k", from_cardinality=1, to_cardinality=1, cross_filter_behavior=2)
    b.add_page("Page 1")
    b.save(path)
    S.pbix_open(path, "i238")
    try:
        ctx = S._get_dax_context("i238")
    finally:
        S.pbix_close("i238")
    return ctx


def test_matches_desktop(built):
    measures = {k: dax for k, (dax, _w) in DESKTOP.items()}
    defs = dict(built["measure_defs"], **measures)
    got = de.evaluate_measures_smart(
        list(measures), built["tables"], defs, {}, built["date_table"], built["date_column"],
        built["relationships"], simulate_row_context=False,
        measure_tables={k: "A1" for k in measures}, model_columns=built.get("model_columns"),
        date_tables=built.get("date_tables"))
    assert {k: got.get(k) for k in DESKTOP} == {k: want for k, (_d, want) in DESKTOP.items()}


def test_only_the_side_that_lacks_a_key_has_a_blank_row(built):
    dc = de.DAXContext(built["tables"], built["measure_defs"], None, None, {}, built["relationships"])
    assert dc.blank_row_tables() == {"B1"}


def _one_to_one(ft, tt, fcol="k", tcol="k"):
    return {"FromTable": ft, "FromColumn": fcol, "ToTable": tt, "ToColumn": tcol, "IsActive": 1,
            "FromCardinality": 1, "ToCardinality": 1, "CrossFilteringBehavior": 2}


def _many_to_one(ft, fcol, tt, tcol, both=False):
    return {"FromTable": ft, "FromColumn": fcol, "ToTable": tt, "ToColumn": tcol, "IsActive": 1,
            "FromCardinality": 2, "ToCardinality": 1, "CrossFilteringBehavior": 2 if both else 1}


def _batch(measures: dict, tables: dict, rels: list) -> dict:
    return de.evaluate_measures_batch(list(measures), tables, dict(measures), {}, relationships=rels)


@pytest.mark.parametrize("declared", ["A1->B1", "B1->A1"])
def test_either_way_round(declared):
    """A one-to-one is the same declared either way round."""
    tables = {"A1": {"columns": ["k"], "rows": [[k] for k in A1]},
              "B1": {"columns": ["k", "z"], "rows": [list(r) for r in B1]}}
    ft, tt = declared.split("->")
    got = _batch({k: dax for k, (dax, _w) in DESKTOP.items()}, tables, [_one_to_one(ft, tt)])
    assert got == {k: want for k, (_d, want) in DESKTOP.items()}


# ---- the one-to-one pair against the full outer join ------------------------------------

BLANK = "<blank>"


def _pairs(A: dict, B: dict) -> list:
    ak = {r[0] for r in A["rows"]}
    bk = {r[0]: j for j, r in enumerate(B["rows"])}
    out = [(i, bk.get(r[0], BLANK)) for i, r in enumerate(A["rows"])]
    out += [(BLANK, j) for j, r in enumerate(B["rows"]) if r[0] not in ak]
    if any(r[0] not in ak for r in B["rows"]) and any(r[0] not in bk for r in A["rows"]):
        out.append((BLANK, BLANK))
    return out


def _cell(tbl: dict, i, col: str):
    return None if i == BLANK else tbl["rows"][i][tbl["columns"].index(col)]


def _keeps(tbl: dict, i, cond: tuple) -> bool:
    op, col, val = cond
    if op == "true":
        return True
    x = _cell(tbl, i, col)
    if op == ">":
        return x is not None and x > val
    if op == "<>":
        return x != val
    return x == val


def _dax_cond(t: str, cond: tuple) -> str:
    op, col, val = cond
    if op == "true":
        return "1 = 1"
    lit = f'"{val}"' if isinstance(val, str) else str(val)
    return f"{t}[{col}] {op} {lit}"


def _reference(tables: dict, flt: tuple | None, agg: tuple):
    side = {"A": 0, "B": 1}
    P = _pairs(tables["A"], tables["B"])
    if flt is not None:
        kind, t, cond = flt
        P = [p for p in P if (kind == "pred" or p[side[t]] != BLANK) and _keeps(tables[t], p[side[t]], cond)]
    akind, t, col = agg
    parts = {p[side[t]] for p in P}
    real = [i for i in parts if i != BLANK]
    if akind == "count":
        return len(real) or None
    if akind == "sum":
        return sum(_cell(tables[t], i, col) for i in real) if real else None
    return len({_cell(tables[t], i, col) for i in parts}) or None


def _pair_case(rng):
    A = {"columns": ["k", "n", "v"],
         "rows": [[k, rng.choice("xy"), rng.randint(1, 30)] for k in rng.sample(range(1, 6), rng.randint(1, 4))]}
    B = {"columns": ["k", "z", "w"],
         "rows": [[k, rng.choice("pq"), rng.randint(1, 30)] for k in rng.sample(range(1, 6), rng.randint(1, 4))]}
    tables = {"A": A, "B": B}
    measures, want = {}, {}
    for t in "AB":
        for c in ("k", tables[t]["columns"][1]):
            measures[f"values_{t}_{c}"] = f"COUNTROWS(VALUES({t}[{c}]))"
            want[f"values_{t}_{c}"] = _reference(tables, None, ("values", t, c))
    for j in range(14):
        t = rng.choice("AB")
        num, txt = tables[t]["columns"][2], tables[t]["columns"][1]
        cond = rng.choice([("true", None, None), (">", num, rng.randint(1, 30)), ("<>", "k", rng.randint(1, 6)),
                           ("=", txt, rng.choice("xypq")), ("=", "k", rng.randint(1, 6))])
        kind = "table" if cond[0] == "true" else rng.choice(["table", "pred"])
        at = rng.choice("AB")
        acols = tables[at]["columns"]
        agg = rng.choice([("count", at, None), ("sum", at, acols[2]), ("values", at, acols[1]),
                          ("values", at, "k")])
        adax = {"count": f"COUNTROWS({at})", "sum": f"SUM({at}[{agg[2]}])",
                "values": f"COUNTROWS(VALUES({at}[{agg[2]}]))"}[agg[0]]
        fdax = f"FILTER({t}, {_dax_cond(t, cond)})" if kind == "table" else _dax_cond(t, cond)
        measures[f"m{j}"] = f"CALCULATE({adax}, {fdax})"
        want[f"m{j}"] = _reference(tables, (kind, t, cond), agg)
    return tables, measures, want


@pytest.mark.parametrize("seed", range(60))
def test_one_to_one_matches_the_outer_join(seed):
    tables, measures, want = _pair_case(random.Random(238_000 + seed))
    got = _batch(measures, tables, [_one_to_one("A", "B")])
    assert {k: got[k] for k in want} == want, (tables, measures)


# ---- whole models: the blank rows the expanded tables need ------------------------------


def _expanded_blank_rows(tables: dict, rels: list) -> set:
    """The tables some real row's expanded table reaches with no partner."""
    adj: dict = {}
    for r in rels:
        adj.setdefault(r["FromTable"], []).append((r["FromColumn"], r["ToTable"], r["ToColumn"]))
        if r["FromCardinality"] == 1:
            adj.setdefault(r["ToTable"], []).append((r["ToColumn"], r["FromTable"], r["FromColumn"]))
    out: set = set()
    for t, tbl in tables.items():
        for row in tbl["rows"]:
            todo, seen = [(t, row)], {t}
            while todo:
                cur, crow = todo.pop()
                for col, nxt, ncol in adj.get(cur, ()):
                    if nxt in seen:
                        continue
                    seen.add(nxt)
                    key = None if crow is None else crow[tables[cur]["columns"].index(col)]
                    ni = tables[nxt]["columns"].index(ncol)
                    hit = [r for r in tables[nxt]["rows"] if r[ni] == key]
                    if not hit:
                        out.add(nxt)
                    todo.append((nxt, hit[-1] if hit else None))
    return out


def _model(rng):
    """T0 .. Tn-1 in a chain, each hop T(i) -> T(i+1) many-to-one, T(i+1)
    -> T(i) many-to-one or one-to-one: snowflakes, stars, a 1:1 at either
    end or in the middle. Keys either side lacks, blank keys."""
    n = rng.choice([2, 3, 4])
    tables = {}
    for i in range(n):
        keys = rng.sample([1, 2, 3, 4, 5, None], rng.randint(1, 4))
        tables[f"T{i}"] = {"columns": ["k", "u", "w", "v"],
                           "rows": [[k, rng.choice([1, 2, 3, 4, 5, 6, None]), rng.choice([1, 2, 3, 4, 5, 6, None]),
                                     rng.randint(1, 50)] for k in keys]}
    rels, kinds = [], []
    for i in range(n - 1):
        kind = rng.choice(["down", "up", "1:1", "1:1"])
        a, b = f"T{i}", f"T{i + 1}"
        if kind == "down":
            rels.append(_many_to_one(a, "u", b, "k", both=rng.random() < 0.5))
        elif kind == "up":
            rels.append(_many_to_one(b, "w", a, "k", both=rng.random() < 0.5))
        else:                   # T(i)[u] unique, one-to-one T(i+1)[k]
            vals = rng.sample([1, 2, 3, 4, 5, 6, None], len(tables[a]["rows"]))
            for r, x in zip(tables[a]["rows"], vals):
                r[1] = x
            rels.append(_one_to_one(a, b, "u", "k"))
        kinds.append(kind)
    return tables, rels, kinds


@pytest.mark.parametrize("seed", range(200))
def test_blank_rows_are_the_expanded_tables(seed):
    tables, rels, kinds = _model(random.Random(238_500 + seed))
    want = _expanded_blank_rows(tables, rels)
    dc = de.DAXContext(tables, {}, None, None, {}, rels)
    assert set(dc.blank_row_tables()) == want, kinds
    # and VALUES lists each table's blank row
    measures = {f"n_{t}": f"COUNTROWS(VALUES({t}[k]))" for t in tables}
    got = _batch(measures, tables, rels)
    assert got == {f"n_{t}": len({r[0] for r in tbl["rows"]} | ({None} if t in want else set()))
                   for t, tbl in tables.items()}, kinds


def test_a_blank_row_from_elsewhere_crosses_the_one_to_one():
    """F -> A 1:1 B with every key of A and B matched: F's orphan 9 gives A
    a blank row, which B takes on (F's orphan reads B's columns as BLANK) --
    and nothing beyond B's own many side gives one back."""
    tables = {"F": {"columns": ["k", "q"], "rows": [[1, 1], [9, 2]]},
              "A": {"columns": ["k"], "rows": [[1], [2]]},
              "B": {"columns": ["k", "z"], "rows": [[1, "p"], [2, "q"]]},
              "C": {"columns": ["z"], "rows": [["p"], ["q"]]}}
    rels = [_many_to_one("F", "k", "A", "k"), _one_to_one("A", "B"), _many_to_one("B", "z", "C", "z")]
    assert de.DAXContext(tables, {}, None, None, {}, rels).blank_row_tables() == {"A", "B", "C"}
    got = _batch({"z": "COUNTROWS(VALUES(B[z]))", "blank_q": "CALCULATE(SUM(F[q]), ISBLANK(B[z]))",
                  "c": "COUNTROWS(VALUES(C[z]))"}, tables, rels)
    assert got == {"z": 3, "blank_q": 2, "c": 3}


def test_a_many_side_that_adds_nothing_goes_unscanned(monkeypatch):
    """Finding the blank rows scans a many side only when its blank row
    would reach a table that has none yet. E's blank row (D's 3) came across
    the 1:1, so it does not cross back to D; one from F2 would, but D has
    one already (F1's 9), so F2 -- a fact table, in a real model -- is never
    read."""
    tables = {"F1": {"columns": ["k"], "rows": [[1], [9]]},
              "F2": {"columns": ["k"], "rows": [[1], [2]]},
              "D": {"columns": ["k"], "rows": [[1], [2], [3]]},
              "E": {"columns": ["k"], "rows": [[1], [2]]}}
    rels = [_one_to_one("D", "E"), _many_to_one("F1", "k", "D", "k"), _many_to_one("F2", "k", "E", "k")]
    scanned = []
    real = de.DAXContext._has_unmatched_keys

    def spy(self, many_table, many_col, one_table, one_col):
        scanned.append((many_table, one_table))
        return real(self, many_table, many_col, one_table, one_col)

    monkeypatch.setattr(de.DAXContext, "_has_unmatched_keys", spy)
    assert de.DAXContext(tables, {}, None, None, {}, rels).blank_row_tables() == {"D", "E"}
    assert ("F2", "E") not in scanned and len(scanned) == len(set(scanned))


def test_no_blank_row_cascades_from_the_way_back():
    """T0 1:1 T1 -> T2: T1's 3 lacks a partner in T0, so only T0 has a blank
    row. Carried back to T1 it went on up to T2 as well."""
    tables = {"T0": {"columns": ["k"], "rows": [[1], [2]]},
              "T1": {"columns": ["k", "up"], "rows": [[1, 10], [2, 20], [3, 10]]},
              "T2": {"columns": ["up"], "rows": [[10], [20]]}}
    rels = [_one_to_one("T0", "T1"), _many_to_one("T1", "up", "T2", "up")]
    assert de.DAXContext(tables, {}, None, None, {}, rels).blank_row_tables() == {"T0"}
    got = _batch({f"n_{t}": f"COUNTROWS(VALUES({t}[{c}]))" for t, c in (("T0", "k"), ("T1", "k"), ("T2", "up"))},
                 tables, rels)
    assert got == {"n_T0": 3, "n_T1": 3, "n_T2": 2}
