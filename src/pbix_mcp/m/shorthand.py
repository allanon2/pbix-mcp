"""Power Query's shorthand: the text its editor shows for a constant step in the formula bar ("5", "abc",
"1/1/2020" rather than ``= 5``), and for a parameter's value in the Queries pane ("Rate (5)").

Measured behaviour, read as rules (pivot-studio #352):

- **Which steps are constants.** A literal (a number, text, logical or null; ``#infinity`` and ``#nan`` too); ``+`` or
  ``-`` on a number literal; or a call of ``#date``, ``#time``, ``#datetime``, ``#datetimezone``, ``#duration``,
  ``#binary`` or ``#table`` whose every argument is such a constant, when the call succeeds. Parentheses make a step
  not a constant (``(5)``), and so do a name (``#"#date"(...)``), an operator, ``type ...``, a list or a record. A
  constant whose value is a binary or a function isn't shown as shorthand either.
- **The text.** A number in 15 significant digits, or 17 when 15 don't give the number back, with an exponent from
  1E-05 down and from 1E+15 up when not whole ("1E-05", "1.5E+300"); a whole number past 999,999,999,999,999 in full
  digits ("1000000000000000"), rounded to 15 significant digits past 2^63; ``#nan`` "NaN". A literal of more than 15
  significant digits keeps them as a decimal does, as written ("0.30000000000000004", "1234567890.12345670"). A logical "TRUE"/"FALSE"; null "null". A date in the
  culture's short date ("1/1/2020"; en-AU "1/01/2020"); a datetime with the culture's long time ("1/1/2020 1:05:09 PM")
  and a datetimezone with its offset after (" +10:00"); a time in the long time ("1:05:09 PM"); any of these with a
  millisecond as ISO 8601 with seven fractional digits ("2020-01-01T10:00:00.5000000", a time "10:00:00.5000000").
  A duration as "d.hh:mm:ss[.fffffff]", "0." before one under a day ("0.00:05:00"), a negative one "-00:05:00".
- **Text** shows as itself, or with a leading ``'`` when it would read back as something else: text starting with
  ``=`` (and longer than that), or ``'``, or reading as a number, a logical, ``null``, a date or time, or a duration
  ("'5", "'TRUE", "'1/1/2020", "'=x"). Text holding a control character (a line feed, a tab) shows as a formula,
  ``= "a#(lf)b"``.
- **A parameter** is a query whose text is ``value meta [IsParameterQuery = ...]`` (the field there, whatever its
  value, as a file loads), or a ``let`` of one such step returning it. Its value shows the same way but never with a
  ``'`` nor as a formula, and not at all when it is null or not a constant (the pane then shows "Name (...)"). A binary
  parameter shows the name its value refers to.

A culture is one of ``dtparse.CULTURE_DATA``'s. What isn't settled raises ``ShorthandUnsupported``: a number literal
of more than 28 significant digits, a constant list, record or table, a ``#binary`` parameter value, and text naming
a zone the date reader refuses (other than M's ISO forms).
"""
from __future__ import annotations

import datetime as dt
import math
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any, Optional

from . import ast, dtparse, library
from .values import Function, MError, kind_of


class ShorthandUnsupported(Exception):
    """A constant whose shorthand isn't settled; the message says why."""


# Literal tokens naming a value ("#date" the constructor, "#infinity" a number), as opposed to an identifier.
PREDEFINED = frozenset({"#date", "#datetime", "#datetimezone", "#time", "#duration", "#binary", "#table",
                        "#infinity", "#nan"})

# Windows' culture data as .NET Framework reads it: the long time pattern, AM and PM come from dtparse's cultures.
# The infinity symbols are Windows 10's (hypothesis, not measured on Desktop).
_LONG_TIME_24H = {"": True}
_INFINITY = {"": ("Infinity", "-Infinity")}
_DEFAULT_INFINITY = ("∞", "-∞")


def _culture(culture: str) -> dtparse.DateCulture:
    if culture not in dtparse.CULTURE_DATA:
        raise ShorthandUnsupported(f"The culture '{culture}' isn't supported yet.")
    return dtparse.CULTURE_DATA[culture]


# --- which expressions are constants ------------------------------------------------------------------------------

_NOT_CONSTANT = object()


class DoubleDecimal(float):
    """A number literal of more than 15 significant digits: Power Query keeps its decimal beside the double, and
    writes the decimal (its digits and scale, as .NET's decimal "G")."""
    decimal: Decimal

    def __new__(cls, value: float, d: Decimal) -> "DoubleDecimal":
        obj = super().__new__(cls, value)
        obj.decimal = d
        return obj

    def __neg__(self) -> "DoubleDecimal":
        return DoubleDecimal(-float(self), -self.decimal)


def constant_value(node: Any, units: str, culture: str = "en-US") -> Any:
    """The value of a constant expression (a check reader's node over ``units``), or ``_NOT_CONSTANT``.
    Raises ShorthandUnsupported for a number literal whose value isn't settled."""
    kind = node.kind
    if kind == "constant":
        tree = node.tree
        if isinstance(tree, ast.Identifier):           # #date, #infinity, ...
            return library.LIBRARY[tree.name] if tree.name in library.LIBRARY else _NOT_CONSTANT
        if tree.kind == "number":
            return _number_literal(units[node.start:node.end], tree.value)
        return tree.value
    tree = node.tree
    if isinstance(tree, ast.Unary) and tree.op in ("+", "-") and len(node.children) == 1:
        operand = node.children[0]
        if operand.kind != "constant":
            return _NOT_CONSTANT
        v = constant_value(operand, units, culture)
        if v is _NOT_CONSTANT or kind_of(v) != "number":
            return _NOT_CONSTANT
        return -v if tree.op == "-" else v      # a DoubleDecimal negates as one
    if kind == "invoke":
        target = node.children[0]
        if target.kind != "constant" or not isinstance(target.tree, ast.Identifier):
            return _NOT_CONSTANT
        for arg in node.children[1:]:
            if constant_value(arg, units, culture) is _NOT_CONSTANT:
                return _NOT_CONSTANT
        from .evaluate import evaluate
        try:
            return evaluate(tree, culture=culture)
        except MError:                                  # a call that fails isn't a constant
            return _NOT_CONSTANT
    return _NOT_CONSTANT


_DECIMAL_MAX = Decimal("79228162514264337593543950335")


def _number_literal(token: str, value: Any) -> Any:
    """A number literal's value: over 15 significant digits (counted on the decimal .NET reads, at most 28 places) a
    DoubleDecimal; past a decimal's range a double."""
    if len(token) > 2 and token[1] in "xX":
        return value
    if len(token) <= 9 and "e" not in token.lower():
        return value
    try:
        d = Decimal(token)
    except InvalidOperation:
        return value
    if abs(d) > _DECIMAL_MAX:                           # beyond a decimal: a double only
        return value
    if d.as_tuple().exponent < -28:
        d = d.quantize(Decimal(1).scaleb(-28))
    if len(d.as_tuple().digits) > 28:
        raise ShorthandUnsupported("A number literal of more than 28 significant digits isn't supported yet.")
    text = format(d, "f")
    if len(text) > 15:
        i = 0
        while i < len(text) and text[i] == "0":
            i += 1
        dot = text.find(".", i)
        last = len(text) - 1
        if dot >= 0:
            while last > i and last > dot and text[last] == "0":
                last -= 1
        whole = (dot if dot >= 0 else last + 1) - i
        frac = last - dot if dot >= 0 else 0
        if whole + frac > 15:
            return DoubleDecimal(float(value), d)
    return value


# --- a value's text -----------------------------------------------------------------------------------------------

def _g(v: float, precision: int) -> str:
    """.NET's "G<precision>" for a finite double, in a culture writing "-" and ".": scientific from an exponent of
    ``precision`` and from -5 down, trailing zeros dropped, the exponent signed and in two digits at least."""
    if v == 0:
        return "0"                                    # -0.0 too, on .NET Framework
    mantissa, exp = f"{v:.{precision - 1}e}".split("e")
    exponent = int(exp)
    sign = "-" if mantissa.startswith("-") else ""
    digits = mantissa.lstrip("-").replace(".", "").rstrip("0") or "0"
    if exponent >= precision or exponent < -4:
        body = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        return f"{sign}{body}E{'+' if exponent >= 0 else '-'}{abs(exponent):02d}"
    if exponent < 0:
        return f"{sign}0.{'0' * (-exponent - 1)}{digits}"
    whole = digits[:exponent + 1].ljust(exponent + 1, "0")
    frac = digits[exponent + 1:]
    return f"{sign}{whole}{'.' + frac if frac else ''}"


def number_text(v: Any, culture: str) -> str:
    """A number's round-trip text, as the formula bar shows a constant."""
    if isinstance(v, int):
        return str(v)
    if isinstance(v, DoubleDecimal):
        return format(v.decimal, "f")
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        pos, neg = _INFINITY.get(culture, _DEFAULT_INFINITY)
        return pos if v > 0 else neg
    if v.is_integer() and abs(v) > 999999999999999:
        if -(2 ** 63) <= v < 2 ** 63:
            return str(int(v))
        if abs(v) < 79228162514264337593543950336:     # a decimal: 15 significant digits
            return str(int(Decimal(f"{v:.14e}")))
    text = _g(v, 15)
    return text if float(text) == v else _g(v, 17)


def _long_time(t: dt.time, c: dtparse.DateCulture) -> str:
    if _LONG_TIME_24H.get(c.name):
        return f"{t.hour:02d}:{t.minute:02d}:{t.second:02d}"
    return f"{t.hour % 12 or 12}:{t.minute:02d}:{t.second:02d} {c.am if t.hour < 12 else c.pm}"


def _short_date(d: dt.date, culture: str) -> str:
    return library._date_text(d, culture)


def _fraction(us: int) -> str:
    return f"{us * 10:07d}"


def _offset(v: dt.datetime) -> str:
    off = v.utcoffset() or dt.timedelta(0)
    sign = "-" if off < dt.timedelta(0) else "+"
    minutes = abs(int(off.total_seconds())) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def duration_text(v: dt.timedelta) -> str:
    ticks = (v.days * 86400 + v.seconds) * 10_000_000 + v.microseconds * 10
    sign = "-" if ticks < 0 else ""
    ticks = abs(ticks)
    days, rest = divmod(ticks, 864_000_000_000)
    hours, rest = divmod(rest, 36_000_000_000)
    minutes, rest = divmod(rest, 600_000_000)
    seconds, frac = divmod(rest, 10_000_000)
    text = f"{sign}{str(days) + '.' if days else ''}{hours:02d}:{minutes:02d}:{seconds:02d}"
    if frac:
        text += f".{frac:07d}"
    return text if sign or days else "0." + text


def value_text(v: Any, culture: str) -> str:
    """A primitive value's round-trip text in the culture: the shorthand before any escaping."""
    c = _culture(culture)
    kind = kind_of(v)
    if kind == "null":
        return "null"
    if kind == "logical":
        return "TRUE" if v else "FALSE"
    if kind == "number":
        return number_text(v, culture)
    if kind == "text":
        return v
    if kind == "date":
        return _short_date(v, culture)
    if kind == "time":
        if v.microsecond // 1000:
            return f"{v.hour:02d}:{v.minute:02d}:{v.second:02d}.{_fraction(v.microsecond)}"
        return _long_time(v, c)
    if kind in ("datetime", "datetimezone"):
        if v.microsecond // 1000:
            iso = f"{v.year:04d}-{v.month:02d}-{v.day:02d}T{v.hour:02d}:{v.minute:02d}:{v.second:02d}" \
                  f".{_fraction(v.microsecond)}"
            return iso + (_offset(v) if kind == "datetimezone" else "")
        # (Desktop rounds from half a second before this, which a value without a millisecond never reaches)
        text = f"{_short_date(v.date(), culture)} {_long_time(v.time(), c)}"
        return text + (" " + _offset(v) if kind == "datetimezone" else "")
    if kind == "duration":
        return duration_text(v)
    raise ShorthandUnsupported(f"The shorthand of a {kind} value isn't supported yet.")


# --- a value's display text (a preview cell) ----------------------------------------------------------------------
#
# Power Query's data preview shows a cell's display string, not its round-trip text (pivot-studio #361): the engine's
# DisplayString as .NET Framework formats it in the culture. Rules, read as behaviour:
#
# - A number from 0.0001 to 99,999,999,999 in magnitude (or zero) with 10 decimals less the digits of its magnitude
#   rounded to a whole number, at most 9, trailing zeros dropped ("0.333333333", "0.3" for 0.1 + 0.2, "1234567891" for
#   1234567890.5; 9.876543219 rounds to 10, two digits, so "9.87654322"); outside that, scientific with one to five decimals ("1.0E-07", "1.23457E+11").
#   Digits are taken to 15 significant first, then rounded half away from zero. NaN "NaN", the infinities
#   "Infinity" and "-Infinity" whatever the culture.
# - In a Percentage.Type column: times 100 in two decimals with no grouping ("50.00%"); a Currency.Type column: grouped
#   in two decimals ("1,234.50").
# - A datetime as the culture's short date and long time, a datetimezone with its offset after, a time in the long
#   time; any of them from half a second up rounds to the next second (no fraction is shown).
# - A duration, a logical, null and text as their round-trip text.
#
# The cultures are dtparse's; the number formats are theirs on Windows (en-US, en-AU: "-n", "n%"; invariant "n %").
_PERCENT_SPACE = {"": True}


def _digits15(v: float) -> Decimal:
    """A double's 15 significant digits, as .NET Framework's formatting takes them before rounding to a format."""
    return Decimal(f"{v:.14e}")


def _fixed(d: Decimal, decimals: int, group: bool = False, keep: bool = False) -> str:
    """``d`` rounded half away from zero to ``decimals`` places; trailing zeros dropped unless ``keep``."""
    q = d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP) if decimals else \
        d.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    text = format(abs(q), f"{',' if group else ''}.{decimals}f")
    if not keep and "." in text:
        text = text.rstrip("0").rstrip(".")
    return sign + text if text.strip("0.,") else text      # a value rounding to zero has no sign


def _scientific(d: Decimal) -> str:
    """.NET's custom "0.0####E+00": one integer digit, one to five decimals, a signed exponent of two digits at least."""
    if d == 0:
        return "0.0E+00"
    sign = "-" if d < 0 else ""
    d = abs(d)
    exponent = d.adjusted()
    mantissa = (d.scaleb(-exponent)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    if mantissa >= 10:
        exponent += 1
        mantissa = (d.scaleb(-exponent)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    text = format(mantissa, ".5f").rstrip("0")
    if text.endswith("."):
        text += "0"
    return f"{sign}{text}E{'+' if exponent >= 0 else '-'}{abs(exponent):02d}"


def number_display_text(v: Any, culture: str, facet: Optional[str] = None) -> str:
    """A number's display string in the culture, ``facet`` its column's type (Percentage.Type, Currency.Type)."""
    _culture(culture)
    with localcontext() as ctx:
        ctx.prec = 400          # every double's whole digits and two decimals (1.8E+308 times 100), past the default 28
        return _number_display_text(v, culture, facet)


def _number_display_text(v: Any, culture: str, facet: Optional[str]) -> str:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return "NaN" if v != v else ("Infinity" if v > 0 else "-Infinity")
    d = v if isinstance(v, Decimal) else (Decimal(v) if isinstance(v, int) and abs(v) < 10 ** 15 else _digits15(float(v)))
    if facet == "Percentage.Type":
        text = _fixed(d * 100, 2, keep=True)
        return text + (" %" if _PERCENT_SPACE.get(culture) else "%")
    if facet == "Currency.Type":
        return _fixed(d, 2, group=True, keep=True)
    a = abs(d)
    if a != 0 and not (Decimal("0.0001") <= a <= Decimal("99999999999")):
        return _scientific(d)
    length = len(str(a.quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    return _fixed(d, min(9, max(0, 10 - length)))


def _round_second(v: Any) -> Any:
    """Half a second and up rounds to the next second, as the preview shows a time; the fraction then dropped."""
    if v.microsecond >= 500_000:
        try:
            v = v + dt.timedelta(microseconds=500_000)
        except OverflowError:
            pass
    return v.replace(microsecond=0)


def display_text(v: Any, culture: str, facet: Optional[str] = None) -> str:
    """A primitive value's display string in the culture, as Power Query's data preview shows a cell (#361);
    ``facet`` is the column's type facet. What isn't settled raises ``ShorthandUnsupported``."""
    c = _culture(culture)
    if isinstance(v, Decimal):
        return number_display_text(v, culture, facet)
    kind = kind_of(v)
    if kind == "number":
        return number_display_text(v, culture, facet)
    if kind == "time":
        t = _round_second(dt.datetime.combine(dt.date(2000, 1, 1), v)).time()
        return _long_time(t, c)
    if kind in ("datetime", "datetimezone"):
        r = _round_second(v)
        text = f"{_short_date(r.date(), culture)} {_long_time(r.time(), c)}"
        return text + (" " + _offset(r) if kind == "datetimezone" else "")
    return value_text(v, culture)


# --- what text reads back as --------------------------------------------------------------------------------------

_XML_DURATION = re.compile(r"-?P(?=\d|T\d)(\d+Y)?(\d+M)?(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?")
_TIMESPAN = re.compile(r"\d{1,8}|(\d{1,8}\.)?\d{1,2}:\d{1,2}(:\d{1,2}(\.\d{1,7})?)?")


def _reads_as_duration(text: str) -> bool:
    if text.startswith(("P", "-P")) and _XML_DURATION.fullmatch(text.strip()):
        return True
    s = text.strip()
    if s.startswith("-"):
        s = s[1:]
    m = _TIMESPAN.fullmatch(s)
    if not m:
        return False
    if ":" not in s:
        return int(s) <= 10675199
    parts = s.split(".", 1)[-1] if m.group(1) else s
    hms = parts.split(".")[0].split(":")
    return int(hms[0]) <= 23 and all(int(p) <= 59 for p in hms[1:])


def _reads_as_other(shorthand: str, culture: str) -> bool:
    """Whether text written as shorthand would read back as something other than plain text, so needs a ``'``:
    Power Query reads it as a formula, then a number, a logical, null, a date, a time, a datetime, a duration and a
    datetimezone (dates and times in the culture: M's ISO forms, then .NET's lenient parse)."""
    if shorthand.startswith("=") and len(shorthand) > 1:
        return True
    if shorthand.startswith("'"):
        return True
    text = shorthand.lstrip()
    if reads_as_number(text, culture):
        return True
    squeezed = re.sub(r"\s", "", text)
    if squeezed.lower() in ("true", "false") or squeezed == "null":
        return True
    if text:
        if _ISO_ZONED.fullmatch(text):                  # M's ISO forms with a zone (IsoDateTimeParser)
            return True
        try:
            library._parse_datetime(text, culture)
            return True
        except MError as e:
            if "isn't supported" in e.message:
                raise ShorthandUnsupported(e.message) from None
    return _reads_as_duration(text)


_ISO_ZONED = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?(Z|[+-]\d{2}:\d{2})")

# .NET's number text for these cultures (Windows' data): signs, separators, the currency symbol (the invariant
# culture's is "¤"), NaN; the infinity symbols as above
_CURRENCY = {"": "\u00a4"}


def _parse_number(s: str, currency: str) -> bool:
    """Whether .NET's ParseNumber reads all of ``s`` under NumberStyles.Any (leading and trailing white space, a sign
    before or after, parentheses, a currency symbol once, "," between digits after the first, ".", an exponent),
    with "-", "+", "," and "." as these cultures write them, and negative pattern 1."""
    i, n = 0, len(s)
    white = lambda ch: ch == " " or "\t" <= ch <= "\r"   # noqa: E731
    sign = parens = cur = False
    while i < n:
        ch = s[i]
        if white(ch) and not sign:
            i += 1
        elif not sign and ch in "+-":
            sign = True
            i += 1
        elif ch == "(" and not sign:
            sign = parens = True
            i += 1
        elif not cur and s.startswith(currency, i):
            cur = True
            i += len(currency)
        else:
            break
    digits = decimal = False
    while i < n:
        ch = s[i]
        if "0" <= ch <= "9":
            digits = True
        elif ch == "." and not decimal:
            decimal = True
        elif ch == "," and digits and not decimal:
            pass
        else:
            break
        i += 1
    if not digits:
        return False
    if i < n and s[i] in "eE":
        j = i + 1
        if j < n and s[j] in "+-":
            j += 1
        if j < n and "0" <= s[j] <= "9":
            while j < n and "0" <= s[j] <= "9":
                j += 1
            i = j
    while i < n:
        ch = s[i]
        if white(ch):
            i += 1
        elif not sign and ch in "+-":
            sign = True
            i += 1
        elif ch == ")" and parens:
            parens = False
            i += 1
        elif not cur and s.startswith(currency, i):
            cur = True
            i += len(currency)
        else:
            break
    return i == n and not parens


def reads_as_number(text: str, culture: str) -> bool:
    """NumberValue.TryParse(text, NumberStyles.Any, culture): .NET's double or decimal parse (an overflow is still a
    number, an infinity), the culture's NaN and infinity symbols, or a percent: one "%" and at most one "-" among
    white space at either end, around such a number."""
    currency = _CURRENCY.get(culture, "$")
    if _parse_number(text, currency):
        return True
    if text.strip(" \t\n\v\f\r") in ("NaN", *_INFINITY.get(culture, _DEFAULT_INFINITY)):
        return True
    i, j, percents, negatives = 0, len(text) - 1, 0, 0
    while i <= j:
        if text[i] in "%-":
            percents, negatives = percents + (text[i] == "%"), negatives + (text[i] == "-")
        elif not text[i].isspace():
            break
        i += 1
    while j >= i:
        if text[j] in "%-":
            percents, negatives = percents + (text[j] == "%"), negatives + (text[j] == "-")
        elif not text[j].isspace():
            break
        j -= 1
    return j >= i and percents == 1 and negatives <= 1 and _parse_number(text[i:j + 1], currency)


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def value_shorthand(v: Any, culture: str, *, parameter: bool = False) -> str:
    """The shorthand of a constant's value (not a binary or a function). ``parameter``: a parameter's options, with
    no formula and no ``'``."""
    text = value_text(v, culture)
    if kind_of(v) != "text" or parameter:
        return text
    if _CONTROL.search(text):
        from .check import _quoted
        return "= " + _quoted(v)
    return "'" + text if _reads_as_other(text, culture) else text


def formula_text(node: Any, units: str, written: str, culture: str) -> str:
    """The formula bar's text for an expression (a check reader's node): a constant's shorthand, else "= " and the
    expression as written."""
    v = constant_value(node, units, culture)
    if v is not _NOT_CONSTANT and kind_of(v) not in ("binary", "function"):
        return value_shorthand(v, culture)
    return "= " + written


def _metadata_field(record: Any, name: str) -> Any:
    for k, (field_name, _s, _e) in enumerate(record.names):
        if field_name == name:
            return record.children[k]
    return None


def _parameter_meta(root: Any) -> Any:
    """A parameter's ``meta`` node: the query's own, or a one-step ``let`` returning it (Power BI allows single-step
    parameters); None for a query that isn't one."""
    node = root
    if root.kind == "let" and len(root.names) == 1:
        body = root.children[1]
        if body.kind == "identifier" and not body.inclusive and body.ident == root.names[0][0]:
            node = root.children[0]
    tree = node.tree
    if (isinstance(tree, ast.Binary) and tree.op == "meta" and len(node.children) == 2
            and node.children[1].kind == "record" and _metadata_field(node.children[1], "IsParameterQuery") is not None):
        return node
    return None


def is_parameter(root: Any) -> bool:
    """Whether a query is a parameter, as Power Query decides when a file loads: its ``meta`` record names
    IsParameterQuery, whatever the value."""
    return _parameter_meta(root) is not None


def parameter_label_value(root: Any, units: str, culture: str) -> Optional[str]:
    """What the Queries pane shows in a parameter's "Name (value)" (a check reader's root node), None for "..." (or
    for a query that isn't a parameter: ``is_parameter`` tells them apart)."""
    node = _parameter_meta(root)
    if node is None:
        return None
    left = node.children[0]
    if _metadata_field(node.children[1], "BinaryIdentifier") is not None:
        return left.ident if left.kind == "identifier" else None
    if left.kind == "invoke" and left.children[0].kind == "constant" and getattr(left.children[0].tree, "name", "") \
            in ("#binary", "#table"):
        raise ShorthandUnsupported(f"The shorthand of a {left.children[0].tree.name} parameter value isn't supported yet.")
    v = constant_value(left, units, culture)
    if v is _NOT_CONSTANT:
        return None
    if kind_of(v) in ("binary", "function"):
        raise ShorthandUnsupported(f"The shorthand of a {kind_of(v)} parameter value isn't supported yet.")
    text = value_shorthand(v, culture, parameter=True)
    return None if text == "null" else text
