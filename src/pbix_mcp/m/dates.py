"""Tier 1 of the library, continued: the date, duration, list and text functions a date table's query uses
(``Date.Year`` … ``Date.EndOfWeek``, ``Date.ToText``, ``Duration.TotalDays``, ``List.Dates``, ``Text.From``,
``Text.PadStart``) and the ``Day.*`` and ``Int8.Type`` values.

The rules are Power Query's own: each follows what Desktop's M engine does, as its behaviour is published by
Microsoft and as Desktop's refreshes show it, rule by rule (pivot-studio #105). Where a rule depends on a culture,
the culture is the function's argument, else the query's default (the model's culture). A first day of week left
out is the culture's. ``Date.ToText`` writes with .NET's date format language, which Power Query uses: the custom
specifiers, and the one-letter standard formats whose patterns a culture here holds. A format this module can't
write as Desktop would is refused with a reason, never guessed.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Optional

from . import dtparse
from .values import Function, MError, MType, Record, force, kind_of

INT8_TYPE = MType("number", facet="Int8.Type")
DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")   # Day.Type: 0 is Sunday

# Every date List.Dates can make lies in 0001-01-01..9999-12-31; past that many items every one is an error.
_MAX_ITEMS = (dt.date.max - dt.date.min).days + 1


def _error(message: str, reason: str = "Expression.Error") -> MError:
    return MError(reason, message)


# --- argument checks, as Power Query words them -------------------------------------------------------------
_KIND_NAME = {"null": "Null", "logical": "Logical", "number": "Number", "text": "Text", "binary": "Binary",
              "date": "Date", "datetime": "DateTime", "datetimezone": "DateTimeZone", "time": "Time",
              "duration": "Duration", "list": "List", "record": "Record", "table": "Table", "type": "Type",
              "function": "Function"}


def _seconds_source(td: dt.timedelta) -> str:
    s = td.seconds % 60 + td.microseconds / 1_000_000
    return str(int(s)) if float(s).is_integer() else repr(s)


def value_source(v: Any) -> Optional[str]:
    """A scalar as a cast error writes it (its M source, invariant), or None for a value written by its kind."""
    from .library import to_text
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return "#nan"
        if isinstance(v, float) and math.isinf(v):
            return "#infinity" if v > 0 else "-#infinity"
        try:
            return to_text(v, "en-US")
        except MError:
            return None
    if isinstance(v, dt.datetime):
        parts = [v.year, v.month, v.day, v.hour, v.minute, v.second]
        if v.tzinfo is None:
            return "#datetime({}, {}, {}, {}, {}, {})".format(*parts)
        off = v.utcoffset() or dt.timedelta(0)
        minutes = int(off.total_seconds() // 60)
        hours, mins = (abs(minutes) // 60, abs(minutes) % 60)
        sign = -1 if minutes < 0 else 1
        return "#datetimezone({}, {}, {}, {}, {}, {}, {}, {})".format(*parts, sign * hours, sign * mins)
    if isinstance(v, dt.date):
        return f"#date({v.year}, {v.month}, {v.day})"
    if isinstance(v, dt.time):
        return f"#time({v.hour}, {v.minute}, {v.second})"
    if isinstance(v, dt.timedelta):
        return f"#duration({v.days}, {v.seconds // 3600}, {v.seconds // 60 % 60}, {_seconds_source(v)})"
    return None


def cast_error(v: Any, type_name: str) -> MError:
    """A value of the wrong kind for a parameter: "We cannot convert the value … to type …." (text quoted and cut
    to 20 characters plus "..."), or "… a value of type … to type …." for a list, record, table, function."""
    if isinstance(v, str):
        shown = v[:20] + "..." if len(v) > 20 else v
        return _error(f'We cannot convert the value "{shown}" to type {type_name}.')
    src = value_source(v)
    if src is None:
        return _error(f"We cannot convert a value of type {_KIND_NAME[kind_of(v)]} to type {type_name}.")
    return _error(f"We cannot convert the value {src} to type {type_name}.")


def _int32(v: Any) -> int:
    """A number as a whole 32-bit integer: 2.5, NaN and the infinities are out of range, never truncated."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise cast_error(v, "Number")
    if isinstance(v, float) and not (math.isfinite(v) and v.is_integer()):
        raise _error("The number is out of range of a 32 bit integer value.")
    n = int(v)
    if not -2 ** 31 <= n < 2 ** 31:
        raise _error("The number is out of range of a 32 bit integer value.")
    return n


def _culture_data(culture: Any, default: str) -> dtparse.DateCulture:
    from .library import _culture
    return dtparse.CULTURE_DATA[_culture(culture, default)]


def _first_day(first: Any, default: int) -> int:
    """An optional Day.Type argument: null is the culture's first day of week; else a whole number 0..6."""
    if first is None:
        return default
    n = _int32(first)
    if not 0 <= n <= 6:
        raise _error("The Day value provided is invalid.")
    return n


def _clr_day(d: dt.date) -> int:
    """.NET's DayOfWeek: 0 is Sunday."""
    return (d.weekday() + 1) % 7


def _date_part(v: Any) -> Optional[dt.date]:
    """The date of a date, datetime or datetimezone (a datetimezone's own clock date, not UTC's); None for null."""
    if v is None:
        return None
    if isinstance(v, (dt.date, dt.datetime)) and not isinstance(v, bool):
        return v if not isinstance(v, dt.datetime) else v.date()
    raise _error("The Date value must contain the Date component.")


# --- Date.Year, Date.Month, Date.Day, Date.QuarterOfYear --------------------------------------------------------
def _year(v: Any) -> Optional[int]:
    d = _date_part(v)
    return None if d is None else d.year


def _month(v: Any) -> Optional[int]:
    d = _date_part(v)
    return None if d is None else d.month


def _day(v: Any) -> Optional[int]:
    d = _date_part(v)
    return None if d is None else d.day


def _quarter(v: Any) -> Optional[int]:
    d = _date_part(v)
    return None if d is None else (d.month + 2) // 3


# --- weeks: Date.DayOfWeek, Date.WeekOfYear, Date.StartOfWeek, Date.EndOfWeek ------------------------------------
def week_of_year(d: dt.date, first: int) -> int:
    """.NET's GregorianCalendar.GetWeekOfYear with CalendarWeekRule.FirstDay: week 1 holds 1 January (a partial
    week counts; not ISO 8601), and a new week starts on each ``first`` day."""
    doy = d.timetuple().tm_yday - 1
    jan1 = _clr_day(d) - doy % 7
    offset = (jan1 - first + 14) % 7
    return (doy + offset) // 7 + 1


def _shift(v: Any, days: int, end: bool) -> Any:
    """``v`` moved by ``days`` to the start of its day (``end``: the last tick of it), keeping its kind and zone."""
    kind = kind_of(v)
    name = {"date": "Date", "datetime": "DateTime", "datetimezone": "DateTimeZone"}[kind]
    try:
        d = (v if kind == "date" else v.date()) + dt.timedelta(days=days)
    except OverflowError:
        d = None
    if d is None:
        raise _error(f"The {name} operation failed because the resulting value falls outside the range of allowed "
                     f"values.")
    if kind == "date":
        return d
    t = dt.time(23, 59, 59, 999999) if end else dt.time(0)     # 23:59:59.9999999 in M; 6 digits kept here
    return dt.datetime.combine(d, t, tzinfo=v.tzinfo)


def _week_functions(evaluator: Any) -> dict[str, Function]:
    """The week functions with the query's culture's first day of week as their default (fixed when built)."""
    default = dtparse.CULTURE_DATA[evaluator.culture].first_day if evaluator.culture in dtparse.CULTURE_DATA else None

    def first_of(first: Any) -> int:
        if first is None and default is None:
            from .library import _culture
            _culture(None, evaluator.culture)          # raises: the query's culture isn't one this library knows
        return _first_day(first, default)  # type: ignore[arg-type]

    def day_of_week(v: Any, first: Any = None) -> Optional[int]:
        if v is None:
            return None
        if not isinstance(v, dt.date):
            raise _error("We expected a Date value with a Days part.")
        d = v.date() if isinstance(v, dt.datetime) else v
        return (_clr_day(d) - first_of(first) + 7) % 7

    def week(v: Any, first: Any = None) -> Optional[int]:
        d = _date_part(v)
        if d is None:
            return None
        return week_of_year(d, first_of(first))

    def start(v: Any, first: Any = None) -> Any:
        d = _date_part(v)
        if d is None:
            return None
        return _shift(v, -((_clr_day(d) - first_of(first) + 7) % 7), end=False)

    def end(v: Any, first: Any = None) -> Any:
        d = _date_part(v)
        if d is None:
            return None
        last = first_of(first) - 1
        return _shift(v, (last - _clr_day(d) + 7) % 7, end=True)

    return {"Date.DayOfWeek": Function("Date.DayOfWeek", day_of_week, 1, 2),
            "Date.WeekOfYear": Function("Date.WeekOfYear", week, 1, 2),
            "Date.StartOfWeek": Function("Date.StartOfWeek", start, 1, 2),
            "Date.EndOfWeek": Function("Date.EndOfWeek", end, 1, 2)}


def _week_function(name: str):
    return lambda evaluator: _week_functions(evaluator)[name]


# --- Date.ToText: .NET's date format language ------------------------------------------------------------------
_ISO_ALIASES = {"YYYY-MM-DD": "yyyy-MM-dd", "YYYYMMDD": "yyyyMMdd", "M/d/yyyy": "M/d/yyyy"}
_INVARIANT_STANDARD = {      # culture-free patterns, written with the invariant culture
    "o": "yyyy'-'MM'-'dd'T'HH':'mm':'ss.fffffffK", "O": "yyyy'-'MM'-'dd'T'HH':'mm':'ss.fffffffK",
    "r": "ddd, dd MMM yyyy HH':'mm':'ss 'GMT'", "R": "ddd, dd MMM yyyy HH':'mm':'ss 'GMT'",
    "s": "yyyy'-'MM'-'dd'T'HH':'mm':'ss", "u": "yyyy'-'MM'-'dd HH':'mm':'ss'Z'",
}
_TIME_STANDARD = "fFgtU"     # the short time pattern isn't measured here; U needs the machine's zone


class _BadFormat(Exception):
    """.NET's FormatException: the format can't be read."""


def _repeat(fmt: str, i: int) -> int:
    n = 1
    while i + n < len(fmt) and fmt[i + n] == fmt[i]:
        n += 1
    return n


def _quoted(fmt: str, i: int) -> tuple[str, int]:
    """The literal text of a quoted run starting at ``fmt[i]`` (a backslash escapes the next character), and its
    length with both quotes."""
    q, out, j = fmt[i], [], i + 1
    while j < len(fmt):
        c = fmt[j]
        if c == q:
            return "".join(out), j - i + 1
        if c == "\\":
            j += 1
            if j >= len(fmt):
                raise _BadFormat
            c = fmt[j]
        out.append(c)
        j += 1
    raise _BadFormat                      # no closing quote


def _custom(fmt: str, v: dt.datetime, c: dtparse.DateCulture) -> str:
    """.NET's DateTimeFormat.FormatCustomized for a value with no zone (a date is its midnight)."""
    out: list[str] = []
    i = 0
    while i < len(fmt):
        ch = fmt[i]
        n = 1
        if ch == "g":
            n = _repeat(fmt, i)
            out.append(c.eras[0])
        elif ch in "hHms":
            n = _repeat(fmt, i)
            value = {"h": v.hour % 12 or 12, "H": v.hour, "m": v.minute, "s": v.second}[ch]
            out.append(str(value).zfill(min(n, 2)))
        elif ch in "fF":
            n = _repeat(fmt, i)
            if n > 7:
                raise _BadFormat
            ticks = v.microsecond * 10                     # 100 ns ticks of the second
            digits = str(ticks // 10 ** (7 - n)).zfill(n)
            if ch == "F":
                digits = digits.rstrip("0")
                if not digits and out and out[-1].endswith("."):
                    out[-1] = out[-1][:-1]                 # .NET drops the point before an empty F
            out.append(digits)
        elif ch == "t":
            n = _repeat(fmt, i)
            mark = c.am if v.hour < 12 else c.pm
            out.append(mark[:1] if n == 1 else mark)
        elif ch == "d":
            n = _repeat(fmt, i)
            if n <= 2:
                out.append(str(v.day).zfill(n))
            else:
                names = c.abbr_days if n == 3 else c.days
                out.append(names[_clr_day(v.date())])
        elif ch == "M":
            n = _repeat(fmt, i)
            if n <= 2:
                out.append(str(v.month).zfill(n))
            else:
                out.append((c.abbr_months if n == 3 else c.months)[v.month - 1])
        elif ch == "y":
            n = _repeat(fmt, i)
            out.append(str(v.year % 100).zfill(n) if n <= 2 else str(v.year).zfill(n))
        elif ch == "z":
            # A date or datetime has no zone, so .NET writes the machine's own offset: Desktop's, not the model's.
            raise _Unsupported("Date.ToText: a zone ('z') in a date's format isn't supported yet.")
        elif ch == "K":
            pass                                           # no zone: K writes nothing
        elif ch == ":":
            out.append(c.time_sep)
        elif ch == "/":
            out.append(c.date_sep)
        elif ch in "'\"":
            text, n = _quoted(fmt, i)
            out.append(text)
        elif ch == "%":
            if i + 1 >= len(fmt) or fmt[i + 1] == "%":
                raise _BadFormat
            out.append(_custom(fmt[i + 1], v, c))
            n = 2
        elif ch == "\\":
            if i + 1 >= len(fmt):
                raise _BadFormat
            out.append(fmt[i + 1])
            n = 2
        else:
            out.append(ch)
        i += n
    return "".join(out)


class _Unsupported(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def format_date(d: dt.date, fmt: str, c: dtparse.DateCulture) -> str:
    """``DateTime.ToString(fmt, culture)`` for a date at midnight, after Power Query's ISO aliases."""
    fmt = _ISO_ALIASES.get(fmt, fmt)
    v = dt.datetime(d.year, d.month, d.day)
    if len(fmt) == 1:
        if fmt in _INVARIANT_STANDARD:
            return _custom(_INVARIANT_STANDARD[fmt], v, dtparse.CULTURE_DATA[""])
        pattern = {"d": c.short_date, "D": c.long_date, "m": c.month_day, "M": c.month_day, "y": c.year_month,
                   "Y": c.year_month, "T": c.long_time, "G": f"{c.short_date} {c.long_time}"}.get(fmt)
        if pattern is None:
            if fmt in _TIME_STANDARD:
                raise _Unsupported(f"Date.ToText: the standard format '{fmt}' isn't supported yet.")
            raise _BadFormat
        return _custom(pattern, v, c)
    if fmt == "":
        return _custom(f"{c.short_date} {c.long_time}", v, c)          # .NET reads "" as "G"
    return _custom(fmt, v, c)


def _date_to_text(evaluator: Any) -> Function:
    def call(date: Any, options: Any = None, culture: Any = None) -> Optional[str]:
        if date is None:
            return None
        if kind_of(date) != "date":
            raise cast_error(date, "Date")
        if isinstance(options, Record):
            raise _error("Date.ToText: an options record isn't supported yet.")
        if options is not None and not isinstance(options, str):
            raise cast_error(options, "Text")
        if culture is not None and not isinstance(culture, str):
            raise cast_error(culture, "Text")
        c = _culture_data(culture, evaluator.culture)
        fmt = c.short_date if options is None else options
        try:
            return format_date(date, fmt, c)
        except _BadFormat:
            raise MError("Expression.Error", "The output DateTime format specified isn't supported.", fmt) from None
        except _Unsupported as e:
            raise _error(e.message) from None
    return Function("Date.ToText", call, 1, 3)


# --- Duration.TotalDays, List.Dates -------------------------------------------------------------------------------
def _total_days(v: Any) -> Optional[float]:
    if v is None:
        return None
    if kind_of(v) != "duration":
        raise cast_error(v, "Duration")
    return v / dt.timedelta(days=1)


def _list_dates(start: Any, count: Any, step: Any) -> list[Any]:
    """``count`` dates from ``start``, ``step`` apart: item i is start + step × i while i < count (so a fractional
    count (so a fractional
    count rounds up); a step's time of day is dropped from each date. A date past the calendar's range fails the
    list: Desktop raises it as the list is read, so a table made from it fails whole (#105's probe)."""
    if kind_of(start) != "date":
        raise cast_error(start, "Date")
    if kind_of(count) != "number":
        raise cast_error(count, "Number")
    if kind_of(step) != "duration":
        raise cast_error(step, "Duration")
    if count < 0:
        raise MError("Expression.Error", "The 'count' argument is out of range.", count)
    if math.isnan(count):
        return []
    n = math.ceil(count) if math.isfinite(count) else math.inf
    if n > _MAX_ITEMS:
        raise _error("List.Dates: a list this long isn't supported yet.")
    out: list[Any] = []
    base = dt.datetime(start.year, start.month, start.day)
    ticks = (step.days * 86400 + step.seconds) * 10_000_000 + step.microseconds * 10
    for i in range(int(n)):
        try:
            total = ticks * i
            if abs(total) > dt.timedelta.max.days * 86400 * 10_000_000:
                raise OverflowError
            out.append((base + dt.timedelta(microseconds=total // 10)).date())
        except OverflowError:
            raise _error("The Date operation failed because the resulting value falls outside the range of allowed "
                         "values.") from None
    return out


# --- Text.From, Text.PadStart -----------------------------------------------------------------------------------
def _text_from(evaluator: Any) -> Function:
    def call(value: Any, culture: Any = None) -> Optional[str]:
        from .library import _culture, to_text
        if culture is not None and not isinstance(culture, str):
            raise cast_error(culture, "Text")
        name = _culture(culture, evaluator.culture)
        if kind_of(value) in ("list", "record", "table", "function", "type"):
            raise cast_error(value, "Text")
        return to_text(value, name)
    return Function("Text.From", call, 1, 2)


def _pad_start(text: Any, count: Any, character: Any = None) -> Optional[str]:
    """Pads on the left to ``count`` characters (Unicode characters, not UTF-16 units); the padding character must be
    one character even when the text is long enough."""
    if count is None or kind_of(count) != "number":
        raise cast_error(count, "Number")
    if text is not None and not isinstance(text, str):
        raise cast_error(text, "Text")
    if character is not None and not isinstance(character, str):
        raise cast_error(character, "Text")
    if text is None:
        return None
    n = _int32(count)
    if n < 0:
        raise MError("Expression.Error", "The 'count' argument is out of range.", count)
    pad = " " if character is None else character
    if len(pad) != 1:          # checked even when nothing is padded (a Desktop refresh, #105's probe)
        raise _error("The value isn't a single-character string.")
    if len(text) >= n:
        return text
    return pad * (n - len(text)) + text


DATES: dict[str, Any] = {
    "Date.Year": Function("Date.Year", _year, 1, 1),
    "Date.Month": Function("Date.Month", _month, 1, 1),
    "Date.Day": Function("Date.Day", _day, 1, 1),
    "Date.QuarterOfYear": Function("Date.QuarterOfYear", _quarter, 1, 1),
    "Date.DayOfWeek": _week_function("Date.DayOfWeek"),
    "Date.WeekOfYear": _week_function("Date.WeekOfYear"),
    "Date.StartOfWeek": _week_function("Date.StartOfWeek"),
    "Date.EndOfWeek": _week_function("Date.EndOfWeek"),
    "Date.ToText": _date_to_text,
    "Duration.TotalDays": Function("Duration.TotalDays", _total_days, 1, 1),
    "List.Dates": Function("List.Dates", _list_dates, 3, 3),
    "Text.From": _text_from,
    "Text.PadStart": Function("Text.PadStart", _pad_start, 2, 3),
    "Int8.Type": INT8_TYPE,
    **{f"Day.{name}": i for i, name in enumerate(DAYS)},
}
