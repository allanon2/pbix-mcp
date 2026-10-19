"""Load an evaluated M table into a model table's columns: each cell becomes a
value of its model column's data type.

A model column's type is the model's own (it can differ from the M column's
type when the model was edited after the query). Two steps, each rule as a
Desktop refresh shows it (#104's load probes, Desktop 2.157; and Microsoft's
Revenue Opportunities sample, whose Int64.Type column of 41679 loads into a
dateTime column as 2014-02-09):

1. A cell that doesn't conform to its M column's type is a failed cell: 2.5,
   "5" or true in an Int64.Type column, 5 in a text column.
2. The model column converts the value:
   - int64: a number rounds half away from zero (2.5 is 3, -2.5 is -3); a
     logical is -1 or 0; text of digits with an optional sign, fraction and
     surrounding whitespace is read so (".5" is 1; "" and "   " are 0; "1."
     fails), each digit worth its code less "0"'s, so "５" is 65253;
   - double: a number; text as en-US numbers read ("1,234", "1e3", "$5",
     "-$5", "1."), finite;
   - decimal: 4 decimal places, half to even, of the number's exact binary
     value (1.23465 is 1.2347, 0.03125 is 0.0312), or of its decimal digits in
     a Currency.Type column (1.23465 is 1.2346);
   - boolean: a logical; a number is true unless 0 (NaN too); "true"/"false"
     in any case, exactly, or digits (" 1 " is true, " TRUE " fails);
   - string: text without trailing spaces, tabs, CR, LF and VT ("x " is "x",
     " " is ""; U+3000 and NBSP stay);
     a logical is "TRUE" or "FALSE"; and one column holds one spelling per
     value under ASCII case folding, the first loaded ("a" then "A" both load
     as "a"; "É" and "é" stay apart);
   - dateTime: a date at midnight; a number or logical as an OLE Automation
     date (true is -1, 1899-12-29); a datetimezone's clock time without its
     offset; a time on 1899-12-30; a duration as days after 1899-12-30. The
     time of day is kept in 1/300 s: a datetime's truncated to the
     millisecond, then rounded half up; a number's rounded half up.
   A value the model column can't take fails the whole load, as it fails
   Desktop's refresh (1e300 into int64, "abc" into int64).

A failed cell is null when the model returns error values as null
(returnErrorValuesAsNull), otherwise loading the table fails. Pairings no
refresh has settled -- numbers and dates into string, text into dateTime
(read by the machine's culture), anything else -- are errors naming them, not
guesses.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from .library import number_from_text, ole_to_datetime
from .values import CellError, MError, MType, Table, kind_of

DATA_TYPES = ("string", "int64", "double", "decimal", "boolean", "dateTime")
_DECIMAL = (Decimal("-922337203685477.5808"), Decimal("922337203685477.5807"))   # a currency's range
_INT64 = (-(2 ** 63), 2 ** 63 - 1)
_SPACE = " \t\n\r\x0b\x0c"
_PLAIN = re.compile(r"^[ \t\n\r\x0b\x0c]*[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)[ \t\n\r\x0b\x0c]*$")
_EN_NUMBER = re.compile(r"^[ \t\n\r\x0b\x0c]*[+-]?\$?[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]*)(?:\.[0-9]*)?"
                        r"(?:[eE][+-]?[0-9]+)?[ \t\n\r\x0b\x0c]*$")
_TRAILING = " \t\r\n\x0b"     # what a string column drops from a value's end (U+3000 and NBSP stay)
_TICKS_PER_DAY = 300 * 86400    # a dateTime column keeps time in 1/300 s
_OLE_EPOCH = dt.datetime(1899, 12, 30)
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _fail(message: str) -> MError:
    return MError("Expression.Error", message)


def conforms(value: Any, m_type: Optional[MType]) -> bool:
    """Whether a value may stand in a column of ``m_type`` (step 1)."""
    if value is None or m_type is None or m_type.kind in ("any", "anynonnull"):
        return True
    if kind_of(value) != m_type.kind:
        return False
    if m_type.facet == "Int64.Type":
        return math.isfinite(value) and float(value).is_integer()
    return True


def _int64(n: Any) -> int:
    if not math.isfinite(n):
        raise _fail("A non-finite number can't be loaded into an int64 column.")
    r = int(Decimal(n).to_integral_value(rounding=ROUND_HALF_UP))   # half away from zero
    if not _INT64[0] <= r <= _INT64[1]:
        raise _fail("The number is out of range of an int64 column.")
    return r


def _digit(c: str) -> Optional[int]:
    """A character's worth as an int64 column reads text: its code less "0"'s, for any decimal digit of the Basic
    Multilingual Plane, not only ASCII ("５" U+FF15 is 65253, "٥" 1589, "²" 130), as a Desktop refresh shows (#104,
    13 points). Digits the refresh refused ("⅕", "Ⅻ", the astral "𝟓"), or that none measured ("¹", "③"), are None."""
    if "0" <= c <= "9" or ord(c) <= 0xFFFF and c.isdecimal() or c == "\u00b2":
        return ord(c) - 48
    return None


def _int64_text(text: str) -> int:
    """Text into an int64 column (#104): surrounding whitespace, a sign, digits (``_digit``), and optionally "." and
    more digits, the first of which rounds the whole number up when it is worth 5 or more ("2.5" is 3, "-0.5" is
    -1, ".5" is 1). Empty or blank text is 0; no exponent, grouping or "$"; "1." fails."""
    s = text.strip(_SPACE)
    if s == "":
        return 0
    sign = -1 if s[0] == "-" else 1
    if s[0] in "+-":
        s = s[1:]
    whole, _, frac = s.partition(".")
    digits = [_digit(c) for c in whole]
    fraction = [_digit(c) for c in frac]
    if (not whole and not frac or "." in s and not frac or None in digits or None in fraction):
        raise _fail(f"The text '{text}' can't be loaded into an int64 column.")
    n = 0
    for d in digits:
        n = n * 10 + d  # type: ignore[operator]
    if fraction and fraction[0] >= 5:  # type: ignore[operator]
        n += 1
    n *= sign
    if not _INT64[0] <= n <= _INT64[1]:
        raise _fail("The number is out of range of an int64 column.")
    return n


def _decimal(n: Any, m_type: Optional[MType]) -> float:
    if not math.isfinite(n):
        raise _fail("A non-finite number can't be loaded into a decimal column.")
    d = Decimal(str(n)) if m_type is not None and m_type.facet == "Currency.Type" else Decimal(n)
    try:
        d = d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        raise _fail("The number is out of range of a decimal column.") from None
    if not _DECIMAL[0] <= d <= _DECIMAL[1]:
        raise _fail("The number is out of range of a decimal column.")
    return float(d)


def _ole(days: float) -> dt.datetime:
    """A number of days as the column stores it: rounded half up to 1/300 s (#104: 1e-7 days is 10 ms)."""
    try:
        ole_to_datetime(days)
    except MError:
        raise _fail("The number is out of range of a dateTime column.") from None
    whole = math.trunc(days)       # the fraction is the time of day, below zero too (-1.25 is 1899-12-29 06:00)
    ticks = math.floor(abs(Decimal(days) - whole) * _TICKS_PER_DAY + Decimal("0.5"))
    return _ticks(_OLE_EPOCH + dt.timedelta(days=whole), ticks)


def _ticks(day: dt.datetime, ticks: int) -> dt.datetime:
    """``day`` plus ``ticks`` 300ths of a second, to the nearest microsecond."""
    return day + dt.timedelta(microseconds=round(Decimal(ticks) * 1_000_000 / 300))


def _stored_datetime(v: dt.datetime) -> dt.datetime:
    """A datetime as a dateTime column stores it, as Desktop's refresh shows (#104, 21 points): truncated to the
    millisecond, then rounded half up to 1/300 s (.0017 s is 0, .005 s is 2/300, 59.9999 s is the next minute)."""
    ms = (v.hour * 3600 + v.minute * 60 + v.second) * 1000 + v.microsecond // 1000
    midnight = dt.datetime(v.year, v.month, v.day)
    return _ticks(midnight, (3 * ms + 5) // 10)


def model_value(value: Any, data_type: str, m_type: Optional[MType] = None) -> Any:
    """``value`` as the model stores it in a column of ``data_type`` (pbix-mcp's names: string, int64, double,
    decimal, boolean, dateTime), from an M column of ``m_type`` (None: any). Step 2 only; the caller checks
    ``conforms`` first, and ``load_rows`` folds a string column's spellings."""
    if value is None:
        return None
    kind = kind_of(value)
    if data_type == "string":
        if kind == "text":
            return value.rstrip(_TRAILING)
        if kind == "logical":
            return "TRUE" if value else "FALSE"
    elif data_type == "int64":
        if kind == "number":
            return _int64(value)
        if kind == "logical":
            return -1 if value else 0
        if kind == "text":
            return _int64_text(value)
    elif data_type == "double":
        if kind == "number":
            return float(value)
        if kind == "text":
            if _EN_NUMBER.match(value) and any("0" <= c <= "9" for c in value):
                n = number_from_text(value.strip(_SPACE), "en-US")
                if n is not None and math.isfinite(n):        # "1e400" fails the load (#104)
                    return float(n)
            raise _fail(f"The text '{value}' can't be loaded into a double column.")
    elif data_type == "decimal":
        if kind == "number":
            return _decimal(value, m_type)
    elif data_type == "boolean":
        if kind == "logical":
            return value
        if kind == "number":
            return value != 0                 # NaN and infinities are true (#104)
        if kind == "text":
            low = value.lower()
            if low in ("true", "false"):      # exactly: " TRUE " fails the load (#104)
                return low == "true"
            if _PLAIN.match(value):
                return Decimal(value.strip(_SPACE)) != 0
            raise _fail(f"The text '{value}' can't be loaded into a boolean column.")
    elif data_type == "dateTime":
        if kind == "datetime":
            return _stored_datetime(value)
        if kind == "date":
            return dt.datetime(value.year, value.month, value.day)
        if kind == "number":
            if not math.isfinite(value):
                raise _fail("A non-finite number can't be loaded into a dateTime column.")
            return _ole(value)
        if kind == "logical":
            return _ole(-1 if value else 0)
        if kind == "datetimezone":
            return _stored_datetime(value.replace(tzinfo=None))
        if kind == "time":
            return _stored_datetime(dt.datetime.combine(_OLE_EPOCH.date(), value))
        if kind == "duration" and value >= dt.timedelta(0):
            return _stored_datetime(_OLE_EPOCH + value)
    raise _fail(f"A {kind} value can't be loaded into a {data_type} column yet.")


def load_rows(table: Table, columns: Sequence[tuple[str, str]], *, errors_as_null: bool) -> list[list[Any]]:
    """The model's rows for ``columns`` (``(name, data type)`` pairs, names as the M table's columns)."""
    idx = [table.index(name) for name, _ in columns]
    types = [table.types[i] if i < len(table.types) else None for i in idx]
    spellings: list[dict[str, str]] = [{} for _ in columns]
    out = []
    for row in table.rows:
        cells: list[Any] = []
        for k, (i, (_, data_type)) in enumerate(zip(idx, columns)):
            cell = row[i]
            m_type = types[k]
            if not isinstance(cell, CellError) and not conforms(cell, m_type):
                assert m_type is not None
                cell = CellError(MError("Expression.Error",
                                        f"We cannot convert the value to type {m_type.facet or m_type.kind}."))
            if isinstance(cell, CellError):
                if not errors_as_null:
                    raise cell.error
                cells.append(None)
                continue
            v = model_value(cell, data_type, m_type)
            if data_type == "string" and v is not None:
                v = spellings[k].setdefault(v.translate(_ASCII_LOWER), v)
            cells.append(v)
        out.append(cells)
    return out
