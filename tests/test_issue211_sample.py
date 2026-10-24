"""Issue #211: SAMPLE picks the rows Desktop picks.

SAMPLE(n, table, orderBy [, order]...) differed from Power BI Desktop in
several ways:

- the positions rounded halves to even (round()), where Desktop rounds them
  up from a step worked out first in double precision: 3 of 10 were rows 1,
  5, 10 where Desktop picks 1, 6, 10;
- only the first orderBy counted, and its order only when it was ASC or DESC
  -- 1, TRUE and TRUE() descended, every later orderBy was ignored;
- n was truncated (2.5 gave two rows, Desktop three), 0 and negative n
  returned the first row (0 is no rows, a negative n an error), and an n of
  BLANK gave BLANK instead of an empty table;
- an order that is no order word (2, BLANK(), "ASC", a column, or a second
  orderBy where the order belongs) was read as DESC or as the next orderBy;
  Desktop refuses the query, and also SAMPLE(n, table) without any orderBy;
- a BLANK orderBy value sorted before every number; among numbers Desktop
  orders it as 0 (just before a 0), among texts first.

Expected values: Power BI Desktop 2.152 over ADOMD, one EVALUATE ROW("v",
...) per probe over GENERATESERIES / DATATABLE tables only, 173 probes run
twice with the same answers (docs/reverse-engineering/experiments/
2026-10-24-sample.md). The probe ids are the record's. Desktop's choice
among rows tied under every orderBy follows no order the rows show and is not
reproduced, so the tie probes are checked only for what the order keys
decide: the keys of the rows picked.
"""
from __future__ import annotations

import math
from fractions import Fraction

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"D": {"columns": ["x"], "rows": [[1]]}}


def _t(n):
    """1 .. n as the column [v]."""
    return f'SELECTCOLUMNS(GENERATESERIES(1, {n}), "v", [Value])'


GROUPS = 'SELECTCOLUMNS(GENERATESERIES(1, 20), "a", INT(([Value] - 1) / 4) + 1, "v", [Value])'
GROUPS3 = ('SELECTCOLUMNS(GENERATESERIES(1, 20), "a", INT(([Value] - 1) / 4) + 1, '
           '"b", MOD([Value], 2), "v", [Value])')
FRUIT = ('DATATABLE("i", INTEGER, "t", STRING, {{1, "pear"}, {2, "Apple"}, {3, "banana"}, '
         '{4, "cherry"}, {5, "apricot"}, {6, "Date"}, {7, "fig"}, {8, "grape"}, {9, "kiwi"}, '
         '{10, "lemon"}})')
COLL = 'DATATABLE("i", INTEGER, "t", STRING, {{1, "a2"}, {2, "_x"}, {3, "a10"}, {4, "1x"}, {5, "Americas"}})'
DIGITS = 'SELECTCOLUMNS(GENERATESERIES(1, 10), "i", [Value], "t", [Value] & "")'
TEXTBLANK = ('SELECTCOLUMNS(GENERATESERIES(1, 4), "i", [Value], '
             '"t", SWITCH([Value], 1, "c", 2, BLANK(), 3, "a", 4, "b"))')
NUMBLANK = 'SELECTCOLUMNS(GENERATESERIES(1, 9), "i", [Value], "v", IF([Value] = 5, BLANK(), [Value] - 5))'
ZEROBLANK = ('SELECTCOLUMNS(GENERATESERIES(1, 5), "i", [Value], '
             '"v", SWITCH([Value], 1, 2, 2, BLANK(), 3, 0, 4, -1, 5, 1))')
DATES = 'SELECTCOLUMNS(GENERATESERIES(1, 10), "i", [Value], "d", DATE(2024, 1, 1) + [Value] - 1)'
DUPS = 'SELECTCOLUMNS(GENERATESERIES(1, 10), "a", INT(([Value] - 1) / 2))'
CONST = 'SELECTCOLUMNS(GENERATESERIES(1, 10), "i", [Value], "c", 0)'
GROUPS_REV = 'SELECTCOLUMNS(GENERATESERIES(1, 20), "a", INT((20 - [Value]) / 4) + 1, "v", 21 - [Value])'
CASES = 'DATATABLE("i", INTEGER, "t", STRING, {{1, "b"}, {2, "A"}, {3, "a"}, {4, "B"}, {5, "c"}, {6, "C"}})'


def _cat(table, col="v", ordered=True):
    """The picked rows' ``col``, ascending (or in the table's own order)."""
    return f'CONCATENATEX({table}, [{col}], ","' + (f', [{col}], ASC)' if ordered else ')')


def _sample(n, table, *order):
    return f'SAMPLE({n}, {table}, {", ".join(order)})'


def _spaced(n, total, *order):
    """SAMPLE(n, 1 .. total, [v], ASC) as its picked values."""
    return _cat(_sample(n, _t(total), *(order or ("[v]", "ASC"))))


def _count_list(n, table=None):
    """COUNTROWS & ":" & the picked values, so no rows (":") and BLANK read apart."""
    s = _sample(n, table or _t(10), "[v]", "ASC")
    return f'COUNTROWS({s}) & ":" & {_cat(s)}'


def _all_but(total, *left_out):
    return ",".join(str(i) for i in range(1, total + 1) if i not in left_out)


ERR = "ERROR"

DESKTOP = [
    # spacing by position, a half rounding up
    ("pr-spacing-n2-of10", _spaced(2, 10), "1,10"),
    ("pr-spacing-n3-of10", _spaced(3, 10), "1,6,10"),
    ("pr-spacing-n4-of10", _spaced(4, 10), "1,4,7,10"),
    ("pr-spacing-n5-of10", _spaced(5, 10), "1,3,6,8,10"),
    ("pr-spacing-n7-of10", _spaced(7, 10), "1,3,4,6,7,9,10"),
    ("pr-spacing-n10-of10", _spaced(10, 10), "1,2,3,4,5,6,7,8,9,10"),
    ("pr-spacing-n2-of11", _spaced(2, 11), "1,11"),
    ("pr-spacing-n3-of11", _spaced(3, 11), "1,6,11"),
    ("pr-spacing-n4-of11", _spaced(4, 11), "1,4,8,11"),
    ("pr-spacing-n5-of11", _spaced(5, 11), "1,4,6,9,11"),
    ("pr-spacing-n7-of11", _spaced(7, 11), "1,3,4,6,8,9,11"),
    ("pr-spacing-n10-of11", _spaced(10, 11), "1,2,3,4,5,7,8,9,10,11"),
    ("pr-spacing-n2-of20", _spaced(2, 20), "1,20"),
    ("pr-spacing-n3-of20", _spaced(3, 20), "1,11,20"),
    ("pr-spacing-n4-of20", _spaced(4, 20), "1,7,14,20"),
    ("pr-spacing-n5-of20", _spaced(5, 20), "1,6,11,15,20"),
    ("pr-spacing-n7-of20", _spaced(7, 20), "1,4,7,11,14,17,20"),
    ("pr-spacing-n10-of20", _spaced(10, 20), "1,3,5,7,9,12,14,16,18,20"),
    ("pr-spacing-n2-of100", _spaced(2, 100), "1,100"),
    ("pr-spacing-n3-of100", _spaced(3, 100), "1,51,100"),
    ("pr-spacing-n4-of100", _spaced(4, 100), "1,34,67,100"),
    ("pr-spacing-n5-of100", _spaced(5, 100), "1,26,51,75,100"),
    ("pr-spacing-n7-of100", _spaced(7, 100), "1,18,34,51,67,84,100"),
    ("pr-spacing-n10-of100", _spaced(10, 100), "1,12,23,34,45,56,67,78,89,100"),
    ("pr-spacing-n2-of101", _spaced(2, 101), "1,101"),
    ("pr-spacing-n3-of101", _spaced(3, 101), "1,51,101"),
    ("pr-spacing-n4-of101", _spaced(4, 101), "1,34,68,101"),
    ("pr-spacing-n5-of101", _spaced(5, 101), "1,26,51,76,101"),
    ("pr-spacing-n7-of101", _spaced(7, 101), "1,18,34,51,68,84,101"),
    ("pr-spacing-n10-of101", _spaced(10, 101), "1,12,23,34,45,57,68,79,90,101"),
    ("pr-spacing-n2-of912", _spaced(2, 912), "1,912"),
    ("pr-spacing-n3-of912", _spaced(3, 912), "1,457,912"),
    ("pr-spacing-n4-of912", _spaced(4, 912), "1,305,608,912"),
    ("pr-spacing-n5-of912", _spaced(5, 912), "1,229,457,684,912"),
    ("pr-spacing-n7-of912", _spaced(7, 912), "1,153,305,457,608,760,912"),
    ("pr-spacing-n10-of912", _spaced(10, 912), "1,102,203,305,406,507,608,710,811,912"),
    ("pr-spacing-n2-of1000", _spaced(2, 1000), "1,1000"),
    ("pr-spacing-n3-of1000", _spaced(3, 1000), "1,501,1000"),
    ("pr-spacing-n4-of1000", _spaced(4, 1000), "1,334,667,1000"),
    ("pr-spacing-n5-of1000", _spaced(5, 1000), "1,251,501,750,1000"),
    ("pr-spacing-n7-of1000", _spaced(7, 1000), "1,168,334,501,667,834,1000"),
    ("pr-spacing-n10-of1000", _spaced(10, 1000), "1,112,223,334,445,556,667,778,889,1000"),
    ("pr-spacing-n50-of912", _spaced(50, 912),
     "1,20,38,57,75,94,113,131,150,168,187,206,224,243,261,280,298,317,336,354,373,391,410,429,"
     "447,466,484,503,522,540,559,577,596,615,633,652,670,689,707,726,745,763,782,800,819,838,"
     "856,875,893,912"),
    ("pr-spacing-n3502-of912", f'COUNTROWS({_sample(3502, _t(912), "[v]", "ASC")})', "912"),
    ("pr-spacing-n1-of10", _spaced(1, 10), "1"),
    ("pr-spacing-n0-of10", f'COUNTROWS({_sample(0, _t(10), "[v]", "ASC")})', None),
    ("pr-by-position-squares",
     _cat(_sample(4, 'SELECTCOLUMNS(GENERATESERIES(1, 30), "v", [Value] * [Value])', "[v]", "ASC")),
     "1,121,400,900"),
    ("x-nm1-9-of10", _spaced(9, 10), _all_but(10, 5)),
    ("x-nm1-19-of20", _spaced(19, 20), _all_but(20, 10)),
    ("x-nm1-99-of100", _spaced(99, 100), _all_but(100, 51)),
    ("x-nm1-911-of912-missing",
     _cat(f'EXCEPT({_t(912)}, {_sample(911, _t(912), "[v]", "ASC")})'), "456"),
    ("x-nm1-911-of912-count", f'COUNTROWS({_sample(911, _t(912), "[v]", "ASC")})', "911"),
    # the step is a double first: neither exact fractions nor a running sum
    ("x-arith-n13-of14", _spaced(13, 14), _all_but(14, 7)),
    ("x-arith-n15-of16", _spaced(15, 16), _all_but(16, 8)),
    ("x-arith-n23-of24", _spaced(23, 24), _all_but(24, 12)),
    ("x-arith-n29-of35", _spaced(29, 35), _all_but(35, 4, 9, 15, 21, 27, 32)),
    ("x-arith-n23-of50", _spaced(23, 50),
     "1,3,5,8,10,12,14,17,19,21,23,25,28,30,32,34,37,39,41,43,46,48,50"),
    ("x-arith-n45-of50", _spaced(45, 50), _all_but(50, 6, 16, 26, 35, 45)),
    ("x-arith-n59-of60", _spaced(59, 60), _all_but(60, 31)),
    # small tables
    ("x-N2-n1-asc", _spaced(1, 2), "1"),
    ("x-N2-n1-desc", _spaced(1, 2, "[v]", "DESC"), "2"),
    ("x-N2-n2", _spaced(2, 2), "1,2"),
    ("x-N2-n3", _spaced(3, 2), "1,2"),
    ("x-N1-n1", _spaced(1, 1), "1"),
    ("x-N1-n3", _spaced(3, 1), "1"),
    ("x-N0-n3", _count_list(3, f'FILTER({_t(10)}, [v] > 100)'), ":"),
    ("x-N3-n2", _spaced(2, 3), "1,3"),
    # n: rounded half up; BLANK / FALSE none, TRUE one, text read; negative an error
    ("x-n-neg1", _count_list(-1), ERR),
    ("x-n-neg5", _count_list(-5), ERR),
    ("x-n-neg0.5", _count_list(-0.5), ERR),
    ("x-n-0.4", _count_list(0.4), ":"),
    ("x-n-0.5", _count_list(0.5), "1:1"),
    ("x-n-0.6", _count_list(0.6), "1:1"),
    ("x-n-1.5", _count_list(1.5), "2:1,10"),
    ("x-n-2.4", _count_list(2.4), "2:1,10"),
    ("x-n-2.5", _count_list(2.5), "3:1,6,10"),
    ("x-n-2.6", _count_list(2.6), "3:1,6,10"),
    ("x-n-3.5", _count_list(3.5), "4:1,4,7,10"),
    ("x-n-4.5", _count_list(4.5), "5:1,3,6,8,10"),
    ("x-n-9.5", _count_list(9.5), "10:1,2,3,4,5,6,7,8,9,10"),
    ("x-n-10.5", _count_list(10.5), "10:1,2,3,4,5,6,7,8,9,10"),
    ("x-n-blank", _count_list("BLANK()"), ":"),
    ("x-n-text3", _count_list('"3"'), "3:1,6,10"),
    ("x-n-true", _count_list("TRUE()"), "1:1"),
    ("x-n-false", _count_list("FALSE()"), ":"),
    ("x-n-expr2.5", _count_list(f"COUNTROWS({_t(10)}) / 4"), "3:1,6,10"),
    ("x-n-3e9", _count_list(3000000000), "10:1,2,3,4,5,6,7,8,9,10"),
    # the order words; DESC when none is given; anything else refused
    ("pr-dir-desc", _spaced(5, 20, "[v]", "DESC"), "1,6,10,15,20"),
    ("pr-dir-1", _spaced(5, 20, "[v]", "1"), "1,6,11,15,20"),
    ("pr-dir-0", _spaced(5, 20, "[v]", "0"), "1,6,10,15,20"),
    ("pr-dir-default", _spaced(5, 20, "[v]"), "1,6,10,15,20"),
    ("x-dir-asc-lower", _spaced(5, 20, "[v]", "asc"), "1,6,11,15,20"),
    ("x-dir-desc-lower", _spaced(5, 20, "[v]", "desc"), "1,6,10,15,20"),
    ("x-dir-TRUE", _spaced(5, 20, "[v]", "TRUE"), "1,6,11,15,20"),
    ("x-dir-TRUEp", _spaced(5, 20, "[v]", "TRUE()"), "1,6,11,15,20"),
    ("x-dir-TRUEsp", _spaced(5, 20, "[v]", "TRUE ( )"), "1,6,11,15,20"),
    ("x-dir-FALSE", _spaced(5, 20, "[v]", "FALSE"), "1,6,10,15,20"),
    ("x-dir-FALSEp", _spaced(5, 20, "[v]", "FALSE()"), "1,6,10,15,20"),
    ("x-dir-2", _spaced(5, 20, "[v]", "2"), ERR),
    ("x-dir-neg1", _spaced(5, 20, "[v]", "-1"), ERR),
    ("x-dir-1plus0", _spaced(5, 20, "[v]", "1 + 0"), ERR),
    ("x-dir-blank", _spaced(5, 20, "[v]", "BLANK()"), ERR),
    ("x-dir-textASC", _spaced(5, 20, "[v]", '"ASC"'), ERR),
    ("x-dir-col", _spaced(5, 20, "[v]", "[v]"), ERR),
    ("x-n1-desc", _spaced(1, 10, "[v]", "DESC"), "10"),
    ("x-n1-default", _spaced(1, 10, "[v]"), "10"),
    ("x-n1-dir1", _spaced(1, 10, "[v]", "1"), "1"),
    ("x-n1-dir0", _spaced(1, 10, "[v]", "0"), "10"),
    ("x-noorder-count", f"COUNTROWS(SAMPLE(3, {_t(10)}))", ERR),
    ("x-noorder-count-big", f"COUNTROWS(SAMPLE(30, {_t(10)}))", ERR),
    # every orderBy counts, each with its own order, paired by position
    ("pr-ties-second-order-desc", _cat(_sample(5, GROUPS, "[a]", "ASC", "[v]", "DESC")), "4,7,10,14,17"),
    ("pr-ties-first-order-only", _cat(_sample(5, GROUPS, "[a]", "ASC"), "a"), "1,2,3,4,5"),
    ("x-ord-aDESC-vASC", _cat(_sample(5, GROUPS, "[a]", "DESC", "[v]", "ASC")), "4,7,11,14,17"),
    ("x-ord-a0-v1", _cat(_sample(5, GROUPS, "[a]", "0", "[v]", "1")), "4,7,11,14,17"),
    ("x-ord-aTRUE-vFALSE", _cat(_sample(5, GROUPS, "[a]", "TRUE", "[v]", "FALSE")), "4,7,10,14,17"),
    ("x-ord-aASC-v", _cat(_sample(5, GROUPS, "[a]", "ASC", "[v]")), "4,7,10,14,17"),
    ("x-ord-a-vASC", _cat(_sample(5, GROUPS, "[a]", "[v]", "ASC")), ERR),
    ("x-ord-a-v", _cat(_sample(5, GROUPS, "[a]", "[v]")), ERR),
    ("x-ord-v1-a", _cat(_sample(5, GROUPS, "[v]", "1", "[a]")), "1,6,11,15,20"),
    ("x-ord-vASC-aDESC", _cat(_sample(5, GROUPS, "[v]", "ASC", "[a]", "DESC")), "1,6,11,15,20"),
    ("x-ord-three", _cat(_sample(5, GROUPS3, "[a]", "ASC", "[b]", "DESC", "[v]", "ASC")), "1,7,10,14,20"),
    ("x-ord-three-num", _cat(_sample(5, GROUPS3, "[a]", "1", "[b]", "0", "[v]", "1")), "1,7,10,14,20"),
    # expression orderBys
    ("x-expr-neg-asc", _spaced(5, 20, "[v] * -1", "ASC"), "1,6,10,15,20"),
    ("x-expr-neg-desc", _spaced(5, 20, "[v] * -1", "DESC"), "1,6,11,15,20"),
    ("x-expr-unary-1", _spaced(5, 20, "-[v]", "1"), "1,6,10,15,20"),
    ("x-expr-mod7-asc", _spaced(5, 20, "MOD([v] * 7, 20)", "ASC"), "2,10,15,17,20"),
    ("x-expr-mod7-desc", _spaced(5, 20, "MOD([v] * 7, 20)", "DESC"), "2,7,15,17,20"),
    ("x-expr-text-asc", _spaced(5, 20, '"x" & [v]', "ASC"), "1,4,9,14,19"),
    ("x-expr-countrows-var-asc",
     _spaced(5, 20, 'VAR x = [v] RETURN COUNTROWS(FILTER(SELECTCOLUMNS(GENERATESERIES(1, 20), "w", [Value]), '
                    '[w] > x))', "ASC"),
     "1,6,10,15,20"),
    # text in the collation, BLANK, dates, duplicate rows
    ("x-text-n3-asc", _cat(_sample(3, FRUIT, "[t]", "ASC"), "i"), "1,2,7"),
    ("x-text-n4-asc", _cat(_sample(4, FRUIT, "[t]", "ASC"), "i"), "1,2,4,8"),
    ("x-text-n3-desc", _cat(_sample(3, FRUIT, "[t]", "DESC"), "i"), "1,2,6"),
    ("x-text-n3-dir1", _cat(_sample(3, FRUIT, "[t]", "1"), "i"), "1,2,7"),
    ("x-text-coll-n4-asc", _cat(_sample(4, COLL, "[t]", "ASC"), "i"), "1,2,4,5"),
    ("x-digits-n3-asc", _cat(_sample(3, DIGITS, "[t]", "ASC"), "i"), "1,5,9"),
    ("x-digits-n4-asc", _cat(_sample(4, DIGITS, "[t]", "ASC"), "i"), "1,3,6,9"),
    ("x-textblank-n3-asc", _cat(_sample(3, TEXTBLANK, "[t]", "ASC"), "i"), "1,2,4"),
    ("x-textblank-n3-desc", _cat(_sample(3, TEXTBLANK, "[t]", "DESC"), "i"), "1,2,3"),
    ("x-numblank-n2-asc", _cat(_sample(2, NUMBLANK, "[v]", "ASC"), "i"), "1,9"),
    ("x-numblank-n3-asc", _cat(_sample(3, NUMBLANK, "[v]", "ASC"), "i"), "1,5,9"),
    ("x-numblank-n3-desc", _cat(_sample(3, NUMBLANK, "[v]", "DESC"), "i"), "1,5,9"),
    # BLANK next to a 0: BLANK second ascending, fourth descending -- the
    # evidence for "just before a 0" (see _sample_order)
    ("x-zeroblank-n3-asc", _cat(_sample(3, ZEROBLANK, "[v]", "ASC"), "i"), "1,3,4"),
    ("x-zeroblank-n4-desc", _cat(_sample(4, ZEROBLANK, "[v]", "DESC"), "i"), "1,2,4,5"),
    ("x-date-n3-dir1", _cat(_sample(3, DATES, "[d]", "1"), "i"), "1,6,10"),
    ("x-date-n3-default", _cat(_sample(3, DATES, "[d]"), "i"), "1,5,10"),
    ("x-dup-count", f'COUNTROWS({_sample(3, DUPS, "[a]", "ASC")})', "3"),
    ("x-dup-cat", _cat(_sample(3, DUPS, "[a]", "ASC"), "a"), "0,2,4"),
]


def _group(v):
    return (v - 1) // 4 + 1


_CASE_TEXT = {1: "b", 2: "a", 3: "a", 4: "b", 5: "c", 6: "c"}

# (probe, expression, Desktop's answer, the orderBy key of a picked row's
# printed value): rows tied under the orderBy, where only the keys picked are
# Desktop's to compare.
TIES = [
    ("x-tie-const-n2-asc", _cat(_sample(2, CONST, "[c]", "ASC"), "i"), "6,10", lambda i: 0),
    ("x-tie-const-n3-asc", _cat(_sample(3, CONST, "[c]", "ASC"), "i"), "1,6,10", lambda i: 0),
    ("x-tie-const-n5-asc", _cat(_sample(5, CONST, "[c]", "ASC"), "i"), "1,3,6,8,10", lambda i: 0),
    ("x-tie-const-n9-asc", _cat(_sample(9, CONST, "[c]", "ASC"), "i"), "1,2,3,4,6,7,8,9,10", lambda i: 0),
    ("x-tie-const-n2-desc", _cat(_sample(2, CONST, "[c]", "DESC"), "i"), "6,10", lambda i: 0),
    ("x-tie-const-n3-desc", _cat(_sample(3, CONST, "[c]", "DESC"), "i"), "1,6,10", lambda i: 0),
    ("x-tie-const-n5-desc", _cat(_sample(5, CONST, "[c]", "DESC"), "i"), "1,3,6,8,10", lambda i: 0),
    ("x-tie-a-n2-asc", _cat(_sample(2, GROUPS, "[a]", "ASC")), "2,20", _group),
    ("x-tie-a-n3-asc", _cat(_sample(3, GROUPS, "[a]", "ASC")), "2,10,20", _group),
    ("x-tie-a-n4-asc", _cat(_sample(4, GROUPS, "[a]", "ASC")), "2,7,14,20", _group),
    ("x-tie-a-n5-asc", _cat(_sample(5, GROUPS, "[a]", "ASC")), "2,6,10,15,20", _group),
    ("x-tie-a-n6-asc", _cat(_sample(6, GROUPS, "[a]", "ASC")), "2,5,11,12,16,20", _group),
    ("x-tie-a-n10-asc", _cat(_sample(10, GROUPS, "[a]", "ASC")), "1,2,5,7,11,12,14,16,18,20", _group),
    ("x-tie-a-n15-asc", _cat(_sample(15, GROUPS, "[a]", "ASC")),
     "2,3,4,5,6,8,10,11,12,13,15,16,17,19,20", _group),
    ("x-tie-a-n19-asc", _cat(_sample(19, GROUPS, "[a]", "ASC")), _all_but(20, 9), _group),
    ("x-tie-a-n5-desc", _cat(_sample(5, GROUPS, "[a]", "DESC")), "2,6,10,16,20", _group),
    ("x-tie-arev-n5-asc", _cat(_sample(5, GROUPS_REV, "[a]", "ASC")), "1,5,11,15,19", _group),
    ("x-tie-case-n3-asc", _cat(_sample(3, CASES, "[t]", "ASC"), "i"), "2,4,6", _CASE_TEXT.__getitem__),
    ("x-tie-unordered-a-n19", _cat(_sample(19, GROUPS, "[a]", "ASC"), ordered=False),
     "2,3,1,4,5,6,7,8,11,10,12,13,14,15,16,17,18,19,20", _group),
]


def _engine(dax):
    """The measure's answer as ADOMD prints it; "ERROR" when the engine
    reports the measure failed."""
    eng = de.DAXEngine()
    got = eng.evaluate_measure("p", de.DAXContext(TABLES, {"p": dax}))
    if "p" in eng.eval_errors:
        return ERR
    if isinstance(got, float) and got.is_integer():
        got = int(got)
    return None if got is None else str(got)


@pytest.mark.parametrize("probe,expr,want", DESKTOP, ids=[p for p, _e, _w in DESKTOP])
def test_matches_desktop(probe, expr, want):
    assert _engine(expr) == want


@pytest.mark.parametrize("probe,expr,want,key", TIES, ids=[t[0] for t in TIES])
def test_ties_pick_desktops_keys(probe, expr, want, key):
    """Rows tied under every orderBy: the same number of rows, with the
    same orderBy values, as Desktop picks."""
    got = _engine(expr)
    assert got not in (None, ERR)
    assert sorted(key(int(x)) for x in got.split(",")) == sorted(key(int(x)) for x in want.split(","))


def test_order_errors_name_the_function():
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {})
    with pytest.raises(de._DAXEvaluationError, match="ORDER argument in the function SAMPLE"):
        eng._eval_expr(_sample(3, _t(10), "[v]", "2"), ctx)
    with pytest.raises(de._DAXEvaluationError, match="non-negative sample size"):
        eng._eval_expr(_sample(-1, _t(10), "[v]", "ASC"), ctx)
    with pytest.raises(de._DAXEvaluationError, match="minimum argument count for the function is 3"):
        eng._eval_expr(f"SAMPLE(3, {_t(10)})", ctx)


def test_order_pairs():
    pairs = de.DAXEngine._order_pairs
    assert pairs("SAMPLE", ["[a]"]) == [("[a]", True)]
    assert pairs("SAMPLE", [" [a] ", " asc ", "[b]", "true ( )", "[c]", " 0 "]) == [
        ("[a]", False), ("[b]", False), ("[c]", True)]
    assert pairs("SAMPLE", ["[a]", "False()", "[b]"]) == [("[a]", True), ("[b]", True)]
    for bad in (["[a]", "[b]"], ["[a]", "ASC", "[b]", "[c]"], ["[a]", "TRUE(1)"], ["[a]", "'ASC'"]):
        with pytest.raises(de._DAXEvaluationError):
            pairs("SAMPLE", bad)


def test_row_count_arg():
    count = de._row_count_arg
    assert [count(v, 10) for v in (0, 0.4, 0.5, 1.5, 2.5, 2.6, 9.4, 9.5, 10, 10.5, 3e9)] == \
        [0, 0, 1, 2, 3, 3, 9, 10, 10, 10, 10]
    assert count(-2.5, 10) == 0 and count(math.inf, 10) == 10 and count(math.nan, 10) == 0


def test_evenly_spaced():
    """Every position within half a row of i * (total - 1) / (n - 1), the
    ends included, increasing; and the measured half-way cases."""
    spaced = de._evenly_spaced
    for total in range(0, 120):
        for n in range(-2, total + 3):
            got = spaced(n, total)
            assert len(got) == max(0, min(n, total))
            if 2 <= n < total:
                assert got[0] == 0 and got[-1] == total - 1
                assert all(a < b for a, b in zip(got, got[1:]))
                for i, p in enumerate(got):
                    assert abs(p - Fraction(i * (total - 1), n - 1)) <= Fraction(1, 2)
    assert spaced(3, 10) == [0, 5, 9] and spaced(5, 10) == [0, 2, 5, 7, 9]
    assert 26 not in spaced(29, 35) and 25 in spaced(29, 35)      # row 27 left out, not 26
    assert 50 not in spaced(99, 100) and 49 in spaced(99, 100)    # row 51 left out, not 50


def test_chart_samplers_still_delegate():
    """SAMPLECARTESIANPOINTSBYCOVER and SAMPLEAXISWITHLOCALMINMAX take
    SAMPLE's rows below n (their conformance goldens cover n >= the rows)."""
    assert _engine(f"COUNTROWS(SAMPLECARTESIANPOINTSBYCOVER(3, {_t(10)}, [v], [v]))") == "3"
    assert _engine(f"COUNTROWS(SAMPLEAXISWITHLOCALMINMAX(3, {_t(10)}, [v], [v], 1))") == "3"
