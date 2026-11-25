"""Tier 2 of the library, continued: ordering, de-duplicating, buffering, numbering, counting and expanding a table
(Table.Sort with Order.*, Table.Distinct, Table.Buffer with BufferMode.*, Table.AddIndexColumn, Table.RowCount,
Table.ExpandRecordColumn), as Power Query evaluates them.

Two comparisons, as Power Query has them. A sort orders values with the *lax* comparison: values of different kinds
order by their kind (null, time, date, datetime, datetimezone, duration, number, logical, text), numbers as doubles with
NaN below every number, text by UTF-16 code units, datetimezones by their instant. Table.Distinct uses equality (the
``=`` operator's): different kinds are never equal, text is case-sensitive, datetimezones at one instant are equal, NaN
equals nothing.

Known differences, each to be measured: Power Query's sort is not stable (a .NET introsort; below 17 rows an insertion
sort, which is), this one is, so rows with equal keys in a larger table may come out in another order; a key that is a
list, record, table or function, and a binary key, are refused rather than ordered; Table.Distinct with a comparer
(Comparer.*) isn't supported yet.
"""
from __future__ import annotations

import functools
import math
from typing import Any, Callable

from .tables import _need_table, row_record
from .values import ANY, CellError, Function, LazyTable, MError, MType, Record, Table, force, kind_of

ASCENDING, DESCENDING = 0, 1                      # Order.Type
BUFFER_EAGER, BUFFER_DELAYED = 1, 2               # BufferMode.Type

# The kinds' order under the lax comparison (the engine's ValueKind numbers).
_KIND_RANK = {"null": 0, "time": 1, "date": 2, "datetime": 3, "datetimezone": 4, "duration": 5, "number": 6,
              "logical": 7, "text": 8, "binary": 9, "list": 10, "record": 11, "table": 12, "function": 13, "type": 14}
_STRUCTURED = ("list", "record", "table", "function", "type")
_SCALAR_KEYS = ("null", "logical", "number", "text", "date", "datetime", "datetimezone", "time", "duration", "binary")


def _error(message: str, detail: Any = None, reason: str = "Expression.Error") -> MError:
    return MError(reason, message, detail)


def _cast(value: Any, type_name: str) -> MError:
    from .dates import cast_error
    return cast_error(value, type_name)


def _kind_name(kind: str) -> str:
    from .dates import _KIND_NAME
    return _KIND_NAME[kind]


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_double(v: Any) -> float:
    try:
        return float(v)
    except OverflowError:                          # an int past a double's range is that double's infinity
        return math.inf if v > 0 else -math.inf


def lax_compare(a: Any, b: Any) -> int:
    """-1, 0 or 1 as a sort orders two values. A structured value on the left fails as the ``<`` operator does."""
    ka, kb = kind_of(a), kind_of(b)
    if ka in _STRUCTURED:
        raise _error(f"We cannot apply operator < to types {_kind_name(ka)} and {_kind_name(kb)}.",
                     Record.of({"Operator": "<", "Left": a, "Right": b}))
    if ka != kb:
        return -1 if _KIND_RANK[ka] < _KIND_RANK[kb] else 1
    if ka == "null":
        return 0
    if ka == "number":
        x, y = _as_double(a), _as_double(b)
        if math.isnan(x) or math.isnan(y):
            return 0 if math.isnan(x) and math.isnan(y) else -1 if math.isnan(x) else 1
        return (x > y) - (x < y)
    if ka == "text":
        x16, y16 = a.encode("utf-16-be"), b.encode("utf-16-be")
        return (x16 > y16) - (x16 < y16)
    if ka == "binary":
        raise _error("Table.Sort: ordering binary values isn't supported yet.")
    return (a > b) - (a < b)                       # dates, datetimes, datetimezones (by instant), times, durations, logicals


# --- Table.Sort -------------------------------------------------------------------------------------------------------

class SortedTable(LazyTable):
    """A sorted table: the criteria were checked when Table.Sort was called; the rows are sorted when first read,
    and an error while sorting (a failed key, a failing comparer) fails the whole table there. Its row count is the
    input's, read without sorting; a Table.Distinct directly over it runs below the sort."""

    def __init__(self, source: Table, criteria: Any, order: list[tuple[Any, bool]]):
        super().__init__(lambda: _sort_rows(source, order))
        self.source, self.criteria, self.order = source, criteria, order


def _direction(v: Any) -> bool:
    """True for ascending. The direction is Order.Ascending or Order.Descending by equality (0, 0.0 and 1 do)."""
    v = force(v)
    if _is_number(v) and v in (ASCENDING, DESCENDING):
        return v == ASCENDING
    raise _error("The specified sort direction is invalid.")


def _key(table: Table, key: Any) -> Any:
    """A column's index, or a key selector function."""
    key = force(key)
    if isinstance(key, str):
        return table.index(key)
    if isinstance(key, Function):
        return key
    raise _error("The specified column selector is invalid.")


def _criterion(table: Table, item: Any) -> tuple[Any, bool]:
    item = force(item)
    if isinstance(item, list):
        if len(item) != 2:
            raise _error("The specified sort criterion is invalid.")
        return _key(table, item[0]), _direction(item[1])
    return _key(table, item), True


def _sort_order(table: Table, criteria: Any) -> list[tuple[Any, bool]]:
    """The criteria's forms, in Power Query's order: a two-parameter function compares whole rows; a two-item list
    of a key (a column name or a function) and a number is one criterion; any other list is one criterion per item;
    anything else is one criterion."""
    if isinstance(criteria, Function) and criteria.max_args == 2:
        return [(("rows", criteria), True)]
    if isinstance(criteria, list):
        items = [force(c) for c in criteria]
        if len(items) == 2 and isinstance(items[0], (str, Function)) and _is_number(items[1]):
            return [_criterion(table, items)]
        return [_criterion(table, c) for c in items]
    return [_criterion(table, criteria)]


def _sort(table: Any, criteria: Any) -> Table:
    t = _need_table(table, "Table.Sort")
    criteria = force(criteria)
    try:
        order = _sort_order(t, criteria)
    except MError as inner:
        raise _error("The specified sort criteria is invalid.", criteria) from inner
    if not order:
        raise _error("'Table.Sort' requires at least one sort criterion.", t)
    return SortedTable(t, criteria, order)


def _sort_rows(source: Table, order: list[tuple[Any, bool]]) -> Table:
    from .dates import _int32
    rows = source.rows
    if len(rows) < 2:                              # a lone row is never compared, so its keys aren't read
        return Table(list(source.columns), [list(r) for r in rows], list(source.types))
    records = [row_record(source, r) for r in rows]

    def key_of(key: Any, i: int) -> Any:
        if isinstance(key, int):
            return force(rows[i][key])
        return key.invoke([records[i]])

    keys = [[None if isinstance(k, tuple) else key_of(k, i) for k, _ in order] for i in range(len(rows))]

    def compare(i: int, j: int) -> int:
        for n, (key, ascending) in enumerate(order):
            if isinstance(key, tuple):             # a whole-row comparer
                c = _int32(force(key[1].invoke([records[i], records[j]])))
                c = (c > 0) - (c < 0)
            else:
                c = lax_compare(keys[i][n], keys[j][n])
            if c:
                return c if ascending else -c
        return 0

    ordered = sorted(range(len(rows)), key=functools.cmp_to_key(compare))
    return Table(list(source.columns), [list(rows[i]) for i in ordered], list(source.types))


# --- Table.Distinct ---------------------------------------------------------------------------------------------------

def _distinct_columns(table: Table, criteria: Any) -> list[int]:
    """The columns compared: all of them (null), one (a name) or several (a list of names). A function, or a
    {selector, comparer} pair, needs a comparer from the library (Comparer.*), which isn't supported yet: any other
    function is "a custom comparer", which Power Query refuses here."""
    if criteria is None:
        return list(range(len(table.columns)))
    if isinstance(criteria, str):
        return [table.index(criteria)]
    if isinstance(criteria, Function):
        raise _error("A custom comparer cannot be used in this context.")
    if isinstance(criteria, list):
        items = [force(c) for c in criteria]
        if items and (isinstance(items[0], list) or len(items) == 2 and isinstance(items[1], Function)):
            raise _error("A custom comparer cannot be used in this context.")
        out = []
        for c in items:
            if not isinstance(c, str):
                raise _error("The specified column selector is invalid.")
            out.append(table.index(c))
        return out
    raise _error("The specified column selector is invalid.")


def _bucket(v: Any) -> Any:
    """A hashable stand-in agreeing with equality: equal values share it (1 and 1.0, datetimezones at one instant);
    values sharing it are then compared by equality itself (so NaN, which equals nothing, is always kept)."""
    kind = kind_of(v)
    return (kind, v) if kind in _SCALAR_KEYS else (kind,)


def _distinct_rows(t: Table, columns: list[int]) -> Table:
    from .evaluate import _equal
    if not columns:                                # no columns compared: every row equals the first
        return Table(list(t.columns), [list(r) for r in t.rows[:1]], list(t.types))
    seen: dict[tuple, list[list[Any]]] = {}
    kept = []
    for row in t.rows:
        values = [force(row[i]) for i in columns]
        group = seen.setdefault(tuple(_bucket(v) for v in values), [])
        if any(all(_equal(a, b) for a, b in zip(values, other)) for other in group):
            continue
        group.append(values)
        kept.append(list(row))
    return Table(list(t.columns), kept, list(t.types))


def _distinct(table: Any, criteria: Any = None) -> Table:
    t = _need_table(table, "Table.Distinct")
    criteria = force(criteria)
    if isinstance(t, SortedTable):                 # Power Query runs a distinct below the sort it reads
        inner = _distinct(t.source, criteria)
        return SortedTable(inner, t.criteria, t.order)   # the same columns, so the same checked order
    try:
        columns = _distinct_columns(t, criteria)
    except MError as inner:
        raise _error("The specified distinct criteria is invalid.", criteria) from inner
    return LazyTable(lambda: _distinct_rows(t, columns))


# --- Table.Buffer -----------------------------------------------------------------------------------------------------

def _buffer(table: Any, options: Any = None) -> Table:
    """The table's rows read now (BufferMode.Eager, the default) or when first read (BufferMode.Delayed), once: an
    error reading them is this step's. The cells are kept as they are (a failed cell stays a failed cell)."""
    from .dates import value_source
    t = _need_table(table, "Table.Buffer")
    mode = BUFFER_EAGER
    options = force(options)
    if options is not None:
        if not isinstance(options, Record):
            raise _cast(options, "Record")
        for name in options.fields:
            if name != "BufferMode":
                value = options[name]
                raise _error(f"This function doesn't support the query option '{name}' with value "
                             f"'{value_source(value) or kind_of(value)}'.")
        value = options["BufferMode"] if "BufferMode" in options else None
        if value is not None:
            if not _is_number(value):
                raise _cast(value, "Number")
            if value not in (BUFFER_EAGER, BUFFER_DELAYED):
                raise _error("The value for a 'BufferMode.Type' enumeration was not one of the allowed values: "
                             "'BufferMode.Eager, BufferMode.Delayed'.", value)
            mode = int(value)

    def read() -> Table:
        return Table(list(t.columns), [list(r) for r in t.rows], list(t.types))
    if mode == BUFFER_EAGER:
        return read()
    return LazyTable(read)


# --- Table.AddIndexColumn ---------------------------------------------------------------------------------------------

def _add_index_column(table: Any, name: Any, initial: Any = None, increment: Any = None, column_type: Any = None) -> Table:
    """A last column numbering the rows: the first row ``initial`` (0), each next the previous plus ``increment``
    (1), added row by row (so a fractional increment accumulates as doubles do). The column's type is ascribed as
    given (type number by default), not checked."""
    t = _need_table(table, "Table.AddIndexColumn")
    name, initial, increment, column_type = (force(v) for v in (name, initial, increment, column_type))
    if not isinstance(name, str):
        raise _cast(name, "Text")
    for v in (initial, increment):
        if v is not None and not _is_number(v):
            raise _cast(v, "Number")
    if column_type is not None and not isinstance(column_type, MType):
        raise _cast(column_type, "Type")
    if name in t.columns:
        raise _error(f"The field '{name}' already exists in the record.", Record.of({"Name": name, "Value": None}))
    value = 0 if initial is None else initial
    step = 1 if increment is None else increment
    rows = []
    for i, r in enumerate(t.rows):
        if i:
            value = value + step
        rows.append([*r, value])
    return Table([*t.columns, name], rows, [*t.types, column_type or MType("number", False)])


# --- Table.RowCount ---------------------------------------------------------------------------------------------------

def _row_count(table: Any) -> int:
    """The rows, counted without reading a cell (a failed cell is still a row). A sort's count is its input's."""
    t = _need_table(table, "Table.RowCount")
    while isinstance(t, SortedTable):
        t = t.source
    return len(t.rows)


# --- Table.ExpandRecordColumn -----------------------------------------------------------------------------------------

def _texts(items: list[Any]) -> list[str]:
    out = []
    for v in items:
        v = force(v)
        if not isinstance(v, str):
            raise _cast(v, "Text")
        out.append(v)
    return out


def _field_types(column_type: MType, fields: list[str]) -> list[MType]:
    """A record type listing the field gives its type (nullable when the record type is); else type any."""
    known = dict(column_type.fields or ()) if column_type.kind == "record" else {}
    return [(known[f].with_nullable() if column_type.nullable else known[f]) if f in known else ANY for f in fields]


def _expand_cell(cell: Any, fields: list[str]) -> list[Any]:
    if isinstance(cell, CellError):
        return [cell] * len(fields)
    if cell is None:
        return [None] * len(fields)
    if not isinstance(cell, Record):
        return [CellError(_cast(cell, "Record"))] * len(fields)
    out = []
    for f in fields:
        if f not in cell:
            out.append(None)
            continue
        try:
            out.append(cell[f])
        except MError as e:
            out.append(CellError(e))
    return out


def _expand_record_column(table: Any, column: Any, field_names: Any, new_names: Any = None) -> Table:
    """The record column's fields as columns in its place, named as the fields or as ``new_names``: a null cell or
    a missing field gives null, a cell that isn't a record fails each new cell, extra fields are dropped."""
    t = _need_table(table, "Table.ExpandRecordColumn")
    column, field_names, new_names = force(column), force(field_names), force(new_names)
    if not isinstance(column, str):
        raise _cast(column, "Text")
    if not isinstance(field_names, list):
        raise _cast(field_names, "List")
    if new_names is not None and not isinstance(new_names, list):
        raise _cast(new_names, "List")
    at = t.index(column)
    if new_names is not None and len(new_names) != len(field_names):
        raise _error("We expected newColumnNames to have the same number of items as fieldNames.", new_names)
    fields = _texts(field_names)
    names = fields if new_names is None else _texts(new_names)
    others = [c for i, c in enumerate(t.columns) if i != at]
    for group in (fields, names + others):
        dup = next((n for n in group if group.count(n) > 1), None)
        if dup is not None:
            raise _error(f"The field '{dup}' already exists in the record.", Record.of({"Name": dup, "Value": None}))
    rows = [[*r[:at], *_expand_cell(r[at], fields), *r[at + 1:]] for r in t.rows]
    types = [*t.types[:at], *_field_types(t.types[at], fields), *t.types[at + 1:]]
    return Table([*t.columns[:at], *names, *t.columns[at + 1:]], rows, types)


def _fn(name: str, call: Callable[..., Any], lo: int, hi: int) -> Function:
    return Function(name, call, lo, hi)


TABLE_OPS: dict[str, Any] = {
    "Table.Sort": _fn("Table.Sort", _sort, 2, 2),
    "Table.Distinct": _fn("Table.Distinct", _distinct, 1, 2),
    "Table.Buffer": _fn("Table.Buffer", _buffer, 1, 2),
    "Table.AddIndexColumn": _fn("Table.AddIndexColumn", _add_index_column, 2, 5),
    "Table.RowCount": _fn("Table.RowCount", _row_count, 1, 1),
    "Table.ExpandRecordColumn": _fn("Table.ExpandRecordColumn", _expand_record_column, 3, 4),
    "Order.Ascending": ASCENDING,
    "Order.Descending": DESCENDING,
    "BufferMode.Eager": BUFFER_EAGER,
    "BufferMode.Delayed": BUFFER_DELAYED,
}
