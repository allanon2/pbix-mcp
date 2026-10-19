"""Load an evaluated M table into a model table's columns: each cell becomes a
value of its model column's data type.

A model column's type is the model's own (it can differ from the M column's
type when the model was edited after the query). Rules, each from Desktop's
saved refreshes unless it is the identity:

- a number into a dateTime column is an OLE Automation date (days since
  1899-12-30): Microsoft's Revenue Opportunities sample loads an Int64.Type
  column of 41679 into a dateTime column as 2014-02-09;
- numbers into int64, double and decimal columns, text into string, logical
  into boolean, date and datetime into dateTime keep their value (a date at
  midnight); a decimal column holds 4 decimal places;
- a failed cell is null when the model returns error values as null
  (returnErrorValuesAsNull), otherwise loading the table fails.

Any other pairing is an error naming it, not a guess.
"""
from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Sequence

from .values import CellError, MError, Table, kind_of

DATA_TYPES = ("string", "int64", "double", "decimal", "boolean", "dateTime")
_OLE_EPOCH = dt.datetime(1899, 12, 30)


def model_value(value: Any, data_type: str) -> Any:
    """``value`` as the model stores it in a column of ``data_type`` (pbix-mcp's names: string, int64, double,
    decimal, boolean, dateTime)."""
    if value is None:
        return None
    kind = kind_of(value)
    if data_type == "string" and kind == "text":
        return value
    if data_type == "boolean" and kind == "logical":
        return value
    if data_type == "int64" and kind == "number" and float(value).is_integer():
        return int(value)
    if data_type == "double" and kind == "number":
        return float(value)
    if data_type == "decimal" and kind == "number":
        return float(Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN))
    if data_type == "dateTime":
        if kind == "datetime":
            return value
        if kind == "date":
            return dt.datetime(value.year, value.month, value.day)
        if kind == "number":
            return _OLE_EPOCH + dt.timedelta(days=value)
    raise MError("Expression.Error", f"A {kind} value can't be loaded into a {data_type} column yet.")


def load_rows(table: Table, columns: Sequence[tuple[str, str]], *, errors_as_null: bool) -> list[list[Any]]:
    """The model's rows for ``columns`` (``(name, data type)`` pairs, names as the M table's columns)."""
    idx = [table.index(name) for name, _ in columns]
    out = []
    for row in table.rows:
        cells: list[Any] = []
        for i, (name, data_type) in zip(idx, columns):
            cell = row[i]
            if isinstance(cell, CellError):
                if not errors_as_null:
                    raise cell.error
                cells.append(None)
                continue
            cells.append(model_value(cell, data_type))
        out.append(cells)
    return out
