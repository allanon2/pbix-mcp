"""M values as the evaluator holds them.

null is ``None``, logical ``bool``, number ``int``/``float``, text ``str``,
binary ``bytes``, date/datetime/time/duration the ``datetime`` module's types.
Lists are Python lists; records, tables, types and functions are the classes
below. An error is the ``MError`` exception; a table cell whose value failed
holds a ``CellError`` until something reads it (the specification's "Error
handling": a failed cell doesn't stop its row from existing).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class MError(Exception):
    """An M error value: ``[Reason, Message, Detail]``."""

    def __init__(self, reason: Optional[str], message: Optional[str], detail: Any = None):
        super().__init__(f"[{reason}] {message}")
        self.reason, self.message, self.detail = reason, message, detail


@dataclass(frozen=True)
class CellError:
    """A list item or table cell whose value is an error: raised when it is read."""
    error: MError


def force(v: Any) -> Any:
    """``v``, or the error a failed item or cell holds."""
    if isinstance(v, CellError):
        raise v.error
    return v


class Thunk:
    """A value computed on first use and kept, error included (let variables, record fields, cells)."""

    __slots__ = ("_compute", "_value", "_error", "_state")

    def __init__(self, compute: Callable[[], Any]):
        self._compute = compute
        self._state = 0        # 0 not run, 1 running, 2 done
        self._value: Any = None
        self._error: Optional[MError] = None

    @classmethod
    def of(cls, value: Any) -> "Thunk":
        t = cls(lambda: value)
        t._state, t._value = 2, value
        return t

    def get(self) -> Any:
        if self._state == 1:
            raise MError("Expression.Error", "A value refers to itself (a cyclic reference).")
        if self._state == 0:
            self._state = 1
            try:
                self._value = self._compute()
            except MError as e:
                self._error = e
            except RecursionError:
                self._error = MError("Expression.Error", "Evaluation nested too deeply.")
            except BaseException:
                self._state = 0     # not an M error (a bug or an interrupt): let it be retried, not called a cycle
                raise
            self._state = 2
            self._compute = None  # type: ignore[assignment]
        if self._error is not None:
            raise self._error
        return self._value


class Record:
    """Ordered fields, each a thunk."""

    def __init__(self, fields: dict[str, Thunk]):
        self.fields = fields

    @classmethod
    def of(cls, values: dict[str, Any]) -> "Record":
        return cls({k: Thunk.of(v) for k, v in values.items()})

    def __getitem__(self, name: str) -> Any:
        if name not in self.fields:
            raise MError("Expression.Error", f"The field '{name}' of the record wasn't found.")
        return self.fields[name].get()

    def __contains__(self, name: str) -> bool:
        return name in self.fields

    def __repr__(self) -> str:
        return f"Record({list(self.fields)})"


@dataclass(frozen=True)
class MType:
    """A type value. ``kind`` is a primitive type name ("number", "text", "table", ...); ``facet`` names a
    number type such as "Int64.Type"; ``fields`` are a record or table type's ``(name, type)`` pairs."""
    kind: str
    nullable: bool = False
    facet: Optional[str] = None
    fields: Optional[tuple[tuple[str, "MType"], ...]] = None

    def with_nullable(self) -> "MType":
        return MType(self.kind, True, self.facet, self.fields)


ANY = MType("any", True)


@dataclass
class Table:
    """Columns, their types, and rows of cells (a cell is a value or a ``CellError``)."""
    columns: list[str]
    rows: list[list[Any]]
    types: list[MType] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.types:
            self.types = [ANY] * len(self.columns)

    def index(self, name: str) -> int:
        try:
            return self.columns.index(name)
        except ValueError:
            raise MError("Expression.Error", f"The column '{name}' of the table wasn't found.") from None


@dataclass
class Function:
    """A function value: ``call`` takes evaluated arguments."""
    name: str
    call: Callable[..., Any]
    min_args: int = 0
    max_args: int = 0

    def invoke(self, args: list[Any]) -> Any:
        if not self.min_args <= len(args) <= self.max_args:
            raise MError("Expression.Error", f"{self.name} takes {self.min_args} to {self.max_args} arguments, "
                                             f"not {len(args)}.")
        return self.call(*args)


def kind_of(value: Any) -> str:
    """The primitive type name of a value. A failed item or cell raises its error (reading it is what fails)."""
    if isinstance(value, CellError):
        raise value.error
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "logical"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, bytes):
        return "binary"
    if isinstance(value, dt.datetime):
        return "datetimezone" if value.tzinfo else "datetime"
    if isinstance(value, dt.date):
        return "date"
    if isinstance(value, dt.time):
        return "time"
    if isinstance(value, dt.timedelta):
        return "duration"
    if isinstance(value, list):
        return "list"
    if isinstance(value, Record):
        return "record"
    if isinstance(value, Table):
        return "table"
    if isinstance(value, MType):
        return "type"
    if isinstance(value, Function):
        return "function"
    raise TypeError(f"not an M value: {value!r}")


class LazyTable(Table):
    """A table computed when first read (a workbook sheet a navigation table lists). An error computing it is
    raised where it is read, as a field's would be."""

    def __init__(self, load: Callable[[], Table]):   # noqa: D107 - no dataclass fields of its own
        self._load: Optional[Callable[[], Table]] = load
        self._table: Optional[Table] = None

    def _get(self) -> Table:
        if self._table is None:
            assert self._load is not None
            self._table = self._load()
            self._load = None
        return self._table

    @property
    def columns(self) -> list[str]:  # type: ignore[override]
        return self._get().columns

    @property
    def rows(self) -> list[list[Any]]:  # type: ignore[override]
        return self._get().rows

    @property
    def types(self) -> list[MType]:  # type: ignore[override]
        return self._get().types

    def __repr__(self) -> str:
        return "LazyTable(loaded)" if self._table is not None else "LazyTable(pending)"
