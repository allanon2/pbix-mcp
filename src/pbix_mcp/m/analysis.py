"""What an M expression refers to, without evaluating it.

``free_names`` lists the names an expression uses but doesn't define itself:
library functions and constants (``Table.AddColumn``, ``Int64.Type``,
``#table``), other queries of the same section, and parameters. A ``let``'s
variables, a record's fields (visible to each other), a function's
parameters and an ``each``'s ``_`` are bound, following the specification's
scoping rules ("Basic Concepts", "Environments and variables").
"""
from __future__ import annotations

from . import ast


def free_names(node: ast.Node) -> list[str]:
    """The names ``node`` uses that it doesn't bind, in order of first use."""
    seen: dict[str, None] = {}
    _visit(node, frozenset(), seen)
    return list(seen)


def _visit(node: ast.Node, bound: frozenset[str], out: dict[str, None]) -> None:
    if isinstance(node, ast.Identifier):
        if node.name not in bound:
            out.setdefault(node.name, None)
        return
    if isinstance(node, ast.FieldAccess | ast.FieldProjection) and node.target is None:
        if "_" not in bound:
            out.setdefault("_", None)
        return
    if isinstance(node, ast.Let):
        inner = bound | {b.name for b in node.bindings}
        for b in node.bindings:
            _visit(b.value, inner, out)
        _visit(node.body, inner, out)
        return
    if isinstance(node, ast.RecordExpr):
        inner = bound | {f.name for f in node.fields}
        for f in node.fields:
            _visit(f.value, inner, out)
        return
    if isinstance(node, ast.Function):
        inner = bound | {p.name for p in node.parameters}
        for p in node.parameters:
            _visit(p, bound, out)
        if node.return_type is not None:
            _visit(node.return_type, bound, out)
        _visit(node.body, inner, out)
        return
    if isinstance(node, ast.Section):
        inner = bound | {m.name for m in node.members}
        for m in node.members:
            if m.attributes is not None:
                _visit(m.attributes, bound, out)
            _visit(m.value, inner, out)
        return
    for child in node.children():
        _visit(child, bound, out)
