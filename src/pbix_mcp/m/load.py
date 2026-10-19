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
     surrounding spaces is read so ("" is 0);
   - double: a number; text as en-US numbers read ("1,234", "1e3", "$5");
   - decimal: 4 decimal places, half to even, of the number's exact binary
     value (1.23465 is 1.2347, 0.03125 is 0.0312), or of its decimal digits in
     a Currency.Type column (1.23465 is 1.2346);
   - boolean: a logical; a number is true unless 0; "true"/"false" in any case
     or digits;
   - string: text without trailing spaces and tabs ("x " is "x", " " is "");
     a logical is "TRUE" or "FALSE"; and one column holds one spelling per
     value under ASCII case folding, the first loaded ("a" then "A" both load
     as "a"; "É" and "é" stay apart);
   - dateTime: a date at midnight; a number or logical as an OLE Automation
     date (true is -1, 1899-12-29); a datetimezone's clock time without its
     offset; a time on 1899-12-30; a duration as days after 1899-12-30.
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
_PLAIN = re.compile(r"^\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*$")
_EN_NUMBER = re.compile(r"^\s*[+-]?\$?[+-]?(?:\d{1,3}(?:,\d{3})+|\d*)(?:\.\d*)?(?:[eE][+-]?\d+)?\s*$")
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
    try:
        return ole_to_datetime(days)
    except MError:
        raise _fail("The number is out of range of a dateTime column.") from None


def model_value(value: Any, data_type: str, m_type: Optional[MType] = None) -> Any:
    """``value`` as the model stores it in a column of ``data_type`` (pbix-mcp's names: string, int64, double,
    decimal, boolean, dateTime), from an M column of ``m_type`` (None: any). Step 2 only; the caller checks
    ``conforms`` first, and ``load_rows`` folds a string column's spellings."""
    if value is None:
        return None
    kind = kind_of(value)
    if data_type == "string":
        if kind == "text":
            return value.rstrip(" \t")
        if kind == "logical":
            return "TRUE" if value else "FALSE"
    elif data_type == "int64":
        if kind == "number":
            return _int64(value)
        if kind == "logical":
            return -1 if value else 0
        if kind == "text":
            if value.strip() == "":
                return 0
            if _PLAIN.match(value):
                return _int64(Decimal(value.strip()))
            raise _fail(f"The text '{value}' can't be loaded into an int64 column.")
    elif data_type == "double":
        if kind == "number":
            return float(value)
        if kind == "text":
            if _EN_NUMBER.match(value) and any(c.isdigit() for c in value):
                n = number_from_text(value.strip(), "en-US")
                assert n is not None            # digits were matched
                return float(n)
            raise _fail(f"The text '{value}' can't be loaded into a double column.")
    elif data_type == "decimal":
        if kind == "number":
            return _decimal(value, m_type)
    elif data_type == "boolean":
        if kind == "logical":
            return value
        if kind == "number":
            return value != 0
        if kind == "text":
            low = value.strip().lower()
            if low in ("true", "false"):
                return low == "true"
            if _PLAIN.match(value):
                return Decimal(value.strip()) != 0
            raise _fail(f"The text '{value}' can't be loaded into a boolean column.")
    elif data_type == "dateTime":
        if kind == "datetime":
            return value
        if kind == "date":
            return dt.datetime(value.year, value.month, value.day)
        if kind == "number":
            if not math.isfinite(value):
                raise _fail("A non-finite number can't be loaded into a dateTime column.")
            return _ole(value)
        if kind == "logical":
            return _ole(-1 if value else 0)
        if kind == "datetimezone":
            return value.replace(tzinfo=None)
        if kind == "time":
            return dt.datetime.combine(_OLE_EPOCH.date(), value)
        if kind == "duration" and value >= dt.timedelta(0):
            return _OLE_EPOCH + value
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
