"""Evaluate an M expression or section document (the M language specification's
"Basic Concepts", "Values", "Operators", "Let", "Functions" and "Error Handling").

``let`` variables and record fields are computed on first use and kept, so an
unused step never runs and a step refers to itself only as an error. Names are
looked up in the innermost scope first, then the library, then ``resolve`` (the
other queries of a model or section). Function arguments are evaluated before
the call. What the library holds is ``library.LIBRARY``; a name outside it is an
``Expression.Error``, never a guess.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable, Optional

from . import ast
from .library import LIBRARY
from .parser import parse_document
from .values import ANY, CellError, Function, MError, MType, Record, Table, Thunk, force, kind_of

Resolver = Callable[[str], Any]


def evaluate(source: str | ast.Node, *, resolve: Optional[Resolver] = None, culture: str = "en-US") -> Any:
    """The value of an M expression. A section document evaluates to a record of its members."""
    tree = parse_document(source) if isinstance(source, str) else source
    ev = _Evaluator(resolve, culture)
    if isinstance(tree, ast.Section):
        scope = _Scope({}, None)
        for m in tree.members:
            scope.names[m.name] = ev.thunk(m.value, scope)
        return Record(dict(scope.names))
    try:
        return ev.eval(tree, _Scope({}, None))
    except RecursionError:
        raise MError("Expression.Error", "Evaluation nested too deeply.") from None


def force_table(table: Table, *, errors_as_null: bool) -> list[list[Any]]:
    """Every cell's value. A failed cell is null when ``errors_as_null`` (the model's
    returnErrorValuesAsNull), otherwise its error is raised."""
    out = []
    for row in table.rows:
        cells = []
        for cell in row:
            if isinstance(cell, CellError):
                if not errors_as_null:
                    raise cell.error
                cell = None
            cells.append(cell)
        out.append(cells)
    return out


class _Scope:
    __slots__ = ("names", "parent")

    def __init__(self, names: dict[str, Thunk], parent: Optional["_Scope"]):
        self.names, self.parent = names, parent

    def find(self, name: str) -> Optional[Thunk]:
        s: Optional[_Scope] = self
        while s is not None:
            if name in s.names:
                return s.names[name]
            s = s.parent
        return None


class _Evaluator:
    def __init__(self, resolve: Optional[Resolver], culture: str):
        self.resolve = resolve
        self.culture = culture

    def thunk(self, node: ast.Node, scope: _Scope) -> Thunk:
        return Thunk(lambda: self.eval(node, scope))

    def lookup(self, name: str, scope: _Scope) -> Any:
        t = scope.find(name)
        if t is not None:
            return t.get()
        if name in LIBRARY:
            value = LIBRARY[name]
            return value(self) if callable(value) and not isinstance(value, (Function, MType)) else value
        if self.resolve is not None:
            return self.resolve(name)
        raise MError("Expression.Error", f"The name '{name}' wasn't recognized.")

    def eval(self, node: ast.Node, scope: _Scope) -> Any:
        method = getattr(self, "_" + type(node).__name__, None)
        if method is None:
            raise MError("Expression.Error", f"{type(node).__name__} isn't evaluated yet.")
        return method(node, scope)

    # --- expressions --------------------------------------------------------

    def _Literal(self, n: ast.Literal, scope: _Scope) -> Any:
        if n.kind == "verbatim":
            raise MError("Expression.Error", "A verbatim literal can't be evaluated.")
        return n.value

    def _Identifier(self, n: ast.Identifier, scope: _Scope) -> Any:
        return self.lookup(n.name, scope)

    def _SectionAccess(self, n: ast.SectionAccess, scope: _Scope) -> Any:
        if self.resolve is None:
            raise MError("Expression.Error", f"The name '{n.section}!{n.member}' wasn't recognized.")
        return self.resolve(n.member)

    def _ListExpr(self, n: ast.ListExpr, scope: _Scope) -> list:
        out: list = []
        for item in n.items:
            if isinstance(item, ast.Range):
                first, last = self.eval(item.first, scope), self.eval(item.last, scope)
                if not (_is_whole(first) and _is_whole(last)):
                    raise MError("Expression.Error", "A list range needs whole numbers.")
                out.extend(range(int(first), int(last) + 1))
            else:
                try:
                    out.append(self.eval(item, scope))
                except MError as e:
                    out.append(CellError(e))
        return out

    def _RecordExpr(self, n: ast.RecordExpr, scope: _Scope) -> Record:
        inner = _Scope({}, scope)
        for f in n.fields:
            if f.name in inner.names:
                raise MError("Expression.Error", f"The field '{f.name}' already exists in the record.")
            inner.names[f.name] = self.thunk(f.value, inner)
        return Record(dict(inner.names))

    def _FieldAccess(self, n: ast.FieldAccess, scope: _Scope) -> Any:
        target = self.lookup("_", scope) if n.target is None else self.eval(n.target, scope)
        if isinstance(target, Record):
            if n.optional and n.name not in target:
                return None
            return target[n.name]
        if isinstance(target, Table):
            if n.optional and n.name not in target.columns:
                return None
            i = target.index(n.name)
            return [row[i] for row in target.rows]
        raise MError("Expression.Error", f"Can't access field '{n.name}' of a {kind_of(target)} value.")

    def _FieldProjection(self, n: ast.FieldProjection, scope: _Scope) -> Any:
        target = self.lookup("_", scope) if n.target is None else self.eval(n.target, scope)
        if isinstance(target, Record):
            fields = {}
            for x in n.names:
                if x in target:
                    fields[x] = target.fields[x]
                elif n.optional:
                    fields[x] = Thunk.of(None)
                else:
                    raise MError("Expression.Error", f"The field '{x}' of the record wasn't found.")
            return Record(fields)
        if isinstance(target, Table):
            cols: list[Optional[int]] = []
            for x in n.names:
                if x in target.columns or not n.optional:
                    cols.append(target.index(x))
                else:
                    cols.append(None)
            rows = [[None if i is None else row[i] for i in cols] for row in target.rows]
            types = [ANY if i is None else target.types[i] for i in cols]
            return Table(list(n.names), rows, types)
        raise MError("Expression.Error", f"Can't project the fields of a {kind_of(target)} value.")

    def _ItemAccess(self, n: ast.ItemAccess, scope: _Scope) -> Any:
        target, index = self.eval(n.target, scope), self.eval(n.index, scope)
        if isinstance(target, list) and _is_whole(index):
            if 0 <= index < len(target):
                return force(target[int(index)])
        elif isinstance(target, Table) and _is_whole(index):
            if 0 <= index < len(target.rows):
                return _row_record(target, target.rows[int(index)])
        elif isinstance(target, Table) and isinstance(index, Record):
            keys = [(target.index(k), index[k]) for k in index.fields]
            matches = [row for row in target.rows if all(_equal(force(row[i]), v) for i, v in keys)]
            if len(matches) > 1:
                raise MError("Expression.Error", "The key matched more than one row in the table.")
            if matches:
                return _row_record(target, matches[0])
            if n.optional:
                return None
            raise MError("Expression.Error", "The key didn't match any rows in the table.")
        else:
            raise MError("Expression.Error", f"Can't look up {kind_of(index)} in a {kind_of(target)} value.")
        if n.optional:
            return None
        raise MError("Expression.Error", "There weren't enough elements in the enumeration to complete the operation.")

    def _Invoke(self, n: ast.Invoke, scope: _Scope) -> Any:
        fn = self.eval(n.function, scope)
        if not isinstance(fn, Function):
            raise MError("Expression.Error", f"A {kind_of(fn)} value can't be invoked.")
        return fn.invoke([self.eval(a, scope) for a in n.arguments])

    def _Unary(self, n: ast.Unary, scope: _Scope) -> Any:
        v = self.eval(n.operand, scope)
        if n.op == "not":
            if v is None or isinstance(v, bool):
                return None if v is None else not v
        elif v is None:
            return None
        elif _is_number(v):
            return -v if n.op == "-" else v
        raise MError("Expression.Error", f"The operator {n.op} can't be applied to a {kind_of(v)} value.")

    def _Binary(self, n: ast.Binary, scope: _Scope) -> Any:
        op = n.op
        if op in ("and", "or"):
            left = self.eval(n.left, scope)
            if left is not None and not isinstance(left, bool):
                raise MError("Expression.Error", f"The operator {op} needs logical values.")
            if op == "and" and left is False or op == "or" and left is True:
                return left
            right = self.eval(n.right, scope)  # type: ignore[arg-type]
            if right is not None and not isinstance(right, bool):
                raise MError("Expression.Error", f"The operator {op} needs logical values.")
            if left is None:
                return None if right is None or right is (op == "and") else right
            return right
        if op == "??":
            left = self.eval(n.left, scope)
            return self.eval(n.right, scope) if left is None else left  # type: ignore[arg-type]
        if op == "meta":
            self.eval(n.right, scope)  # type: ignore[arg-type]
            return self.eval(n.left, scope)   # metadata doesn't change the value
        left = self.eval(n.left, scope)
        if op in ("as", "is"):
            t = self.type_value(n.right, scope)  # type: ignore[arg-type]
            ok = _conforms(left, t)
            if op == "is":
                return ok
            if not ok:
                raise MError("Expression.Error", f"A {kind_of(left)} value isn't of type {t.kind}.")
            return left
        right = self.eval(n.right, scope)  # type: ignore[arg-type]
        return _binary(op, left, right)

    def _Let(self, n: ast.Let, scope: _Scope) -> Any:
        inner = _Scope({}, scope)
        for b in n.bindings:
            inner.names[b.name] = self.thunk(b.value, inner)
        return self.eval(n.body, inner)

    def _If(self, n: ast.If, scope: _Scope) -> Any:
        cond = self.eval(n.condition, scope)
        if not isinstance(cond, bool):
            raise MError("Expression.Error", f"An if condition must be logical, not {kind_of(cond)}.")
        return self.eval(n.then if cond else n.otherwise, scope)

    def _Function(self, n: ast.Function, scope: _Scope) -> Function:
        params = n.parameters
        required = sum(1 for p in params if not p.optional)
        types = [None if p.type is None else self.type_value(p.type, scope) for p in params]
        returns = None if n.return_type is None else self.type_value(n.return_type, scope)

        def call(*args: Any) -> Any:
            names = {}
            for i, p in enumerate(params):
                value = args[i] if i < len(args) else None
                t = types[i]
                if t is not None and not (value is None and p.optional) and not _conforms(value, t):
                    raise MError("Expression.Error", f"The parameter {p.name} takes a {t.kind} value, "
                                                     f"not {kind_of(value)}.")
                names[p.name] = Thunk.of(value)
            result = self.eval(n.body, _Scope(names, scope))
            if returns is not None and not _conforms(result, returns):
                raise MError("Expression.Error", f"The function returns {returns.kind}, not {kind_of(result)}.")
            return result
        return Function("each" if n.each_ else "function", call, required, len(params))

    def _ErrorRaise(self, n: ast.ErrorRaise, scope: _Scope) -> Any:
        v = self.eval(n.value, scope)
        if isinstance(v, str):
            raise MError("Expression.Error", v)
        if isinstance(v, Record):
            get = lambda k: v[k] if k in v else None  # noqa: E731
            message = get("Message")
            if "Message.Format" in v:
                message = _format_message(get("Message.Format"), get("Message.Parameters"))
            raise MError(get("Reason"), message, get("Detail"))
        raise MError("Expression.Error", f"An error needs a text or record, not {kind_of(v)}.")

    def _Try(self, n: ast.Try, scope: _Scope) -> Any:
        try:
            value = self.eval(n.protected, scope)
        except MError as e:
            error = Record.of({"Reason": e.reason, "Message": e.message, "Detail": e.detail,
                               "Message.Format": None, "Message.Parameters": None, "ErrorCode": None})
            if n.otherwise is not None:
                return self.eval(n.otherwise, scope)
            if n.catch is not None:
                handler = self._Function(n.catch, scope)
                return handler.invoke([error] if n.catch.parameters else [])
            return Record.of({"HasError": True, "Error": error})
        if n.otherwise is not None or n.catch is not None:
            return value
        return Record.of({"HasError": False, "Value": value})

    def _TypeExpr(self, n: ast.TypeExpr, scope: _Scope) -> MType:
        return self.type_value(n.type, scope)

    def _NotImplementedExpr(self, n: ast.NotImplementedExpr, scope: _Scope) -> Any:
        raise MError("Expression.Error", "Not implemented.")

    # --- types --------------------------------------------------------------

    def type_value(self, t: ast.Node, scope: _Scope) -> MType:
        if isinstance(t, ast.PrimitiveType):
            return MType(t.name, t.name in ("any", "null"))
        if isinstance(t, ast.NullableType):
            return self.type_value(t.inner, scope).with_nullable()
        if isinstance(t, ast.RecordType):
            return MType("record", fields=self.field_types(t, scope))
        if isinstance(t, ast.ListType):
            return MType("list")
        if isinstance(t, ast.FunctionType):
            return MType("function")
        if isinstance(t, ast.TableType):
            row = self.type_value(t.row, scope) if isinstance(t.row, ast.RecordType) else self.eval(t.row, scope)
            if not isinstance(row, MType) or row.kind != "record":
                raise MError("Expression.Error", "A table type needs a record type for its row.")
            return MType("table", fields=row.fields)
        if isinstance(t, ast.ExprType):
            v = self.eval(t.expr, scope)
            if not isinstance(v, MType):
                raise MError("Expression.Error", f"A {kind_of(v)} value isn't a type.")
            return v
        raise MError("Expression.Error", f"{type(t).__name__} isn't a type.")

    def field_types(self, t: ast.RecordType, scope: _Scope) -> tuple[tuple[str, MType], ...]:
        return tuple((f.name, ANY if f.type is None else self.type_value(f.type, scope)) for f in t.fields)


# --- helpers -------------------------------------------------------------------

def _format_message(fmt: Any, params: Any) -> Any:
    """Message.Format's #{n} placeholders filled from Message.Parameters (the specification's error records)."""
    if fmt is None:
        return None
    if not isinstance(fmt, str) or not (params is None or isinstance(params, list)):
        raise MError("Expression.Error", "Message.Format needs text and Message.Parameters a list.")
    out = fmt
    for i, p in enumerate(params or []):
        p = force(p)
        if not isinstance(p, (str, int, float)) or isinstance(p, bool):
            raise MError("Expression.Error", f"A {kind_of(p)} message parameter isn't supported yet.")
        out = out.replace("#{" + str(i) + "}", p if isinstance(p, str) else str(p))
    return out


def _row_record(table: Table, row: list[Any]) -> Record:
    fields = {}
    for name, cell in zip(table.columns, row):
        if isinstance(cell, CellError):
            fields[name] = Thunk(_raiser(cell.error))
        else:
            fields[name] = Thunk.of(cell)
    return Record(fields)


def _raiser(error: MError) -> Callable[[], Any]:
    def raise_it() -> Any:
        raise error
    return raise_it


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_whole(v: Any) -> bool:
    return _is_number(v) and float(v).is_integer()


def _equal(a: Any, b: Any) -> bool:
    if _is_number(a) and _is_number(b):
        return bool(a == b)
    ka, kb = kind_of(a), kind_of(b)
    if ka != kb:
        return False
    if ka == "list":
        return len(a) == len(b) and all(_equal(force(x), force(y)) for x, y in zip(a, b))
    if ka == "record":
        return set(a.fields) == set(b.fields) and all(_equal(a[k], b[k]) for k in a.fields)
    if ka == "table":
        # Same column names in any order, and the same rows in order (the specification's table equality).
        if sorted(a.columns) != sorted(b.columns) or len(a.rows) != len(b.rows):
            return False
        order = [b.columns.index(c) for c in a.columns]
        return all(_equal(force(x), force(rb[j])) for ra, rb in zip(a.rows, b.rows) for x, j in zip(ra, order))
    return bool(a == b)


_ORDERED = ("number", "text", "date", "datetime", "datetimezone", "time", "duration", "logical", "binary")
_ARITHMETIC = ("null", "number", "date", "datetime", "datetimezone", "time", "duration")


def _binary(op: str, left: Any, right: Any) -> Any:
    if op == "=":
        return _equal(left, right)
    if op == "<>":
        return not _equal(left, right)
    kl, kr = kind_of(left), kind_of(right)
    if op in ("<", ">", "<=", ">="):
        if not (kl in _ORDERED + ("null",) and kr in _ORDERED + ("null",)) or "null" not in (kl, kr) and kl != kr:
            raise MError("Expression.Error", f"The operator {op} can't compare {kl} and {kr} values.")
        if left is None or right is None:
            return None
        return {"<": left < right, ">": left > right, "<=": left <= right, ">=": left >= right}[op]
    if left is None or right is None:
        other = kr if left is None else kl
        allowed = ("null", "text", "date", "time") if op == "&" else _ARITHMETIC
        if other not in allowed:
            raise MError("Expression.Error", f"The operator {op} can't be applied to {kl} and {kr} values.")
        return None
    if op == "&":
        if kl == kr == "text":
            return left + right
        if kl == kr == "list":
            return left + right
        if kl == kr == "record":
            return Record({**left.fields, **right.fields})
        if kl == "date" and kr == "time":
            return dt.datetime.combine(left, right)
        raise MError("Expression.Error", f"The operator & can't be applied to {kl} and {kr} values.")
    if kl == kr == "number":
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:   # IEEE 754, as the specification's division table
                if left == 0 or math.isnan(left):
                    return float("nan")
                return math.copysign(float("inf"), left) * math.copysign(1.0, right)
            return left / right
    if op in ("+", "-") and kr == "duration" and kl in ("date", "datetime", "datetimezone", "time", "duration"):
        return left + right if op == "+" else left - right
    if op == "-" and kl == kr and kl in ("date", "datetime", "datetimezone"):
        return left - right
    raise MError("Expression.Error", f"The operator {op} can't be applied to {kl} and {kr} values.")


def _conforms(value: Any, t: MType) -> bool:
    if value is None:
        return t.nullable or t.kind in ("any", "null")
    if t.kind == "any":
        return True
    if t.kind == "anynonnull":
        return True
    return kind_of(value) == t.kind
