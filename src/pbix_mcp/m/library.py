"""The part of M's standard library the evaluator runs: what a table typed in
with Desktop's "Enter data", written with ``#table`` or appended with
``Table.Combine`` needs, and the type conversions ``Table.TransformColumnTypes``
makes. Behaviour follows Microsoft's published function reference
(learn.microsoft.com/powerquery-m); where it is silent, the rule is the one
Desktop's own saved refreshes show, and each such rule says so.

``LIBRARY_NAMES`` is what a caller may count on: any other library name raises
``Expression.Error`` when evaluated.

Text to number, date and time conversions know the en-US culture (and the
invariant ISO forms) only; another culture is an error, not a guess.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import gzip
import json
import math
import re
import zlib
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Callable, Optional, TypeVar

from .values import ANY, CellError, Function, MError, MType, Record, Table, force, kind_of

CULTURES = frozenset({"en-US"})

INT64_TYPE = MType("number", facet="Int64.Type")
CURRENCY_TYPE = MType("number", facet="Currency.Type")
PERCENTAGE_TYPE = MType("number", facet="Percentage.Type")
_INT64 = (-(2 ** 63), 2 ** 63 - 1)
_CURRENCY = (Decimal("-922337203685477.5808"), Decimal("922337203685477.5807"))


def _error(message: str, reason: str = "Expression.Error") -> MError:
    return MError(reason, message)


def _need(value: Any, kind: str, what: str) -> None:
    if kind_of(value) != kind:
        raise _error(f"{what} needs a {kind} value, not {kind_of(value)}.")


def _culture(culture: Any, default: str) -> str:
    name = default if culture is None else culture
    if not isinstance(name, str):
        raise _error(f"A culture must be text, not {kind_of(name)}.")
    if name not in CULTURES:
        raise _error(f"The culture '{name}' isn't supported yet.")
    return name


# --- text to number and date (en-US) ------------------------------------------

_NUMBER = re.compile(r"""^(?P<open>\()?
                         (?P<sign>[+-])?\$?(?P<sign2>[+-])?
                         (?P<int>\d+(?:,\d+)*|)
                         (?:\.(?P<frac>\d*))?
                         (?:[eE](?P<exp>[+-]?\d+))?
                         (?P<pct>%)?
                         (?P<close>\))?$""", re.X)


def number_from_text(text: str, culture: str) -> int | float | None:
    """en-US number text, as a Desktop refresh reads it (#104's D1 probes): digits with "," between any of
    them ("1,23" is 123), ".", an exponent, a sign before or after a leading "$" ("-$5", "$-5"), a trailing "%"
    (a hundredth), and an accounting negative in parentheses ("(1,000)" is -1000). Empty text is null. A number
    beyond a double's range is infinite ("1e400")."""
    if text == "":
        return None
    s = text.strip()
    m = _NUMBER.match(s)
    if (not m or not (m.group("int") or m.group("frac")) or bool(m.group("open")) != bool(m.group("close"))
            or m.group("sign") and m.group("sign2") or m.group("open") and (m.group("sign") or m.group("sign2"))):
        raise _error("We couldn't convert to Number.", "DataFormat.Error")
    digits = (m.group("int") or "0").replace(",", "") + ("." + m.group("frac") if m.group("frac") else "")
    value = Decimal(digits)
    if m.group("exp"):
        value = value.scaleb(int(m.group("exp")))
    if m.group("pct"):
        value = value / 100
    if "-" in (m.group("sign"), m.group("sign2")) or m.group("open"):
        value = -value
    if value == value.to_integral_value() and not (m.group("frac") or m.group("exp") or m.group("pct")):
        return int(value)
    return float(value)


_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S.%f",
                     "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                     "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
                     "%b %d, %Y")
_TWO_DIGIT_YEAR = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$")


def _parse_datetime(text: str) -> tuple[dt.datetime, bool]:
    """(the value, whether the text gave a time of day)."""
    s = " ".join(text.split())
    if len(s) > 26 and "." in s:   # Python keeps 6 fractional digits; M keeps 7
        head, _, frac = s.rpartition(".")
        s = head + "." + frac[:6]
    for fmt in _DATETIME_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt), "%H" in fmt or "%I" in fmt
        except ValueError:
            continue
    m = _TWO_DIGIT_YEAR.match(s)
    if m:
        # A two-digit year falls in 1950-2049, Windows' and .NET's default window (Microsoft's
        # Calendar.TwoDigitYearMax); Desktop reads "11/20/15" as 2015.
        yy = int(m.group(3))
        try:
            return dt.datetime(2000 + yy if yy <= 49 else 1900 + yy, int(m.group(1)), int(m.group(2))), False
        except ValueError:
            pass
    raise _error("We couldn't parse the input provided as a DateTime value.", "DataFormat.Error")


def datetime_from_text(text: str, culture: str) -> dt.datetime | None:
    """ISO 8601 forms, en-US month/day/year with an optional 12- or 24-hour time, a two-digit year, or
    "Nov 20, 2015" (#104's D1 probes). Empty text is null."""
    if text == "":
        return None
    return _parse_datetime(text)[0]


_OLE_EPOCH = dt.datetime(1899, 12, 30)
_OLE_RANGE = (-657435.0, 2958466.0)   # OLE Automation dates: 0100-01-01 to 9999-12-31


def ole_to_datetime(days: float) -> dt.datetime:
    """An OLE Automation date (DateTime.From of a number, in Microsoft's reference): whole days from
    1899-12-30, and the fraction is the time of day even below zero (-1.25 is 1899-12-29 06:00)."""
    if not (math.isfinite(days) and _OLE_RANGE[0] <= days < _OLE_RANGE[1] + 1):
        raise _error("The number is out of range of an OLE Automation date.")
    whole = math.trunc(days)
    return _OLE_EPOCH + dt.timedelta(days=whole) + dt.timedelta(days=abs(days - whole))


def datetime_to_ole(v: dt.datetime) -> float:
    """The inverse of ``ole_to_datetime``."""
    whole = (v.date() - _OLE_EPOCH.date()).days
    frac = (v - dt.datetime(v.year, v.month, v.day)).total_seconds() / 86400
    return whole + frac if whole >= 0 else whole - frac


def _finite_decimal(n: Any) -> Decimal:
    if isinstance(n, float) and not math.isfinite(n):
        raise _error("The number is out of range.")
    return Decimal(str(n))


def _round(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


# --- conversions (the .From functions TransformColumnTypes uses) --------------

def to_number(v: Any, culture: str) -> Any:
    if v is None or isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, str):
        return number_from_text(v, culture)
    if isinstance(v, dt.datetime) and not v.tzinfo:
        return datetime_to_ole(v)
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return (v - dt.date(1899, 12, 30)).days
    raise _error(f"We cannot convert a value of type {kind_of(v)} to type Number.")


def _number_or_text(v: Any, culture: str, what: str) -> Any:
    """Int64.From and Currency.From take a number, or text read as Number.From reads it: a Desktop refresh
    gives Int64.From("10%") 0 and Currency.From("10%") 0.1 (#104's D1 probes). No logicals."""
    if v is None or isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if isinstance(v, str):
        return number_from_text(v, culture)
    raise _error(f"We cannot convert a value of type {kind_of(v)} to type {what}.")


def to_int64(v: Any, culture: str) -> Any:
    """Int64.From: a fractional part rounds half to even (Microsoft's reference)."""
    n = _number_or_text(v, culture, "Int64")
    if n is None:
        return None
    try:
        r = int(_round(_finite_decimal(n), 0))
    except InvalidOperation:
        raise _error("The number is out of range of a 64 bit integer value.") from None
    if not _INT64[0] <= r <= _INT64[1]:
        raise _error("The number is out of range of a 64 bit integer value.")
    return r


def to_currency(v: Any, culture: str) -> Any:
    """Currency.From: rounded half to even to 4 decimal places, within the currency range (Microsoft's reference)."""
    n = _number_or_text(v, culture, "Currency")
    if n is None:
        return None
    try:
        d = _round(_finite_decimal(n), 4)
    except InvalidOperation:
        raise _error("The number is out of range of a currency value.") from None
    if not _CURRENCY[0] <= d <= _CURRENCY[1]:
        raise _error("The number is out of range of a currency value.")
    return int(d) if d == d.to_integral_value() else float(d)


def to_text(v: Any, culture: str) -> Any:
    """Text.From, en-US, as a Desktop refresh writes it (#104's D1 probes): a whole number in full digits
    (1e21 is "1000000000000000000000"), any other number in 15 significant digits, or 17 when 15 don't give the
    number back ("0.33333333333333331"), with an exponent below 1e-4 ("1E-05"); "true"/"false"; a date as
    "11/20/2015" and a datetime as "11/20/2015 10:59:00 AM"."""
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float) and math.isfinite(v):
        if v.is_integer():
            if abs(v) > 1e21:
                raise _error("Converting a number above 1e21 to text isn't supported yet.")
            return str(int(v))
        text = "%.15G" % v
        return text if float(text) == v else "%.17G" % v
    if isinstance(v, dt.datetime) and not v.tzinfo and not v.microsecond:
        hour = v.hour % 12 or 12
        return f"{v.month}/{v.day}/{v.year} {hour}:{v.minute:02d}:{v.second:02d} {'AM' if v.hour < 12 else 'PM'}"
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return f"{v.month}/{v.day}/{v.year}"
    # Infinities, fractional seconds, zones, times and durations aren't settled by a Desktop refresh yet.
    raise _error(f"Converting a {kind_of(v)} value to text isn't supported yet.")


def to_datetime(v: Any, culture: str) -> Any:
    if v is None or isinstance(v, dt.datetime) and not v.tzinfo:
        return v
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return dt.datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        return datetime_from_text(v, culture)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return ole_to_datetime(v)
    raise _error(f"We cannot convert a value of type {kind_of(v)} to type DateTime.")


def to_date(v: Any, culture: str) -> Any:
    """Date.From. Text with a time of day is an error ("11/20/2015 10:59" fails in a Desktop refresh, #104)."""
    if isinstance(v, str):
        if v == "":
            return None
        d, timed = _parse_datetime(v)
        if timed:
            raise _error("We couldn't parse the input provided as a Date value.", "DataFormat.Error")
        return d.date()
    d = to_datetime(v, culture)
    return None if d is None else d.date()


def to_logical(v: Any, culture: str) -> Any:
    """Logical.From: "true" or "false" in any case (Microsoft's reference); a number is true unless 0."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v == "":
            return None           # a Desktop refresh: Logical.From("") is null (#104)
        low = v.lower()
        if low in ("true", "false"):
            return low == "true"
        raise _error("Could not convert to a logical.")
    if isinstance(v, (int, float)):
        return v != 0
    raise _error(f"We cannot convert a value of type {kind_of(v)} to type Logical.")


def _converter(t: MType) -> Optional[Callable[[Any, str], Any]]:
    """The conversion a column typed ``t`` gets; None for ``any`` (no conversion)."""
    if t.kind == "number":
        return {None: to_number, "Int64.Type": to_int64, "Currency.Type": to_currency,
                "Percentage.Type": to_number}.get(t.facet)
    return {"text": to_text, "datetime": to_datetime, "date": to_date, "logical": to_logical}.get(t.kind)


# --- the functions -----------------------------------------------------------

def _binary_from_text(text: Any, encoding: Any = 0) -> Any:
    if text is None:
        return None
    _need(text, "text", "Binary.FromText")
    try:
        if encoding is None or type(encoding) is int and encoding == 0:
            return base64.b64decode("".join(text.split()), validate=True)
        if type(encoding) is int and encoding == 1:
            return bytes.fromhex(text)
    except (binascii.Error, ValueError):
        raise _error("The text isn't valid for its binary encoding.", "DataFormat.Error") from None
    raise _error(f"The binary encoding {encoding!r} isn't supported.")


def _binary_decompress(data: Any, compression: Any) -> Any:
    if data is None:
        return None
    _need(data, "binary", "Binary.Decompress")
    try:
        if type(compression) is int and compression == 1:   # Deflate: raw, as Desktop's "Enter data" stores rows
            return zlib.decompress(data, -zlib.MAX_WBITS)
        if type(compression) is int and compression == 0:   # Compression.GZip
            return gzip.decompress(data)
    except (zlib.error, OSError, EOFError):
        raise _error("The data couldn't be decompressed.", "DataFormat.Error") from None
    raise _error(f"The compression type {compression!r} isn't supported.")


def _json_value(v: Any) -> Any:
    if isinstance(v, list):
        return [_json_value(x) for x in v]
    if isinstance(v, dict):
        return Record.of({k: _json_value(x) for k, x in v.items()})
    return v


def _no_constant(name: str) -> Any:
    raise _error(f"We found an error in the JSON ({name} isn't JSON).", "DataFormat.Error")


def _json_document(source: Any, encoding: Any = None) -> Any:
    if isinstance(source, bytes):
        if encoding not in (None, 65001):
            raise _error(f"The text encoding {encoding!r} isn't supported.")
        try:
            source = source.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise _error("The JSON isn't valid UTF-8.", "DataFormat.Error") from None
    _need(source, "text", "Json.Document")
    try:
        return _json_value(json.loads(source, parse_float=float, parse_constant=_no_constant))
    except json.JSONDecodeError as e:
        raise _error(f"We found an error in the JSON ({e.msg}).", "DataFormat.Error") from None


def _columns(spec: Any, width: int, what: str) -> tuple[list[str], list[MType]]:
    """A Table.FromRows / #table columns argument: null, a count, a list of names or a table type."""
    if spec is None:
        return [f"Column{i + 1}" for i in range(width)], [ANY] * width
    if isinstance(spec, (int, float)) and not isinstance(spec, bool) and what != "Table.Combine":
        if not float(spec).is_integer() or spec < 0:
            raise _error(f"{what} needs a whole number of columns.")
        n = int(spec)
        return [f"Column{i + 1}" for i in range(n)], [ANY] * n
    if isinstance(spec, list):
        for name in spec:
            _need(name, "text", what)
        return list(spec), [ANY] * len(spec)
    if isinstance(spec, MType) and spec.kind == "table" and spec.fields is not None:
        return [n for n, _ in spec.fields], [t for _, t in spec.fields]
    raise _error(f"{what} needs column names, a column count or a table type.")


def _rows_table(rows: Any, columns: Any, what: str) -> Table:
    if not isinstance(rows, list):
        raise _error(f"{what} needs a list of rows, not {kind_of(rows)}.")
    for r in rows:
        if not isinstance(r, list):
            raise _error(f"{what} needs each row to be a list, not {kind_of(r)}.")   # a failed row raises its error
    width = max((len(r) for r in rows), default=0)
    names, types = _columns(columns, width, what)
    if len(set(names)) != len(names):
        raise _error(f"{what}: a column name is repeated.")
    for i, r in enumerate(rows):
        if len(r) != len(names):
            raise _error(f"{what}: row {i} has {len(r)} values and the table {len(names)} columns.")
    return Table(names, [list(r) for r in rows], types)


def _transform_column_types(evaluator: Any) -> Function:
    def call(table: Any, transforms: Any, culture: Any = None) -> Table:
        _need(table, "table", "Table.TransformColumnTypes")
        if isinstance(culture, Record):
            if any(k != "Culture" for k in culture.fields):
                raise _error("Table.TransformColumnTypes: only a culture record's Culture field is supported.")
            culture = culture["Culture"] if "Culture" in culture else None
        name = _culture(culture, evaluator.culture)
        if not isinstance(transforms, list):
            raise _error("Table.TransformColumnTypes needs a list of {column, type} pairs.")
        transforms = [force(x) for x in transforms]
        pairs = [transforms] if len(transforms) == 2 and isinstance(transforms[0], str) else transforms
        rows = [list(r) for r in table.rows]
        types = list(table.types)
        for pair in pairs:
            pair = [force(x) for x in pair] if isinstance(pair, list) else pair
            if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str) and isinstance(pair[1], MType)):
                raise _error("Table.TransformColumnTypes needs {column, type} pairs.")
            col, t = pair
            i = table.index(col)
            if t.kind in ("list", "record", "table", "function"):
                raise _error(f"A column can't be converted to type {t.kind}.")
            conv = _converter(t)
            if conv is None and t.kind != "any":
                raise _error(f"Converting a column to {t.facet or t.kind} isn't supported yet.")
            if conv is not None:
                for r in rows:
                    cell = r[i]
                    if isinstance(cell, CellError):
                        continue
                    try:
                        r[i] = conv(cell, name)
                    except MError as e:
                        r[i] = CellError(e)
            types[i] = t
        return Table(list(table.columns), rows, types)
    return Function("Table.TransformColumnTypes", call, 2, 3)


def _table_combine(tables: Any, columns: Any = None) -> Table:
    if not isinstance(tables, list):
        raise _error("Table.Combine needs a list of tables.")
    for t in tables:
        _need(t, "table", "Table.Combine")
    given: Optional[list[MType]] = None
    if columns is None:
        names: list[str] = []
        for t in tables:
            names += [c for c in t.columns if c not in names]
    else:
        names, given = _columns(columns, 0, "Table.Combine")
        if not (isinstance(columns, MType)):
            given = None
    types = []
    for k, n in enumerate(names):
        if given is not None:
            types.append(given[k])
            continue
        seen = {t.types[t.columns.index(n)] for t in tables if n in t.columns}
        types.append(seen.pop() if len(seen) == 1 and all(n in t.columns for t in tables) else ANY)
    rows = [[r[t.columns.index(n)] if n in t.columns else None for n in names] for t in tables for r in t.rows]
    return Table(names, rows, types)


def _fn(name: str, call: Callable[..., Any], lo: int, hi: int) -> Function:
    return Function(name, call, lo, hi)


# --- #date, #datetime, #datetimezone, #time, #duration (the M specification's constructors) ----------------

def _whole(v: Any, what: str) -> int:
    if not (isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer()):
        raise _error(f"{what} needs whole numbers.")
    return int(v)


def _seconds(v: Any, what: str) -> tuple[int, int]:
    """Whole seconds and microseconds of a seconds argument (fractions to 7 places in M; 6 kept here)."""
    if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)):
        raise _error(f"{what} needs a number of seconds.")
    whole = math.floor(v)
    return whole, round((Decimal(str(v)) - whole) * 1_000_000)


_V = TypeVar("_V")


def _make(what: str, build: Callable[[], _V]) -> _V:
    try:
        return build()
    except (ValueError, OverflowError):
        raise _error(f"The arguments of {what} don't make a valid value.") from None


def _date(y: Any, m: Any, d: Any) -> dt.date:
    return _make("#date", lambda: dt.date(_whole(y, "#date"), _whole(m, "#date"), _whole(d, "#date")))


def _time_parts(h: Any, mi: Any, s: Any, what: str) -> tuple[int, int, int, int]:
    sec, micro = _seconds(s, what)
    if not 0 <= sec < 60 or micro >= 1_000_000:
        raise _error(f"The arguments of {what} don't make a valid value.")
    return _whole(h, what), _whole(mi, what), sec, micro


def _datetime(y: Any, m: Any, d: Any, h: Any, mi: Any, s: Any) -> dt.datetime:
    hh, mm, ss, us = _time_parts(h, mi, s, "#datetime")
    return _make("#datetime", lambda: dt.datetime(_whole(y, "#datetime"), _whole(m, "#datetime"),
                                                  _whole(d, "#datetime"), hh, mm, ss, us))


def _datetimezone(y: Any, m: Any, d: Any, h: Any, mi: Any, s: Any, oh: Any, om: Any) -> dt.datetime:
    base = _datetime(y, m, d, h, mi, s)
    hours, minutes = _whole(oh, "#datetimezone"), _whole(om, "#datetimezone")
    if hours * minutes < 0 or not -14 * 60 <= hours * 60 + minutes <= 14 * 60:
        raise _error("The arguments of #datetimezone don't make a valid value.")
    return base.replace(tzinfo=dt.timezone(dt.timedelta(hours=hours, minutes=minutes)))


def _time(h: Any, mi: Any, s: Any) -> dt.time:
    hh, mm, ss, us = _time_parts(h, mi, s, "#time")
    return _make("#time", lambda: dt.time(hh, mm, ss, us))


def _duration(d: Any, h: Any, mi: Any, s: Any) -> dt.timedelta:
    for v in (d, h, mi, s):
        if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)):
            raise _error("#duration needs numbers.")
    return _make("#duration", lambda: dt.timedelta(days=d, hours=h, minutes=mi, seconds=s))


# A value, or a callable that takes the evaluator (for functions that read its culture).
LIBRARY: dict[str, Any] = {
    "Binary.FromText": _fn("Binary.FromText", _binary_from_text, 1, 2),
    "BinaryEncoding.Base64": 0,
    "BinaryEncoding.Hex": 1,
    "Binary.Decompress": _fn("Binary.Decompress", _binary_decompress, 2, 2),
    "Compression.GZip": 0,
    "Compression.Deflate": 1,
    "Json.Document": _fn("Json.Document", _json_document, 1, 2),
    "Table.FromRows": _fn("Table.FromRows", lambda rows, columns=None: _rows_table(rows, columns, "Table.FromRows"), 1, 2),
    "#table": _fn("#table", lambda columns, rows: _rows_table(rows, columns, "#table"), 2, 2),
    "Table.TransformColumnTypes": _transform_column_types,
    "Table.Combine": _fn("Table.Combine", _table_combine, 1, 2),
    "#date": _fn("#date", _date, 3, 3),
    "#datetime": _fn("#datetime", _datetime, 6, 6),
    "#datetimezone": _fn("#datetimezone", _datetimezone, 8, 8),
    "#time": _fn("#time", _time, 3, 3),
    "#duration": _fn("#duration", _duration, 4, 4),
    "#infinity": float("inf"),
    "#nan": float("nan"),
    "Int64.Type": INT64_TYPE,
    "Currency.Type": CURRENCY_TYPE,
    "Percentage.Type": PERCENTAGE_TYPE,
}

# Tier 1 above; tier 2, the table functions a query's steps use; tier 3, local files.
from .sources import SOURCES  # noqa: E402  (they import this module's conversions lazily)
from .tables import TABLES  # noqa: E402

TIERS: dict[str, int] = {**{k: 1 for k in LIBRARY}, **{k: 2 for k in TABLES}, **{k: 3 for k in SOURCES}}
LIBRARY.update(TABLES)
LIBRARY.update(SOURCES)
LIBRARY_NAMES = frozenset(LIBRARY)
