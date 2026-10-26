"""Issue #248: a Fixed Decimal (Currency) value keeps its type through reads,
arithmetic and aggregates, rounded as Power BI Desktop rounds it.

What was wrong (0.9.147): only a SUM of a Currency column (and CURRENCY()) was
a Currency (#241). Every arithmetic result on one was an unrounded Double --
CURRENCY(1) / 3 was 0.333333333, CURRENCY(0.0001) * 0.5 0.00005 --, AVERAGE /
AVERAGEX / MEDIAN of a Currency column were unrounded Doubles (AVERAGE(TCur3[v])
0.133333333 where Desktop has the Currency 0.1333), unary minus, MIN / MAX /
MINX / MAXX gave Doubles, and a Currency column read in a row context, through
RELATED, LOOKUPVALUE, VALUES, SELECTEDVALUE or DATATABLE(..., CURRENCY, ...)
was a Double, so SUMX(TCur, TCur[v] * 2) added Doubles (-22.2 in 1E-17 bits).

Desktop holds a Currency as a signed 64-bit count of 1/10,000: Currency +- / *
Integer is exact (wrapping past the range), Currency * Double the count's
Double product rounded half away from zero, Currency / Integer and / Double
the count's Double quotient rounded half away, anything / Currency a Double,
Currency * Currency a Double; AVERAGE the exact total over the count rounded
half away, MEDIAN / PERCENTILE interpolated with a tie rounded up.

Expected values: the DESKTOP strings are Power BI Desktop 2.152's answers over
ADOMD to batteries build_b254.py (B254) and build_b254b.py (B254b), each probe
a packed text of cells built by g / r / ex / t below (verbatim from the
builders): g = type letter + FORMAT(CONVERT(x, DOUBLE), "0.000000000"), r =
type letter + the value in counts of 1/10,000, ex = type letter + (x - c) *
1E17, t = the value's own text. The model is the batteries' (through the
builder, the reader and the server, so the column types come from the file).
The helper checks are exact arithmetic or Desktop's cells, as named.
"""
from __future__ import annotations

import json
import warnings
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit


# ---- the cell builders (build_b254.py, verbatim) -------------------------------------
def tcode(x):
    return (f'IF(ISBLANK({x}), "B", IF(ISDATETIME({x}), "T", IF(ISCURRENCY({x}), "C", '
            f'IF(ISDOUBLE({x}), "D", IF(ISINT64({x}), "I", IF(ISBOOLEAN({x}), "L", '
            f'IF(ISSTRING({x}), "S", "?")))))))')


def g(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE), "0.000000000"))'


def r(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT(CONVERT({x}, DOUBLE) * 10000, "0.00"))'


def ex(x, c):
    return f'IF(ISERROR({x}), "E", {tcode(x)} & FORMAT((CONVERT({x}, DOUBLE) - {c}) * 1E17, "0.0"))'


def tonly(x):
    return f'IF(ISERROR({x}), "E", {tcode(x)})'


def cells(*xs):
    return ' & " " & '.join(xs)


def rows_list(table, cell, order):
    return f'CONCATENATEX({table}, {cell}, ",", {order}, ASC)'


def t(x):          # build_b254b.py
    return f'IFERROR(({x}) & "", "E")'


def texts(*xs):
    return ' & "|" & '.join(t(x) for x in xs)


def in_ids(ids):
    return "{" + ", ".join(map(str, ids)) + "}"


C = "CURRENCY(1.0001)"
C2 = "CURRENCY(2.0002)"
B = "BLANK()"
AVG_SETS = [(9, 8), (11, 8), (13, 8), (7, 8), (5, 8), (11, 7, 8), (11, 6, 8), (5, 9, 8), (3, 8)]
MED_SETS = [(9, 8), (10, 9), (13, 8), (7, 8)]
BIG = 'CURRENCY("900719925474.0993")'          # 2^53 + 1 counts
BIG2 = "CURRENCY(123456789012.3457)"

# ---- the model (build_b254.py, build_b254b.py) ---------------------------------------
TR_ROWS = [(i, (i - 8) / 10000) for i in range(1, 16)]             # counts -7 .. 7
TABLES = {
    "TR": ([("id", "Int64"), ("v", "Decimal")], TR_ROWS),
    "TCur": ([("v", "Decimal"), ("k", "Int64"), ("d", "Double")], [(0.1, 3, 3.0)] * 10),
    "TCur3": ([("v", "Decimal")], [(0.1,), (0.1,), (0.2,)]),
    "T1": ([("v", "Decimal"), ("n", "Decimal"), ("d", "Double")], [(0.0001, -0.0001, 0.5)] * 10),
    "T3": ([("id", "Int64"), ("v", "Decimal"), ("n", "Decimal"), ("d", "Double")],
           [(i, 0.0003, -0.0003, 0.5) for i in range(1, 11)]),
    "FactC": ([("key", "Int64"), ("q", "Int64")], [(1, 3)] * 10),
    "DimC": ([("key", "Int64"), ("price", "Decimal")], [(1, 0.1), (2, 0.2)]),
}
HELPERS = {"h_sum3": "SUM(TCur3[v])", "h_avg3": "AVERAGE(TCur3[v])", "h_cur": "CURRENCY(1.5)"}

# ---- the probes (verbatim; a subset of cells where named) ----------------------------
PROBES = {
    "t_add": cells(g(f"{C} + 2"), g(f"2 + {C}"), g(f"{C} + 0.00002"), g(f"0.00002 + {C}"),
                   g(f"{C} + {C2}"), g(f"{C} + {B}"), g(f"{B} + {C}"), g(f"{C} + 2.0")),
    "t_sub": cells(g(f"{C} - 2"), g(f"2 - {C}"), g(f"{C} - 0.00002"), g(f"0.00002 - {C}"),
                   g(f"{C} - {C2}"), g(f"{C} - {B}"), g(f"{B} - {C}"), g(f"-{C}")),
    "t_mul": cells(g(f"{C} * 3"), g(f"3 * {C}"), g(f"{C} * 0.3"), g(f"0.3 * {C}"),
                   g(f"{C} * {C}"), g(f"{C} * {B}"), g(f"{B} * {C}"), g(f"{C} * 2.0")),
    "t_mul_conv": cells(g("CURRENCY(2) * 0.00004"), g("CURRENCY(10) * 0.33333"), g("CURRENCY(1) / 0.33333"),
                        g("0.00004 * CURRENCY(2)"), g("CURRENCY(0.0001) * 0.9"), g("CURRENCY(1) * 1.00004")),
    "t_div": cells(g("CURRENCY(1) / 3"), g("1 / CURRENCY(3)"), g("CURRENCY(1) / 0.3"), g("0.5 / CURRENCY(3)"),
                   g("CURRENCY(1) / CURRENCY(3)"), g(f"{B} / CURRENCY(2)"), g("CURRENCY(1) / 2.0"),
                   g("SUM(TCur3[v]) / COUNTROWS(TCur3)")),
    "t_divide": cells(g("DIVIDE(CURRENCY(1), 3)"), g("DIVIDE(1, CURRENCY(3))"), g("DIVIDE(CURRENCY(1), 0.3)"),
                      g("DIVIDE(0.5, CURRENCY(3))"), g("DIVIDE(CURRENCY(1), CURRENCY(3))"),
                      g("DIVIDE(CURRENCY(1), 0)"), g("DIVIDE(CURRENCY(1), 0, 2.5)"),
                      g("DIVIDE(SUM(TCur3[v]), COUNTROWS(TCur3))")),
    "t_mixed": cells(g("CURRENCY(1.5) + TRUE()"), g("CURRENCY(1.5) * TRUE()"), g('CURRENCY(1.5) + "2"'),
                     g('CURRENCY(1.5) * "2"'), g("CURRENCY(1.5) + DATE(2024, 1, 1)"),
                     g("DATE(2024, 1, 2) - CURRENCY(0.5)"), g("CURRENCY(1.5) - DATE(2024, 1, 1)")),
    "r_cmul05": rows_list("GENERATESERIES(-7, 7)", r("CURRENCY([Value] / 10000) * 0.5"), "[Value]"),
    "r_mul05": rows_list("TR", r("TR[v] * 0.5"), "TR[id]"),
    "r_div2": rows_list("TR", r("TR[v] / 2"), "TR[id]"),
    "r_mul15": rows_list("TR", r("TR[v] * 1.5"), "TR[id]"),
    "r_mul025": rows_list("TR", r("TR[v] * 0.25"), "TR[id]"),
    "r_div4": rows_list("TR", r("TR[v] / 4"), "TR[id]"),
    "r_div3": rows_list("TR", r("TR[v] / 3"), "TR[id]"),
    "r_divd04": rows_list("TR", r("TR[v] / 0.4"), "TR[id]"),
    "r_divide2": rows_list("TR", r("DIVIDE(TR[v], 2)"), "TR[id]"),
    "r_mulcc": rows_list("TR", r("TR[v] * CURRENCY(0.5)"), "TR[id]"),
    "r_neg": rows_list("TR", r("-TR[v]"), "TR[id]"),
    "r_avg": cells(*[r(f"CALCULATE(AVERAGE(TR[v]), TR[id] IN {in_ids(s)})") for s in AVG_SETS]),
    "r_avgx": cells(*[r(f"CALCULATE(AVERAGEX(TR, TR[v]), TR[id] IN {in_ids(s)})") for s in AVG_SETS]),
    "r_median": cells(*[r(f"CALCULATE(MEDIAN(TR[v]), TR[id] IN {in_ids(s)})") for s in MED_SETS]),
    "r_sumdiv": cells(*[r(f"CALCULATE(SUM(TR[v]) / COUNTROWS(TR), TR[id] IN {in_ids(s)})") for s in AVG_SETS]),
    "x_sumx1": cells(ex("SUMX(TCur, TCur[v] * 2)", "2"), ex("SUMX(TCur, TCur[v] * 1.5)", "1.5"),
                     ex("SUMX(TCur, TCur[v] / 3)", "0.333"), ex("SUMX(TCur, TCur[v] + TCur[v])", "2"),
                     ex("SUMX(TCur, TCur[v] + 1)", "11"), ex("SUMX(TCur, -TCur[v])", "-1")),
    "x_sumx2": cells(ex("SUMX(TCur, TCur[v] * TCur[k])", "3"), ex("SUMX(TCur, TCur[v] * TCur[d])", "3"),
                     ex("SUMX(TCur, DIVIDE(TCur[v], 3))", "0.333"),
                     ex("SUMX(TCur, IF(TCur[v] > 0, TCur[v], 0))", "1"),
                     ex("SUMX(TCur, CURRENCY(TCur[d] / 10))", "3"), ex("SUMX(TCur, TCur[v] - 0.05)", "0.5")),
    "a_col": cells(g("SUM(TCur3[v])"), g("AVERAGE(TCur3[v])"), g("MIN(TCur3[v])"), g("MAX(TCur3[v])"),
                   g("MEDIAN(TCur3[v])"), g("AVERAGEA(TCur3[v])")),
    "a_iter": cells(g("AVERAGEX(TCur3, TCur3[v])"), g("AVERAGEX(TCur3, TCur3[v] * 2)"),
                    g("MEDIANX(TCur3, TCur3[v])"), g("MINX(TCur3, TCur3[v] * 0.5)"), g("MAXX(TCur3, TCur3[v])"),
                    g("PRODUCTX(TCur3, TCur3[v])"), ex("SUMX(VALUES(TCur3[v]), TCur3[v])", "0.3")),
    "l_tables": cells(ex('SUMX(SELECTCOLUMNS(TCur, "w", TCur[v]), [w])', "1"),
                      ex('SUMX(SELECTCOLUMNS(TCur, "w", TCur[v] * 2), [w])', "2"),
                      ex('SUMX(ADDCOLUMNS(TCur, "w", TCur[v]), [w])', "1"),
                      ex('SUMX(ADDCOLUMNS(TCur, "w", TCur[v] + 0.0), [w])', "1"),
                      ex("SUMX(FactC, RELATED(DimC[price]))", "1"),
                      ex("SUMX(FactC, FactC[q] * RELATED(DimC[price]))", "3"),
                      ex("SUMX(SUMMARIZE(TCur3, TCur3[v]), TCur3[v])", "0.3")),
    # l_ctors cells 0, 1 and 5 (2 to 4 are GENERATESERIES's)
    "l_ctors_cur": cells(ex('SUMX(DATATABLE("x", CURRENCY, {{0.1}, {0.2}}), [x])', "0.3"),
                         ex("SUMX({CURRENCY(0.1), CURRENCY(0.2)}, [Value])", "0.3"),
                         g('MAXX(DATATABLE("x", CURRENCY, {{0.00005}}), [x])')),
    "l_scalars": cells(g("LOOKUPVALUE(TR[v], TR[id], 10)"), g("CALCULATE(SELECTEDVALUE(TR[v]), TR[id] = 10)"),
                       g("CALCULATE(VALUES(TR[v]), TR[id] = 10)"), g("MAXX(FILTER(TR, TR[id] = 10), TR[v])"),
                       g("CALCULATE(MAX(TR[v]), TR[id] = 10)"), g("FIRSTNONBLANKVALUE(TR[id], MAX(TR[v]))")),
    "l_rowctx": (rows_list("TR", tonly("TR[v]"), "TR[id]") + ' & "|" & '
                 + rows_list('SELECTCOLUMNS(TR, "w", TR[v], "i", TR[id])', tonly("[w]"), "[i]")),
    "l_measure": cells(g("[h_sum3]"), g("[h_sum3d]"), g("[h_sum3] * 0.5"), g("[h_avg3]"), g("[h_cur]"),
                       g("[h_sum3] / 3")),
    "k_scalar": cells("IF(CURRENCY(0.1) = 0.10001, 1, 0)", "IF(CURRENCY(0.1) < 0.10001, 1, 0)",
                      "IF(0.10001 > CURRENCY(0.1), 1, 0)", "IF(CURRENCY(0.1) = 0.1, 1, 0)",
                      "IF(CURRENCY(0.1) == 0.10001, 1, 0)", "IF(CURRENCY(0.1) IN {0.10001}, 1, 0)",
                      "IF(CURRENCY(0.0001) * 0.5 = 0.0001, 1, 0)", "IF(CURRENCY(0.1) = 0.09996, 1, 0)"),
    "m_concat": 'CONCATENATEX(TR, TR[v], ",", TR[id], ASC)',
    "s_vv": cells(g("SUMX(T1, T1[v] * T1[v])"), ex("SUMX(TCur, TCur[v] * TCur[v])", "0.1")),
    # ---- build_b254b.py
    "a_fe_mul": cells(r("CURRENCY(1) * 0.00015"), r("CURRENCY(1) * 0.00035"),
                      r("CURRENCY(0.0005) * 0.29999999999999993"), r("CURRENCY(1) * -0.00015"),
                      r("CURRENCY(2) * 0.000075"), r("CURRENCY(1) * 0.00045"), r("0.00015 * CURRENCY(1)"),
                      r('CURRENCY(1) * "0.00015"')),
    "a_fe_big": texts(BIG, f"{BIG} * 1", f"{BIG} / 1", f"{BIG} * 1.0", f"{BIG} / 1.0", f"{BIG} + 0",
                      f"{BIG} + CURRENCY(0.0002)", f"{BIG} * 3", f'{BIG} - CURRENCY("900719925474.0992")',
                      f"{BIG2} * 1.0", f"{BIG2} / 1.0", f"{BIG2} * 1"),
    "a_cmp": cells("IF(CURRENCY(0.0003) * 0.5 = CURRENCY(0.0002), 1, 0)",
                   "IF(CURRENCY(1) * 0.00015 = CURRENCY(0.0001), 1, 0)",
                   "IF(CURRENCY(2) = 2, 1, 0)", "IF(CURRENCY(0.1) = CURRENCY(0.1), 1, 0)"),
    "b_fe_row": rows_list("TOPN(1, T3, T3[id], ASC)", r("T3[v] * 0.5"), "T3[id]"),
    "c_divide": cells(g("DIVIDE(CURRENCY(1), 0, 2.55555)"), g("DIVIDE(1, 0, CURRENCY(2.5))"),
                      g("DIVIDE(4, 2, CURRENCY(1))"), g("DIVIDE(CURRENCY(1), 0.0)"), g("DIVIDE(CURRENCY(1), 0, 1E16)"),
                      g("DIVIDE(CURRENCY(0.0003), 2)")),
    "c_div_ok": cells(g("CURRENCY(1) / TRUE()"), g("CURRENCY(3) / 2.0"), g("CURRENCY(3) / -2"),
                      g("-3 / CURRENCY(2)"), g("CURRENCY(1) / 0.3")),
    "f_median": cells(r("CALCULATE(MEDIANX(TR, TR[v]), TR[id] IN {7, 8})"),
                      r("CALCULATE(MEDIANX(TR, TR[v]), TR[id] IN {5, 8})"),
                      r("CALCULATE(MEDIAN(TR[v]), TR[id] IN {5, 8})"), r("PERCENTILEX.INC(TR, TR[v], 0.25)"),
                      r("PERCENTILE.INC(TR[v], 0.25)"), r("CALCULATE(AVERAGEX(TR, TR[v] * 1), TR[id] IN {5, 8})")),
}
RAW = {"raw_avg3": "AVERAGE(TCur3[v])", "raw_div3": "CURRENCY(1) / 3", "raw_half": "CURRENCY(0.0001) * 0.5",
       "raw_big": "CURRENCY(123456789012.3456)", "d0_cc": "CURRENCY(1) / CURRENCY(0)",
       "d0_nc": "-CURRENCY(1) / CURRENCY(0)"}

# ---- Desktop 2.152's answers (B254: open1/b248/desk.json, B254b: open2/b248-currency/desk.json)
DESKTOP = {
    "t_add": "C3.000100000 C3.000100000 D1.000120000 D1.000120000 C3.000300000 C1.000100000 C1.000100000 D3.000100000",
    "t_sub": "C-0.999900000 C0.999900000 D1.000080000 D-1.000080000 C-1.000100000 C1.000100000 C-1.000100000 "
             "C-1.000100000",
    "t_mul": "C3.000300000 C3.000300000 C0.300000000 C0.300000000 D1.000200010 B B C2.000200000",
    "t_mul_conv": "C0.000100000 C3.333300000 C3.000000000 C0.000100000 C0.000100000 C1.000000000",
    "t_div": "C0.333300000 D0.333333333 C3.333300000 D0.166666667 D0.333333333 B C0.500000000 C0.133300000",
    "t_divide": "C0.333300000 D0.333333333 C3.333300000 D0.166666667 D0.333333333 B C2.500000000 C0.133300000",
    "t_mixed": "C2.500000000 C1.500000000 D3.500000000 C3.000000000 T45293.500000000 T45292.500000000 "
               "D-45290.500000000",
    "r_cmul05": "C-4.00,C-3.00,C-3.00,C-2.00,C-2.00,C-1.00,C-1.00,C0.00,C1.00,C1.00,C2.00,C2.00,C3.00,C3.00,C4.00",
    "r_mul05": "C-4.00,C-3.00,C-3.00,C-2.00,C-2.00,C-1.00,C-1.00,C0.00,C1.00,C1.00,C2.00,C2.00,C3.00,C3.00,C4.00",
    "r_div2": "C-4.00,C-3.00,C-3.00,C-2.00,C-2.00,C-1.00,C-1.00,C0.00,C1.00,C1.00,C2.00,C2.00,C3.00,C3.00,C4.00",
    "r_mul15": "C-11.00,C-9.00,C-8.00,C-6.00,C-5.00,C-3.00,C-2.00,C0.00,C2.00,C3.00,C5.00,C6.00,C8.00,C9.00,C11.00",
    "r_mul025": "C-2.00,C-2.00,C-1.00,C-1.00,C-1.00,C-1.00,C0.00,C0.00,C0.00,C1.00,C1.00,C1.00,C1.00,C2.00,C2.00",
    "r_div4": "C-2.00,C-2.00,C-1.00,C-1.00,C-1.00,C-1.00,C0.00,C0.00,C0.00,C1.00,C1.00,C1.00,C1.00,C2.00,C2.00",
    "r_div3": "C-2.00,C-2.00,C-2.00,C-1.00,C-1.00,C-1.00,C0.00,C0.00,C0.00,C1.00,C1.00,C1.00,C2.00,C2.00,C2.00",
    "r_divd04": "C-18.00,C-15.00,C-13.00,C-10.00,C-8.00,C-5.00,C-3.00,C0.00,C3.00,C5.00,C8.00,C10.00,C13.00,"
                "C15.00,C18.00",
    "r_divide2": "C-4.00,C-3.00,C-3.00,C-2.00,C-2.00,C-1.00,C-1.00,C0.00,C1.00,C1.00,C2.00,C2.00,C3.00,C3.00,C4.00",
    "r_mulcc": "D-3.50,D-3.00,D-2.50,D-2.00,D-1.50,D-1.00,D-0.50,D0.00,D0.50,D1.00,D1.50,D2.00,D2.50,D3.00,D3.50",
    "r_neg": "C7.00,C6.00,C5.00,C4.00,C3.00,C2.00,C1.00,C0.00,C-1.00,C-2.00,C-3.00,C-4.00,C-5.00,C-6.00,C-7.00",
    "r_avg": "C1.00 C2.00 C3.00 C-1.00 C-2.00 C1.00 C0.00 C-1.00 C-3.00",
    "r_avgx": "C1.00 C2.00 C3.00 C-1.00 C-2.00 C1.00 C0.00 C-1.00 C-3.00",
    "r_median": "C1.00 C2.00 C3.00 C0.00",
    "r_sumdiv": "C1.00 C2.00 C3.00 C-1.00 C-2.00 C1.00 C0.00 C-1.00 C-3.00",
    "x_sumx1": "C0.0 C0.0 C0.0 C0.0 C0.0 C0.0",
    "x_sumx2": "C0.0 C0.0 C0.0 C0.0 C0.0 D-5.6",
    "a_col": "C0.400000000 C0.133300000 C0.100000000 C0.200000000 C0.100000000 C0.133300000",
    "a_iter": "C0.133300000 C0.266700000 C0.100000000 C0.050000000 C0.200000000 D0.002000000 C0.0",
    "l_tables": "C0.0 C0.0 C0.0 D-11.1 C0.0 C0.0 C0.0",
    "l_ctors_cur": "C0.0 C0.0 C0.000100000",            # l_ctors "C0.0 C0.0 C0.0 I3 I2 C0.000100000", cells 0, 1, 5
    "l_scalars": "C0.000200000 C0.000200000 C0.000200000 C0.000200000 C0.000200000 C-0.000700000",
    "l_rowctx": "C,C,C,C,C,C,C,C,C,C,C,C,C,C,C|C,C,C,C,C,C,C,C,C,C,C,C,C,C,C",
    "l_measure": "C0.400000000 C0.400000000 C0.200000000 C0.133300000 C1.500000000 C0.133300000",
    "k_scalar": "0 1 1 1 0 0 1 0",
    "m_concat": "-0.0007,-0.0006,-0.0005,-0.0004,-0.0003,-0.0002,-0.0001,0,0.0001,0.0002,0.0003,0.0004,0.0005,"
                "0.0006,0.0007",
    "s_vv": "D0.000000100 D2.8",
    "a_fe_mul": "C1.00 C4.00 C1.00 C-1.00 C1.00 C5.00 C1.00 C1.00",
    "a_fe_big": "900719925474.0993|900719925474.0993|900719925474.0992|900719925474.0992|900719925474.0992|"
                "900719925474.0993|900719925474.0995|2702159776422.2979|0.0001|123456789012.3457|"
                "123456789012.3457|123456789012.3457",
    "a_cmp": "1 1 1 1",
    "b_fe_row": "C2.00",
    "c_divide": "C2.555600000 D2.500000000 D2.000000000 B E C0.000200000",
    "c_div_ok": "C1.000000000 C1.500000000 C-1.500000000 D-1.500000000 C3.333300000",
    "f_median": "C0.00 C-1.00 C-1.00 C-3.00 C-3.00 C-2.00",
}
DESKTOP_RAW = {"raw_avg3": 0.1333, "raw_div3": 0.3333, "raw_half": 0.0001, "raw_big": 123456789012.3456,
               "d0_cc": float("inf"), "d0_nc": float("-inf")}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The batteries' model through the builder, the reader and the server."""
    from pbix_mcp import server as S
    from pbix_mcp.builder import PBIXBuilder

    warnings.simplefilter("ignore")
    path = str(tmp_path_factory.mktemp("i248") / "b248.pbix")
    b = PBIXBuilder("b248")
    for name, (cols, rows) in TABLES.items():
        b.add_table(name, [{"name": c, "data_type": ty} for c, ty in cols],
                    rows=[{c: v for (c, _), v in zip(cols, row)} for row in rows])
    b.add_relationship("FactC", "key", "DimC", "key")
    for k, v in HELPERS.items():
        b.add_measure("TCur3", k, v)
    b.add_measure("TCur3", "h_sum3d", "SUM(TCur3[v])", data_type="Decimal")
    for k, v in {**PROBES, **RAW}.items():
        b.add_measure("FactC" if "RELATED(" in v else "TR", k, v)
    b.add_page("Page 1")
    b.save(path)
    assert json.loads(S.pbix_open(path, "i248"))["success"]
    try:
        out = json.loads(S.pbix_evaluate_dax("i248", ",".join({**PROBES, **RAW}), apply_default_filters=False))
        ctx = S._get_dax_context("i248")
    finally:
        S.pbix_close("i248")
    return {x["name"]: x for x in out["results"]}, ctx


@pytest.mark.parametrize("name", list(DESKTOP))
def test_desktop_answers(built, name):
    got, _ctx = built
    assert got[name].get("value") == DESKTOP[name], got[name]


@pytest.mark.parametrize("name", list(DESKTOP_RAW))
def test_desktop_raw_values(built, name):
    got, _ctx = built
    assert got[name].get("value") == DESKTOP_RAW[name] or (
        isinstance(got[name].get("value"), str) and float(got[name]["value"]) == DESKTOP_RAW[name]), got[name]


def test_the_rows_stay_floats(built):
    """Reads are typed; the model's rows keep plain floats, so the row dicts
    an iteration makes of them are not GC-tracked (a dict of str, int and
    float alone is not; one holding a _Currency is)."""
    import gc

    _got, ctx = built
    eng = de.DAXEngine()
    for name in ("TR", "TCur", "TCur3", "T1", "T3", "DimC"):
        tbl = ctx["tables"][name]
        for c in tbl["currency_columns"]:
            i = tbl["columns"].index(c)
            assert all(type(row[i]) is float for row in tbl["rows"])
        dctx = de.DAXContext(ctx["tables"], {}, None, None, None, [])
        rows = eng._eval_tail(name, dctx)
        assert rows and not any(gc.is_tracked(r) for r in rows)
    assert gc.is_tracked({"v": de._Currency(0.1)})


def test_a_calculated_table_stores_floats_and_marks_the_column():
    """A calculated table the engine computes keeps plain floats in its rows
    (the decoder's form of a Currency column) and records the columns that
    held only Currency values, so a read types them again."""
    from pbix_mcp.dax.calc_tables import _convert_dax_result

    rows = [{"__table__": "T", "x": de._Currency(0.1), "y": 1.5},
            {"__table__": "T", "x": None, "y": de._Currency(2.5)}]
    out = _convert_dax_result(rows, {})
    assert out["rows"] == [[0.1, 1.5], [None, 2.5]]
    assert all(type(v) is float for row in out["rows"] for v in row if v is not None)
    assert out["currency_columns"] == frozenset({"x"})          # y also held a Double


# ---- in memory: the same rules over a tables dict -------------------------------------

def _tables():
    tables = {"T": {"columns": ["c", "w", "k", "g"],
                    "rows": [[0.1, 0.1, 1, "a"], [0.1, 0.1, 2, "a"], [0.2, 0.2, 3, "b"]]},
              "D": {"columns": ["g", "p"], "rows": [["a", 0.1], ["b", 0.2]]}}
    de.mark_currency_columns(tables, {"T": ["c"], "D": ["p"]})
    return tables


def _eval(exprs: dict, tables=None) -> dict:
    tables = tables or _tables()
    rels = [{"FromTable": "T", "FromColumn": "g", "ToTable": "D", "ToColumn": "g", "IsActive": 1}]
    eng = de.DAXEngine()
    out = {}
    for k, e in exprs.items():
        ctx = de.DAXContext(tables, {}, None, None, None, rels)
        out[k] = eng._eval_expr(e, ctx)
    return out


def test_reads_of_a_currency_column_are_currency_in_memory():
    got = _eval({
        "row": 'CONCATENATEX(T, IF(ISCURRENCY(T[c]), "C", "D"), "")',
        "bare": 'CONCATENATEX(T, IF(ISCURRENCY([c]), "C", "D"), "")',
        "dbl": 'CONCATENATEX(T, IF(ISCURRENCY(T[w]), "C", "D"), "")',
        "related": 'CONCATENATEX(T, IF(ISCURRENCY(RELATED(D[p])), "C", "D"), "")',
        "lookup": 'IF(ISCURRENCY(LOOKUPVALUE(T[c], T[k], 3)), "C", "D")',
        "selected": 'IF(ISCURRENCY(CALCULATE(SELECTEDVALUE(T[c]), T[k] = 3)), "C", "D")',
        "values": 'IF(ISCURRENCY(CALCULATE(VALUES(T[c]), T[k] = 3)), "C", "D")',
        "conv": "CONVERT(CALCULATE(VALUES(T[c]), T[k] = 3), DOUBLE)",
        "min": 'IF(ISCURRENCY(MIN(T[c])), "C", "D")',
        "maxx": 'IF(ISCURRENCY(MAXX(T, T[c])), "C", "D")',
        "earlier": 'CONCATENATEX(T, IF(ISCURRENCY(MAXX(FILTER(T, T[k] = EARLIER(T[k])), EARLIER(T[c]))), '
                   '"C", "D"), "")',
    })
    assert got == {"row": "CCC", "bare": "CCC", "dbl": "DDD", "related": "CCC", "lookup": "C",
                   "selected": "C", "values": "C", "conv": 0.2, "min": "C", "maxx": "C", "earlier": "CCC"}


def test_arithmetic_and_aggregates_in_memory():
    got = _eval({"avg": "AVERAGE(T[c])", "avgx": "AVERAGEX(T, T[c] * 2)", "sumx": "SUMX(T, T[c] * 2)",
                 "dbl": "AVERAGE(T[w])", "neg": "-MAX(T[c])", "med": "MEDIAN(T[c])"})
    # exact arithmetic: 0.4 / 3 = 0.13333... -> 1333 counts; 0.8 / 3 -> 2667 counts
    assert got["avg"] == 0.1333 and type(got["avg"]) is de._Currency
    assert got["avgx"] == 0.2667 and type(got["avgx"]) is de._Currency
    assert got["sumx"] == 0.8 and type(got["sumx"]) is de._Currency
    assert type(got["dbl"]) is float and got["dbl"] == (0.1 + 0.1 + 0.2) / 3
    assert got["neg"] == -0.2 and type(got["neg"]) is de._Currency
    assert got["med"] == 0.1 and type(got["med"]) is de._Currency



def test_iterations_add_int64_results_beside_currency_exactly():
    """SUMX / AVERAGEX add Int64 results beside computed Currency results as
    counts (Desktop 2.152, build_b254.py x_sumx2), in any order: before the
    first Currency the Int64s' exact sum is taken over, after it each adds
    its count -- the loops keep no list of every Int64 result (review: an
    append per row of SUMX(P, IF(P[k] > 5, 1, 0))). Exact arithmetic: 1 +
    0.1 + 0.2 is 13000 counts, the Currency 1.3 (a Double fold gives
    1.3000000000000003); the average 13000 / 3 counts rounds half away to
    4333; a Double beside them makes the sum a Double."""
    got = _eval({"first": "SUMX({1, CURRENCY(0.1), CURRENCY(0.2)}, [Value])",
                 "middle": "SUMX({CURRENCY(0.1), 1, CURRENCY(0.2)}, [Value])",
                 "last": "SUMX({CURRENCY(0.1), CURRENCY(0.2), 1}, [Value])",
                 "bool": "SUMX({TRUE(), CURRENCY(0.1), CURRENCY(0.2)}, [Value])",
                 "avg": "AVERAGEX({1, CURRENCY(0.1), CURRENCY(0.2)}, [Value])",
                 "ints": "SUMX({1, 2}, [Value])",
                 "dbl": "SUMX({1, CURRENCY(0.25), 0.5}, [Value])",
                 "avg_dbl": "AVERAGEX({1.5, CURRENCY(0.5)}, [Value])"})
    for k in ("first", "middle", "last", "bool"):
        assert (type(got[k]), got[k]) == (de._Currency, 1.3), k
    assert (type(got["avg"]), got["avg"]) == (de._Currency, 0.4333)
    assert (type(got["ints"]), got["ints"]) == (int, 3)
    assert (type(got["dbl"]), got["dbl"]) == (float, 1.75)
    assert (type(got["avg_dbl"]), got["avg_dbl"]) == (float, 1.0)

# ---- the helpers --------------------------------------------------------------------

def _away(x: Fraction) -> int:
    d = Decimal(x.numerator) / Decimal(x.denominator)
    return int(d.to_integral_value(rounding=ROUND_HALF_UP))


def test_div_half_away_is_exact():
    for num in range(-60, 61):
        for den in (-7, -4, -3, -2, -1, 1, 2, 3, 4, 7, 10000):
            assert de._div_half_away(num, den) == _away(Fraction(num, den)), (num, den)


def test_currency_from_a_double_rounds_x10000_half_away():
    # Desktop, build_b254.py r_cur_lit: the counts of CURRENCY(<TD values>)
    values = [0.03125, 0.09375, 0.15625, -0.03125, -0.09375, 0.00005, 0.00015, 0.00025, 1.00005, -0.00005]
    assert [de._cur_count(de._cur_from_double(v)) for v in values] == [313, 938, 1563, -313, -938, 1, 1, 3,
                                                                       10001, -1]
    # build_b254b.py a_fe_conv: 0.00035 is 4 counts, 0.00045 5, 0.00065 7, -0.00035 -4
    assert [de._cur_count(de._cur_from_double(v)) for v in (0.00035, 0.00045, 0.00065, -0.00035)] == [4, 5, 7, -4]


def test_currency_from_text_rounds_the_decimal_half_to_even():
    # Desktop, build_b254.py r_cur_text
    texts_ = ["0.00005", "0.00015", "0.00025", "-0.00005", "-0.00015", "1.00005", "0.03125"]
    assert [de._cur_count(de._cur_from_text(s)) for s in texts_] == [0, 2, 2, 0, -2, 10000, 312]


def test_medians_round_a_tie_up():
    # Desktop, build_b254.py r_median: (0, 1) -> 1, (1, 2) -> 2, (0, 5) -> 3, (-1, 0) -> 0
    for counts, want in (([0, 1], 1), ([1, 2], 2), ([0, 5], 3), ([-1, 0], 0), ([-3, 0], -1)):
        assert de._cur_count(de._cur_percentile(counts, Fraction(1, 2))) == want


def test_wrap_and_exact_counts():
    # Desktop, build_b254.py o_column: 12E18 counts wrap to -6446744073709551616
    assert de._wrap64(12 * 10 ** 18) == -6446744073709551616
    assert de._wrap64(10 ** 19) == 10 ** 19 - 2 ** 64
    big = de._cur_make(9007199254740993)             # 2^53 + 1: kept exactly
    assert de._cur_count(big) == 9007199254740993 and type(big) is de._Currency
    assert de._cur_text(big) == "900719925474.0993"
