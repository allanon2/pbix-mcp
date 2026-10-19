"""Tier 2 of the library: the table functions a query's steps use after reading
a source -- renaming, removing and selecting columns, adding a computed column,
promoting headers -- and Text.BeforeDelimiter. Behaviour follows Microsoft's
function reference; where it is silent the function refuses (an error naming
what isn't known yet) rather than guess.
"""
from __future__ import annotations

from typing import Any

from .values import ANY, CellError, Function, MError, MType, Record, Table, Thunk, force, kind_of

MISSING_ERROR, MISSING_IGNORE, MISSING_USE_NULL = 0, 1, 2      # MissingField.Type (Microsoft's reference)
FROM_START, FROM_END = 0, 1                                     # RelativePosition.Type


def _error(message: str, reason: str = "Expression.Error") -> MError:
    return MError(reason, message)


def _need_table(t: Any, what: str) -> Table:
    if not isinstance(t, Table):
        raise _error(f"{what} needs a table, not {kind_of(t)}.")
    return t


def _names(columns: Any, what: str) -> list[str]:
    columns = force(columns)
    if isinstance(columns, str):
        return [columns]
    if isinstance(columns, list):
        out = [force(c) for c in columns]
        if all(isinstance(c, str) for c in out):
            return out
    raise _error(f"{what} needs a column name or a list of them.")


def _missing(value: Any, what: str, allowed: tuple[int, ...]) -> int:
    if value is None:
        return MISSING_ERROR
    if type(value) is int and value in allowed:
        return value
    raise _error(f"{what}: MissingField value {value!r} isn't supported.")


def _not_found(name: str) -> MError:
    return _error(f"The column '{name}' of the table wasn't found.")


def row_record(table: Table, row: list[Any]) -> Record:
    """A row as the record a column generator sees: a failed cell is an error only when read."""
    fields = {}
    for name, cell in zip(table.columns, row):
        fields[name] = Thunk(_raiser(cell.error)) if isinstance(cell, CellError) else Thunk.of(cell)
    return Record(fields)


def _raiser(error: MError):
    def raise_it() -> Any:
        raise error
    return raise_it


def _rename_columns(table: Any, renames: Any, missing: Any = None) -> Table:
    t = _need_table(table, "Table.RenameColumns")
    mode = _missing(missing, "Table.RenameColumns", (MISSING_ERROR, MISSING_IGNORE))
    renames = force(renames)
    pairs = [renames] if isinstance(renames, list) and len(renames) == 2 and isinstance(force(renames[0]), str) \
        else renames
    if not isinstance(pairs, list):
        raise _error("Table.RenameColumns needs {old, new} pairs.")
    columns = list(t.columns)
    for pair in pairs:
        pair = [force(x) for x in pair] if isinstance(pair, list) else pair
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair)):
            raise _error("Table.RenameColumns needs {old, new} pairs.")
        old, new = pair
        if old not in columns:
            if mode == MISSING_IGNORE:
                continue
            raise _not_found(old)
        columns[columns.index(old)] = new
    if len(set(columns)) != len(columns):
        dup = next(c for c in columns if columns.count(c) > 1)
        raise _error(f"The field '{dup}' already exists in the record.")
    return Table(columns, [list(r) for r in t.rows], list(t.types))


def _remove_columns(table: Any, columns: Any, missing: Any = None) -> Table:
    t = _need_table(table, "Table.RemoveColumns")
    mode = _missing(missing, "Table.RemoveColumns", (MISSING_ERROR, MISSING_IGNORE, MISSING_USE_NULL))
    drop = set()
    for c in _names(columns, "Table.RemoveColumns"):
        if c not in t.columns:
            if mode == MISSING_ERROR:
                raise _not_found(c)
            continue
        drop.add(c)
    keep = [i for i, c in enumerate(t.columns) if c not in drop]
    return Table([t.columns[i] for i in keep], [[r[i] for i in keep] for r in t.rows], [t.types[i] for i in keep])


def _select_columns(table: Any, columns: Any, missing: Any = None) -> Table:
    t = _need_table(table, "Table.SelectColumns")
    mode = _missing(missing, "Table.SelectColumns", (MISSING_ERROR, MISSING_IGNORE, MISSING_USE_NULL))
    names: list[str] = []
    idx: list[int | None] = []
    for c in _names(columns, "Table.SelectColumns"):
        if c in t.columns:
            names.append(c)
            idx.append(t.columns.index(c))
        elif mode == MISSING_USE_NULL:
            names.append(c)
            idx.append(None)
        elif mode == MISSING_ERROR:
            raise _error(f"The field '{c}' of the record wasn't found.")   # the reference's own example error
    if len(set(names)) != len(names):
        raise _error("Table.SelectColumns: a column is selected twice.")
    rows = [[None if i is None else r[i] for i in idx] for r in t.rows]
    return Table(names, rows, [ANY if i is None else t.types[i] for i in idx])


def _add_column(table: Any, name: Any, generator: Any, column_type: Any = None) -> Table:
    t = _need_table(table, "Table.AddColumn")
    if not isinstance(name, str):
        raise _error("Table.AddColumn needs a column name.")
    if name in t.columns:
        raise _error(f"The field '{name}' already exists in the record.")
    if not isinstance(generator, Function):
        raise _error("Table.AddColumn needs a function to compute the column.")
    if column_type is not None and not isinstance(column_type, MType):
        raise _error("Table.AddColumn's column type must be a type.")
    rows = []
    for r in t.rows:
        cell: Any
        try:
            cell = generator.invoke([row_record(t, r)])
        except MError as e:
            cell = CellError(e)
        rows.append([*r, cell])
    return Table([*t.columns, name], rows, [*t.types, column_type or ANY])


def _promote_headers(table: Any, options: Any = None) -> Table:
    """The first row's text and number values become the column names (Microsoft's reference: "By default, only
    text or number values are promoted"); the rest keep their ColumnN name. Options aren't supported yet, nor are
    names the reference doesn't settle: an empty or repeated one is an error."""
    t = _need_table(table, "Table.PromoteHeaders")
    if options is not None:
        if not isinstance(options, Record) or any(k not in ("PromoteAllScalars", "Culture") for k in options.fields):
            raise _error("Table.PromoteHeaders: only the PromoteAllScalars and Culture options are known.")
        if options["PromoteAllScalars"] if "PromoteAllScalars" in options else False:
            raise _error("Table.PromoteHeaders: PromoteAllScalars isn't supported yet.")
    if not t.rows:
        return Table(list(t.columns), [], list(t.types))
    from .library import to_text
    names = []
    for i, cell in enumerate(t.rows[0]):
        v = force(cell)
        if isinstance(v, str):
            name = v
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            name = to_text(v, "en-US")
        else:
            name = t.columns[i]
        if name == "":
            raise _error("Table.PromoteHeaders: an empty header isn't supported yet.")
        names.append(name)
    if len(set(names)) != len(names):
        dup = next(n for n in names if names.count(n) > 1)
        raise _error(f"Table.PromoteHeaders: the header {dup!r} repeats; how it is renamed isn't known yet.")
    return Table(names, [list(r) for r in t.rows[1:]], [ANY] * len(names))


def _before_delimiter(text: Any, delimiter: Any, index: Any = None) -> Any:
    """The text before the delimiter's index-th occurrence (0: the first), counted from the start or, with
    {index, RelativePosition.FromEnd}, from the end (Microsoft's reference). Not found isn't settled by the
    reference: an error until Desktop shows it."""
    if text is None:
        return None
    if not isinstance(text, str) or not isinstance(delimiter, str) or delimiter == "":
        raise _error("Text.BeforeDelimiter needs text and a non-empty delimiter.")
    n, where = 0, FROM_START
    index = force(index)
    if isinstance(index, list) and len(index) == 2:
        n, where = force(index[0]), force(index[1])
    elif index is not None:
        n = index
    if not (type(n) is int and n >= 0 and type(where) is int and where in (FROM_START, FROM_END)):
        raise _error("Text.BeforeDelimiter: the index must be a count, or {count, RelativePosition}.")
    positions = []
    start = 0
    while True:
        i = text.find(delimiter, start)
        if i < 0:
            break
        positions.append(i)
        start = i + len(delimiter)
    if len(positions) <= n:
        raise _error("Text.BeforeDelimiter: the delimiter wasn't found; what Desktop returns then isn't known yet.")
    pos = positions[n] if where == FROM_START else positions[len(positions) - 1 - n]
    return text[:pos]


def _fn(name: str, call: Any, lo: int, hi: int) -> Function:
    return Function(name, call, lo, hi)


TABLES: dict[str, Any] = {
    "Table.RenameColumns": _fn("Table.RenameColumns", _rename_columns, 2, 3),
    "Table.RemoveColumns": _fn("Table.RemoveColumns", _remove_columns, 2, 3),
    "Table.SelectColumns": _fn("Table.SelectColumns", _select_columns, 2, 3),
    "Table.AddColumn": _fn("Table.AddColumn", _add_column, 3, 4),
    "Table.PromoteHeaders": _fn("Table.PromoteHeaders", _promote_headers, 1, 2),
    "Text.BeforeDelimiter": _fn("Text.BeforeDelimiter", _before_delimiter, 2, 3),
    "MissingField.Error": MISSING_ERROR,
    "MissingField.Ignore": MISSING_IGNORE,
    "MissingField.UseNull": MISSING_USE_NULL,
    "RelativePosition.FromStart": FROM_START,
    "RelativePosition.FromEnd": FROM_END,
}

