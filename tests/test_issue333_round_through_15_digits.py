"""#333: ROUND, ROUNDUP, ROUNDDOWN, TRUNC, CEILING and FLOOR read
a number as its Double's 15-significant-digit decimal, at every magnitude.

Power BI Desktop answers TRUNC(9007199254740993) - 9007199254740992 with -2,
and so ROUNDDOWN(.., 0), ROUNDUP(.., 0), ROUND(.., 0), CEILING(.., 1) and
FLOOR(.., 1): the Int64 becomes the Double 9007199254740992, which is
9.00719925474099E+15 in 15 significant digits -- the decimal .NET documents
for a Double converted to Decimal. MROUND, QUOTIENT and INT work in
Doubles and give 0. The engine read ROUND's number by its exact spelling
(1), multiplied ROUNDUP / ROUNDDOWN / TRUNC by 10 ** digits in Doubles with
a 1e-9 tolerance (0), and divided CEILING / FLOOR in exact ints (1).

Below 2**53 Desktop reads the number so too (build_b257c.py group J):
TRUNC(0.29, 2) and FLOOR(0.29, 0.01) are 0.29 (the Double product with 100
and the quotient by 0.01 are 28.999999999999996, which gave 0.28),
ROUNDUP(2.0000000001, 0) is 3 (the tolerance gave 2), ROUND(
1.0049999999999997, 2) 1.01 (the exact spelling gave 1), ROUND(1 / 3, 16)
0.333333333333333, ROUNDUP(0.1 + 0.2, 16) 0.3, and TRUNC(1234567890123456)
- 1234567890123450 is 10: an Int64 of 16 digits is rounded to 15 (cutting
gives 0, the exact digits 6).

Types: a Double stays a Double (Desktop: ROUNDUP(1.5, 0) is the Double 2,
TRUNC(-1.5) the Double -1); an Int64 stays an Int64 at any digits --
ROUND(5, 0), ROUNDUP(5, 0) and TRUNC(5), and ROUND(5, 2), ROUNDUP(5, 1) and
TRUNC(5, 1) (build_b257d.py), which we answered as Doubles; and CEILING /
FLOOR are typed as their significance: CEILING(5, 2) and CEILING(2.5, 1)
are Int64s, CEILING(5, 0.5) a Double, FLOOR(2.5, CURRENCY(1)) a Currency.

The 15 digits round the Double's exact binary value, not its shortest
spelling: ROUND(1.000000000000025, 14) is 1.00000000000003 and
ROUND(0.1234567890123455, 15) 0.123456789012345 (build_b257d.py
j_dec15_tie_a, j_dec15_tie_b). A multiple of a Double significance is the
Double product of the whole number and the significance: CEILING(0.25,
0.1) is 0.1 * 3, 0.30000000000000004 (j_ceiling_prod), where we multiplied
the decimals.

Expected values: Power BI Desktop 2.152 over ADOMD, by probe id --
F:/_desktop_oracle/build_b257b.py group J (F:/_tmp_pbix/open2/b252-order53),
build_b257c.py group J (F:/_tmp_pbix/rel148/desk/desk_b257c.json),
build_b257d.py group J (F:/_tmp_pbix/rel148/desk/desk_b257d.json) and
build_b254b.py f_fns (F:/_tmp_pbix/open2/b248-currency; cells TRUNC(-1.5)
D-1, ROUNDUP(1.5, 0) D2, ROUND(2.5, 0) D3; D = Double); the helper checks
are exact (decimal) arithmetic.
"""
from __future__ import annotations

import decimal
import random

import pytest

from pbix_mcp.dax import engine as de

pytestmark = pytest.mark.unit

TABLES = {"T": {"columns": ["x"], "rows": [[1]]}}


def _ev(expr):
    return de.evaluate_measure("m", TABLES, {"m": expr})


def _typ(x):
    """build_b257c.py's TYP(): the type letter of ``x``."""
    return (f'IF(ISBLANK({x}), "B", IF(ISCURRENCY({x}), "C", IF(ISDOUBLE({x}), "D", '
            f'IF(ISINT64({x}), "I", "?"))))')


@pytest.mark.parametrize("probe,expr,want", [
    ('j_trunc_029', 'VAR r = TRUNC(0.29, 2) RETURN IF(r = 0.29, "dec15", IF(r = 0.28, "double product", "other"))',
     'dec15'),
    ('j_floor_029', 'VAR r = FLOOR(0.29, 0.01) RETURN IF(r = 0.29, "dec15", IF(r = 0.28, "double quotient", '
     '"other"))', 'dec15'),
    ('j_roundup_tiny', 'ROUNDUP(2.0000000001, 0)', 3),
    ('j_round_17', 'VAR r = ROUND(1.0049999999999997, 2) RETURN IF(r = 1.01, "dec15", IF(r = 1, "exact repr", '
     '"other"))', 'dec15'),
    ('j_round_third', 'VAR r = ROUND(1 / 3, 16) RETURN IF(r = 0.333333333333333, "dec15", '
     'IF(r = 1 / 3, "exact repr", "other"))', 'dec15'),
    ('j_roundup_sum', 'VAR r = ROUNDUP(0.1 + 0.2, 16) RETURN IF(r = 0.3, "dec15", "exact repr")', 'dec15'),
    ('j_trunc_16', 'TRUNC(1234567890123456) - 1234567890123450', 10),
    ('j_type_round_int', _typ('ROUND(5, 0)'), 'I'),
    ('j_type_ceiling_int', _typ('CEILING(5, 2)'), 'I'),
    ('j_type_ceiling_dbl', _typ('CEILING(2.5, 1)'), 'I'),
    ('j_type_roundup_int', _typ('ROUNDUP(5, 0)'), 'I'),
    ('j_type_trunc_int', _typ('TRUNC(5)'), 'I'),
])
def test_b257c_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


# the reference reading, independent of the engine's: the Double's exact
# binary value rounded to 15 significant digits (build_b257d.py
# j_dec15_tie_a, j_dec15_tie_b)
_D15 = decimal.Context(prec=15, rounding=decimal.ROUND_HALF_EVEN)


def _d15(v):
    return _D15.create_decimal_from_float(float(v))


def _directed_ref(val, digits, up):
    q = _d15(val).quantize(decimal.Decimal(1).scaleb(-digits),
                           rounding=decimal.ROUND_UP if up else decimal.ROUND_DOWN,
                           context=decimal.Context(prec=60))
    return int(q) if type(val) is int else float(q)


def _half_ref(val, digits):
    q = _d15(val).quantize(decimal.Decimal(1).scaleb(-digits), rounding=decimal.ROUND_HALF_UP,
                           context=decimal.Context(prec=60))
    return int(q) if type(val) is int else float(q)


def _same(a, b):
    """Equal, of one type, and a zero of one sign."""
    return a == b and type(a) is type(b) and (str(a) == str(b) if a == 0 else True)


def _multiple_ref(val, sig, up):
    """The whole number of the decimals' quotient, times the significance in
    its own type: a Double multiple is the Double product (build_b257d.py
    j_ceiling_prod)."""
    ctx = decimal.Context(prec=60)
    n = ctx.divide(_d15(val), _d15(sig)).to_integral_value(
        rounding=decimal.ROUND_CEILING if up else decimal.ROUND_FLOOR, context=ctx)
    return int(n) * sig if type(sig) is int else (float(n) or 0.0) * sig


def _doubles(n):
    r = random.Random(252)
    out = []
    for _ in range(n):
        k = r.random()
        if k < 0.3:
            out.append(r.uniform(-1000, 1000))
        elif k < 0.6:       # within a few 15-digit units of a multiple of 10 ** -d
            d = r.randint(0, 6)
            base = r.randint(-10 ** 6, 10 ** 6) / 10 ** d
            out.append(base + r.choice([0, 1, -1, 5e-16, -5e-16, 1e-13, -1e-13]) * max(abs(base), 1) * 1e-15)
        elif k < 0.8:
            out.append(r.uniform(-1, 1) * 10 ** r.randint(-12, 16))
        else:
            out.append(float(r.randint(-10 ** 16, 10 ** 16)))
    return out


def test_fast_paths_round_as_the_decimals_do():
    """Exact arithmetic: the Double shortcuts of ROUND, ROUNDUP / ROUNDDOWN /
    TRUNC and CEILING / FLOOR give what the 15-digit decimals give, value and
    type, over Doubles of many magnitudes and near-ties."""
    r = random.Random(7)
    sigs = [1, 2, 7, -2, 10, 0.5, 0.25, 0.1, 0.01, 0.05, 1.5, 2.0, 100.0, 1e-7, 0.123456789]
    for v in _doubles(4000):
        for d in (0, 1, 2, 5, 15, 16, -1):
            up = r.random() < 0.5
            got = de._round_dec15_directed(v, d, up)
            want = _directed_ref(v, d, up)
            assert _same(got, want), (v, d, up)
            got = de._round_dec15_half(v, d)
            want = _half_ref(v, d)
            assert _same(got, want), (v, d)
        sig = r.choice(sigs)
        for val in (v, int(v) if abs(v) < 1e17 else 5):
            up = r.random() < 0.5
            got = de._round_dec15_multiple(val, sig, up)
            want = _multiple_ref(val, sig, up)
            assert _same(got, want), (val, sig, up)
    for iv in (5, -5, 123456789012345, 1234567890123456, -(2 ** 53 + 1), 999999999999999):
        for d in (0, 2, -1, -3, -15, -16):
            for up in (True, False):
                got = de._round_dec15_directed(iv, d, up)
                want = _directed_ref(iv, d, up)
                assert got == want and type(got) is type(want), (iv, d, up)
            got = de._round_dec15_half(iv, d)
            want = _half_ref(iv, d)
            assert got == want and type(got) is type(want), (iv, d)


@pytest.mark.parametrize("probe,expr,want", [
    ('z53_trunc', 'TRUNC(9007199254740993) - 9007199254740992', -2),
    ('z53_rounddown', 'ROUNDDOWN(9007199254740993, 0) - 9007199254740992', -2),
    ('z53_roundup', 'ROUNDUP(9007199254740993, 0) - 9007199254740992', -2),
    ('z53_round', 'ROUND(9007199254740993, 0) - 9007199254740992', -2),
    ('z53_ceiling', 'CEILING(9007199254740993, 1) - 9007199254740992', -2),
    ('z53_floor', 'FLOOR(9007199254740993, 1) - 9007199254740992', -2),
    # Doubles, not decimals: these stay at 0
    ('z53_mround', 'MROUND(9007199254740993, 1) - 9007199254740992', 0),
    ('z53_quotient', 'QUOTIENT(9007199254740993, 1) - 9007199254740992', 0),
])
def test_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


@pytest.mark.parametrize("probe,expr,want,kind", [
    ('f_fns_5', 'TRUNC(-1.5)', -1, float),
    ('f_fns_6', 'ROUNDUP(1.5, 0)', 2, float),
    ('f_fns_10', 'ROUND(2.5, 0)', 3, float),
])
def test_types_match_desktop(probe, expr, want, kind):
    got = _ev(expr)
    assert got == want and type(got) is kind


def test_dec15_is_the_doubles_15_digit_decimal():
    """Exact arithmetic: 2**53 + 1 is the Double 2**53, 9.00719925474099E+15
    in 15 digits; a number of up to 15 digits is its own spelling."""
    import decimal
    assert de._dec15(2 ** 53 + 1) == decimal.Decimal("9007199254740990")
    assert de._dec15(1.005) == decimal.Decimal("1.005")
    assert de._dec15(2.675) == decimal.Decimal("2.675")
    assert de._dec15(0.1 + 0.2) == decimal.Decimal("0.3")
    assert de._dec15(123456789012345) == decimal.Decimal("123456789012345")


@pytest.mark.parametrize("expr,want", [
    ("ROUND(1234567890123456, 0)", 1234567890123460),
    ("ROUNDDOWN(1234567890123456, 0)", 1234567890123460),
    ("ROUNDUP(1234567890123454, 0)", 1234567890123450),
    ("CEILING(1234567890123456, 1)", 1234567890123460),
    # 57 * 0.01 in Doubles is 0.5700000000000001 (build_b257d.py
    # j_ceiling_prod: a multiple is the Double product)
    ("IF(FLOOR(0.57, 0.01) = 0.57, 1, 0)", 0),
    ("IF(FLOOR(0.57, 0.01) = 57 * 0.01, 1, 0)", 1),
    ("IF(ROUNDDOWN(0.57, 2) = 0.57, 1, 0)", 1),
])
def test_below_2_53_on_15_digits(expr, want):
    """Exact arithmetic on Desktop's rule (j_trunc_16, j_trunc_029,
    j_floor_029, j_ceiling_prod): a 16-digit Int64 is its 15-digit decimal,
    0.29 is 0.29, and a multiple of a Double significance is the Double
    product of the whole number and the significance."""
    assert _ev(expr) == want


def test_nan_and_infinity_are_left_as_they_are():
    """Exact arithmetic: a NaN or an infinity has no decimal to round."""
    import math
    assert math.isnan(de._round_dec15_directed(float("nan"), 0, True))
    assert de._round_dec15_directed(float("inf"), 2, False) == float("inf")


@pytest.mark.parametrize("expr", [
    "TRUNC(-9007199254740993) + 9007199254740992",
    "ROUND(-9007199254740993, 0) + 9007199254740992",
    "ROUNDDOWN(-9007199254740993, 0) + 9007199254740992",
])
def test_negative_past_2_53(expr):
    """The mirror of z53_*: -(2**53 + 1) is the Double -2**53, which is
    -9.00719925474099E+15 in 15 digits, so each is +2 (exact arithmetic on
    Desktop's rule; the negative side is not measured)."""
    assert _ev(expr) == 2


# build_b257d.py group J (F:/_tmp_pbix/rel148/desk/desk_b257d.json)
@pytest.mark.parametrize("probe,expr,want", [
    # a multiple of a Double significance is the Double product: 3 * 0.1 is
    # 0.30000000000000004 (we multiplied the decimals and answered 0.3)
    ('j_ceiling_prod', 'VAR r = CEILING(0.25, 0.1) RETURN IF(r = 0.3, "decimal product", '
                       'IF(r = 0.1 * 3, "double product", "other"))', 'double product'),
    ('j_type_ceiling_dsig', _typ('CEILING(5, 0.5)'), 'D'),
    ('j_type_floor_csig', _typ('FLOOR(2.5, CURRENCY(1))'), 'C'),
    # an Int64 stays one at positive digits too (we answered D)
    ('j_type_round_int2', _typ('ROUND(5, 2)'), 'I'),
    ('j_type_roundup_int1', _typ('ROUNDUP(5, 1)'), 'I'),
    ('j_type_trunc_int1', _typ('TRUNC(5, 1)'), 'I'),
    # the 15 digits round the Double's exact binary value, not its spelling
    # (we rounded the spelling half even: ...002 and ...346)
    ('j_dec15_tie_a', 'VAR r = ROUND(1.000000000000025, 14) RETURN IF(r = 1.00000000000002, "half even (engine)", '
                      'IF(r = 1.00000000000003, "half up or binary", "other"))', 'half up or binary'),
    ('j_dec15_tie_b', 'VAR r = ROUND(0.1234567890123455, 15) RETURN IF(r = 0.123456789012346, "spelling (engine)", '
                      'IF(r = 0.123456789012345, "binary", "other"))', 'binary'),
])
def test_b257d_matches_desktop(probe, expr, want):
    assert _ev(expr) == want


def test_dec15_rounds_the_exact_binary_value():
    """Exact arithmetic: the Double 1.000000000000025 is
    1.0000000000000250910..., 0.1234567890123455 is 0.1234567890123454969...;
    rounded to 15 significant digits they are ...003 and ...345
    (j_dec15_tie_a, j_dec15_tie_b)."""
    assert decimal.Decimal(1.000000000000025) > decimal.Decimal("1.000000000000025")
    assert decimal.Decimal(0.1234567890123455) < decimal.Decimal("0.1234567890123455")
    assert de._dec15(1.000000000000025) == decimal.Decimal("1.00000000000003")
    assert de._dec15(0.1234567890123455) == decimal.Decimal("0.123456789012345")
    assert de._round_dec15_half(1.000000000000025, 14) == 1.00000000000003
    assert de._round_dec15_half(0.1234567890123455, 15) == 0.123456789012345


def test_int64_keeps_its_type_at_any_digits():
    """ROUND / ROUNDUP / ROUNDDOWN / TRUNC of an Int64 are Int64s at any
    digits, past 10**15 too, and statically (j_type_round_int2, ...;
    _static_type feeds IF's unification: IF(FALSE(), CURRENCY(1),
    ROUND(5, 2)) is a Currency, Int64 beside Currency)."""
    for fn in (de._round_dec15_half, lambda v, d: de._round_dec15_directed(v, d, True),
               lambda v, d: de._round_dec15_directed(v, d, False)):
        for v in (5, -5, 1234567890123456, 2 ** 53 + 1):
            for d in (0, 1, 2, 16):
                assert type(fn(v, d)) is int, (v, d)
    assert de._round_dec15_half(1234567890123456, 2) == 1234567890123460
    eng = de.DAXEngine()
    ctx = de.DAXContext(TABLES, {})
    for e in ('ROUND(5, 2)', 'ROUNDUP(5, 1)', 'ROUNDDOWN(5, 3)', 'TRUNC(5, 1)', 'ROUND(5, -1)'):
        assert eng._static_type(e, ctx) == 'I', e
    assert _ev(_typ('IF(FALSE(), CURRENCY(1), ROUND(5, 2))')) == 'C'


def test_a_double_multiple_is_the_double_product():
    """Exact arithmetic (j_ceiling_prod, j_floor_029): the whole number of
    the 15-digit quotient times the Double significance, in Doubles; an
    Int64 significance multiplies exactly."""
    assert de._round_dec15_multiple(0.25, 0.1, True) == 3 * 0.1 == 0.30000000000000004
    assert de._round_dec15_multiple(0.29, 0.01, False) == 29 * 0.01 == 0.29
    assert de._round_dec15_multiple(0.57, 0.01, False) == 57 * 0.01 == 0.5700000000000001
    assert de._round_dec15_multiple(1e20, 0.1, True) == 1e21 * 0.1
    assert de._round_dec15_multiple(9007199254740993, 1, True) == 9007199254740990
