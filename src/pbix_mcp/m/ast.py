"""The syntax tree an M document parses to.

Every node carries ``start``/``end``: its span in the parsed text. A
parenthesized expression is its inner node; ``each e`` is a ``Function`` with
the one parameter ``_`` and ``each_ = True``; ``[x]`` with no target reads
``_``'s field (``target=None``).
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Iterator, Optional, Union


@dataclass
class Node:
    start: int = field(default=0, kw_only=True, compare=False, repr=False)
    end: int = field(default=0, kw_only=True, compare=False, repr=False)

    def children(self) -> Iterator["Node"]:
        """The nodes directly under this one, in source order."""
        for f in fields(self):
            yield from _nodes(getattr(self, f.name))


def _nodes(value: object) -> Iterator[Node]:
    if isinstance(value, Node):
        yield value
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _nodes(v)


def walk(node: Node) -> Iterator[Node]:
    """``node`` and every node under it, depth first."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(list(n.children())))


# --- expressions -------------------------------------------------------------

@dataclass
class Literal(Node):
    kind: str                       # "number", "text", "logical", "null"
    value: object


@dataclass
class Identifier(Node):
    name: str                       # a keyword such as "#table" is an identifier too
    inclusive: bool = False         # "@name"


@dataclass
class SectionAccess(Node):
    section: str
    member: str


@dataclass
class Range(Node):
    first: "Expr"
    last: "Expr"


@dataclass
class ListExpr(Node):
    items: list[Union["Expr", Range]]


@dataclass
class RecordField(Node):
    name: str
    value: "Expr"


@dataclass
class RecordExpr(Node):
    fields: list[RecordField]


@dataclass
class FieldAccess(Node):
    target: Optional["Expr"]        # None: the implicit "_" of an each
    name: str
    optional: bool = False


@dataclass
class FieldProjection(Node):
    target: Optional["Expr"]
    names: list[str]
    optional: bool = False


@dataclass
class ItemAccess(Node):
    target: "Expr"
    index: "Expr"
    optional: bool = False


@dataclass
class Invoke(Node):
    function: "Expr"
    arguments: list["Expr"]


@dataclass
class Unary(Node):
    op: str                         # "+", "-", "not"
    operand: "Expr"


@dataclass
class Binary(Node):
    op: str                         # arithmetic, comparison, "&", "and", "or", "??", "meta", "as", "is"
    left: "Expr"
    right: Union["Expr", "Type"]    # a Type for "as" and "is"


@dataclass
class Binding(Node):
    name: str
    value: "Expr"


@dataclass
class Let(Node):
    bindings: list[Binding]
    body: "Expr"


@dataclass
class If(Node):
    condition: "Expr"
    then: "Expr"
    otherwise: "Expr"


@dataclass
class Parameter(Node):
    name: str
    type: Optional["Type"] = None
    optional: bool = False


@dataclass
class Function(Node):
    parameters: list[Parameter]
    return_type: Optional["Type"]
    body: "Expr"
    each_: bool = False


@dataclass
class ErrorRaise(Node):
    value: "Expr"


@dataclass
class Try(Node):
    protected: "Expr"
    otherwise: Optional["Expr"] = None
    catch: Optional[Function] = None


@dataclass
class TypeExpr(Node):
    type: "Type"


@dataclass
class NotImplementedExpr(Node):     # "..."
    pass


# --- types (after "type", "as" and "is") ------------------------------------

@dataclass
class PrimitiveType(Node):
    name: str                       # "number", "text", "table", "any", ...


@dataclass
class NullableType(Node):
    inner: "Type"


@dataclass
class FieldSpec(Node):
    name: str
    type: Optional["Type"] = None
    optional: bool = False


@dataclass
class RecordType(Node):
    fields: list[FieldSpec]
    open: bool = False


@dataclass
class ListType(Node):
    item: "Type"


@dataclass
class FunctionType(Node):
    parameters: list[Parameter]
    return_type: "Type"


@dataclass
class TableType(Node):
    row: Union[RecordType, "Expr"]


@dataclass
class ExprType(Node):
    """A type given by an expression: ``Int64.Type``, ``_t``, ``(type text) meta [...]``."""
    expr: "Expr"


Type = Union[PrimitiveType, NullableType, RecordType, ListType, FunctionType, TableType, ExprType]
Expr = Union[Literal, Identifier, SectionAccess, ListExpr, RecordExpr, FieldAccess, FieldProjection, ItemAccess,
             Invoke, Unary, Binary, Let, If, Function, ErrorRaise, Try, TypeExpr, NotImplementedExpr]


# --- documents ---------------------------------------------------------------

@dataclass
class SectionMember(Node):
    name: str
    value: Expr
    shared: bool = False
    attributes: Optional[RecordExpr] = None


@dataclass
class Section(Node):
    name: Optional[str]
    members: list[SectionMember]
    attributes: Optional[RecordExpr] = None
