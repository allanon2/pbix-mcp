"""Issue #227: a table filter over either side of a one-to-one relationship
kept every row of the table again.

FILTER(FC, ...) as a CALCULATE filter filters FC's EXPANDED table, which
across a one-to-one takes in EC. A kept FC row whose key EC lacks relates to
EC's blank row, so EC's columns must keep that blank row. The engine took
every one-to-one hop for a one -> many hop and never selected the next
table's blank row for an unmatched key: EC's columns kept only the matched
rows, and the both-ways cross filter from EC then dropped the unmatched FC row
itself. CALCULATE(SUM(FC[A]), FILTER(FC, 1 = 1)) was 10 where SUM(FC[A]) is
30, and the same from EC's side.

Expected values:
- DESKTOP: Power BI Desktop 2.152 over ADOMD (build_b210.py), the model built
  below: FC(K, N, A) = (1, "xa", 10), (2, "ya", 20) one-to-one EC(K, Z) =
  (1, "p"), (3, "q"), cross filter both ways.
- The rest hold whatever the relationships are, from DAX's own rules: a table
  filter keeps exactly the rows FILTER returns (CALCULATE(SUM(T[x]),
  FILTER(T, c)) is SUMX(FILTER(T, c), T[x]) when nothing else filters T), a
  one-to-one is the same declared either way round, and the data as
  many-to-one answers as it did (Desktop: a table filter on the many side
  keeps the one side's blank row for an orphan key -- issue #163's exp_names
  -- which is what a one-to-one does from both sides).
"""
from __future__ import annotations

import random

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

FC = [(1, "xa", 10), (2, "ya", 20)]
EC = [(1, "p"), (3, "q")]
DESKTOP = [
    ("c11_sum", "SUM(FC[A])", 30),
    ("c11_filter_true", "CALCULATE(SUM(FC[A]), FILTER(FC, 1 = 1))", 30),
    ("c11_rows", "CALCULATE(COUNTROWS(FC), FILTER(FC, FC[A] > 0))", 2),
    ("c11_values_n", "COUNTROWS(VALUES(FC[N]))", 3),
    ("c11_values_z", "COUNTROWS(VALUES(EC[Z]))", 3),
    ("c11_search", 'CALCULATE(SUM(FC[A]), FILTER(FC, SEARCH("a", FC[N]) > 0))', 30),
    ("c11_all", "CALCULATE(SUM(FC[A]), FILTER(ALL(FC), 1 = 1))", 30),
    ("c11_ec_rows", "CALCULATE(COUNTROWS(EC), FILTER(EC, 1 = 1))", 2),
]
# #203's rewrite of CALCULATE(<aggregate of T>, FILTER(T, <condition>)) hands
# SEARCH the blank row's BLANK on this table (issue #210); this issue is the
# table filter the rewrite is checked against.
REWRITE_EXEMPT = {"c11_search"}


def _no_rewrite(mp):
    mp.setattr(de.DAXEngine, "_pushdown_table_filter", lambda self, arg, ctx: None)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The Desktop model, through the builder and reader: the relationship's
    cardinalities and direction come from the file."""
    import warnings

    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    warnings.simplefilter("ignore")
    path = str(tmp_path_factory.mktemp("i227") / "b227.pbix")
    b = PBIXBuilder("b227")
    b.add_table("FC", [{"name": "K", "data_type": "Int64"}, {"name": "N", "data_type": "String"},
                       {"name": "A", "data_type": "Int64"}],
                rows=[{"K": k, "N": n, "A": a} for k, n, a in FC])
    b.add_table("EC", [{"name": "K", "data_type": "Int64"}, {"name": "Z", "data_type": "String"}],
                rows=[{"K": k, "Z": z} for k, z in EC])
    b.add_relationship("FC", "K", "EC", "K", from_cardinality=1, to_cardinality=1, cross_filter_behavior=2)
    b.add_page("Page 1")
    b.save(path)
    S.pbix_open(path, "i227")
    try:
        ctx = S._get_dax_context("i227")
    finally:
        S.pbix_close("i227")
    return ctx


def _smart(ctx, measures: dict) -> dict:
    defs = dict(ctx["measure_defs"], **measures)
    mt = dict(ctx.get("measure_tables") or {}, **{k: "FC" for k in measures})
    return de.evaluate_measures_smart(
        list(measures), ctx["tables"], defs, {}, ctx["date_table"], ctx["date_column"],
        ctx["relationships"], simulate_row_context=False, measure_tables=mt,
        model_columns=ctx.get("model_columns"), date_tables=ctx.get("date_tables"))


@pytest.mark.parametrize("rewrite", [True, False], ids=["rewrite", "table-filter"])
def test_matches_desktop(built, rewrite):
    cases = [c for c in DESKTOP if not (rewrite and c[0] in REWRITE_EXEMPT)]
    with pytest.MonkeyPatch.context() as mp:
        if not rewrite:
            _no_rewrite(mp)
        got = _smart(built, {name: dax for name, dax, _want in cases})
    assert {name: got.get(name) for name, _dax, _want in cases} == {name: want for name, _dax, want in cases}


# ---- the same data in memory, every way the relationship can be declared -----------------

TABLES = {"FC": {"columns": ["K", "N", "A"], "rows": [list(r) for r in FC]},
          "EC": {"columns": ["K", "Z"], "rows": [list(r) for r in EC]}}


def _rel(ft, tt, card, both=True):
    r = {"FromTable": ft, "FromColumn": "K", "ToTable": tt, "ToColumn": "K", "IsActive": 1,
         "FromCardinality": 1 if card == "1:1" else 2, "ToCardinality": 1}
    if both:
        r["CrossFilteringBehavior"] = 2
    return r


def _batch(measures: dict, rels, fc=None, tables=None) -> dict:
    return de.evaluate_measures_batch(list(measures), tables or TABLES, dict(measures), dict(fc or {}),
                                      relationships=rels)


BOTH_SIDES = {
    "fc_sum": ("CALCULATE(SUM(FC[A]), FILTER(FC, 1 = 1))", 30),
    "fc_rows": ("CALCULATE(COUNTROWS(FC), FILTER(FC, FC[A] > 0))", 2),
    "fc_k2": ("CALCULATE(SUM(FC[A]), FILTER(FC, FC[K] = 2))", 20),
    "ec_rows": ("CALCULATE(COUNTROWS(EC), FILTER(EC, 1 = 1))", 2),
    "ec_k3": ("CALCULATE(COUNTROWS(EC), FILTER(EC, EC[K] = 3))", 1),
    # the other side's blank row, which the unmatched kept row relates to
    "fc_ec_values": ("CALCULATE(COUNTROWS(VALUES(EC[Z])), FILTER(FC, 1 = 1))", 2),
    "fc_ec_blank": ("CALCULATE(COUNTROWS(VALUES(EC[Z])), FILTER(FC, FC[K] = 2))", 1),
    "ec_fc_blank": ("CALCULATE(COUNTROWS(VALUES(FC[N])), FILTER(EC, EC[K] = 3))", 1),
    # a row's transition: K = 2 reaches EC's blank row, K = 3 FC's
    "sumx_fc": ("SUMX(FC, CALCULATE(COUNTROWS(VALUES(EC[Z]))))", 2),
    "sumx_ec": ("SUMX(EC, CALCULATE(COUNTROWS(VALUES(FC[N]))))", 2),
    "sumx_fc_sum": ("SUMX(FC, CALCULATE(SUM(FC[A])))", 30),
}


@pytest.mark.parametrize("rewrite", [True, False], ids=["rewrite", "table-filter"])
@pytest.mark.parametrize("declared", ["FC->EC", "EC->FC"])
def test_one_to_one_either_side(declared, rewrite, monkeypatch):
    if not rewrite:
        _no_rewrite(monkeypatch)
    ft, tt = declared.split("->")
    got = _batch({k: dax for k, (dax, _w) in BOTH_SIDES.items()}, [_rel(ft, tt, "1:1")])
    assert got == {k: want for k, (_d, want) in BOTH_SIDES.items()}


def test_kept_row_under_an_outer_filter(monkeypatch):
    """FILTER iterates the rows the outer filter leaves -- here only K = 2,
    which EC lacks -- and the table filter keeps it."""
    _no_rewrite(monkeypatch)
    m = {"v": "CALCULATE(SUM(FC[A]), FILTER(FC, 1 = 1))", "n": "CALCULATE(COUNTROWS(EC), FILTER(EC, 1 = 1))"}
    assert _batch(m, [_rel("FC", "EC", "1:1")], {"FC.N": ["ya"]})["v"] == 20
    assert _batch(m, [_rel("FC", "EC", "1:1")], {"EC.Z": ["q"]})["n"] == 1


@pytest.mark.parametrize("rewrite", [True, False], ids=["rewrite", "table-filter"])
@pytest.mark.parametrize("both", [True, False], ids=["both", "single"])
def test_many_to_one_unchanged(both, rewrite, monkeypatch):
    """The same data as many-to-one: FC's K = 2 is an orphan, EC has a blank
    row and FC none."""
    if not rewrite:
        _no_rewrite(monkeypatch)
    m = {"fc_sum": "CALCULATE(SUM(FC[A]), FILTER(FC, 1 = 1))",
         "fc_rows": "CALCULATE(COUNTROWS(FC), FILTER(FC, FC[A] > 0))",
         "fc_search": 'CALCULATE(SUM(FC[A]), FILTER(FC, SEARCH("a", FC[N]) > 0))',
         "ec_rows": "CALCULATE(COUNTROWS(EC), FILTER(EC, 1 = 1))",
         "fc_ec_values": "CALCULATE(COUNTROWS(VALUES(EC[Z])), FILTER(FC, 1 = 1))",
         "ec_fc": "CALCULATE(SUM(FC[A]), FILTER(EC, 1 = 1))",
         "values_n": "COUNTROWS(VALUES(FC[N]))", "values_z": "COUNTROWS(VALUES(EC[Z]))"}
    got = _batch(m, [_rel("FC", "EC", "m:1", both)])
    assert got == {"fc_sum": 30, "fc_rows": 2, "fc_search": 30, "ec_rows": 2, "fc_ec_values": 2,
                   "ec_fc": 10, "values_n": 2, "values_z": 3}


# ---- chains: a table filter keeps exactly FILTER's rows ---------------------------------


def _chain(rng):
    """T0 -> T1 (-> T2): each hop many-to-one (single or both ways) or
    one-to-one, with keys either side lacks and blank keys."""
    n = rng.choice([2, 3])
    tables, rels = {}, []
    for i in range(n):
        keys = rng.sample(range(1, 7), rng.randint(1, 4))
        tables[f"T{i}"] = {"columns": ["k", "up", "v"],
                           "rows": [[k, rng.choice([1, 2, 3, 4, 5, 6, None]), rng.randint(1, 50)] for k in keys]}
    for i in range(n - 1):
        card = rng.choice(["m:1", "1:1"])
        if card == "1:1":           # unique on both ends
            for r, up in zip(tables[f"T{i}"]["rows"], rng.sample(range(1, 7), len(tables[f"T{i}"]["rows"]))):
                r[1] = up
        rels.append({"FromTable": f"T{i}", "FromColumn": "up", "ToTable": f"T{i + 1}", "ToColumn": "k",
                     "IsActive": 1, "FromCardinality": 1 if card == "1:1" else 2, "ToCardinality": 1,
                     "CrossFilteringBehavior": 2 if card == "1:1" or rng.random() < 0.5 else 1})
    return tables, rels


@pytest.mark.parametrize("seed", range(40))
def test_table_filter_keeps_filters_rows(seed, monkeypatch):
    _no_rewrite(monkeypatch)
    rng = random.Random(227_000 + seed)
    tables, rels = _chain(rng)
    measures, want_of = {}, {}
    for t in tables:
        for j, cond in enumerate(["1 = 1", f"{t}[v] > {rng.randint(1, 50)}", f"{t}[k] <> {rng.randint(1, 6)}",
                                  f"ISBLANK({t}[up])"]):
            measures[f"{t}_{j}"] = f"CALCULATE(SUM({t}[v]), FILTER({t}, {cond}))"
            measures[f"{t}_{j}_want"] = f"SUMX(FILTER({t}, {cond}), {t}[v])"
            measures[f"{t}_{j}_n"] = f"CALCULATE(COUNTROWS({t}), FILTER({t}, {cond}))"
            measures[f"{t}_{j}_n_want"] = f"COUNTROWS(FILTER({t}, {cond}))"
            want_of[f"{t}_{j}"] = f"{t}_{j}_want"
            want_of[f"{t}_{j}_n"] = f"{t}_{j}_n_want"
    got = _batch(measures, rels, tables=tables)
    assert {k: got[k] for k in want_of} == {k: got[w] for k, w in want_of.items()}
