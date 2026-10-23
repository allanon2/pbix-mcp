"""A recursive-descent parser for M documents, from the language specification's
syntactic grammar (learn.microsoft.com/powerquery-m, "Consolidated Grammar").

``parse_document`` reads what a query or a ``Section1.m`` holds: an expression,
or a section with its members. ``parse_expression`` reads an expression only.
Both raise ``MSyntaxError`` on text that isn't M.

Operator precedence, loosest first: ``??``, ``or``, ``and``, ``is``, ``as``,
``=``/``<>``, the comparisons, ``+``/``-``/``&``, ``*``/``/``, ``meta``, then
the unary operators and ``type``. Nesting is bounded by Python's recursion
limit (about 160 levels of parentheses at the default limit); deeper text is
an ``MSyntaxError``.
"""
from __future__ import annotations

from typing import Callable, NoReturn, Optional, TypeVar, Union

from . import ast
from .lexer import MSyntaxError, Token, tokenize

PRIMITIVE_TYPES = frozenset({
    "any", "anynonnull", "binary", "date", "datetime", "datetimezone", "duration", "function", "list", "logical",
    "none", "null", "number", "record", "table", "text", "time", "type",
})


# Binary operator levels, loosest first. "is"/"as" take a type on the right.
_LEVELS: list[tuple[str, ...]] = [("or",), ("and",), ("is",), ("as",), ("=", "<>"), ("<", ">", "<=", ">="),
                                  ("+", "-", "&"), ("*", "/")]
N = TypeVar("N", bound=ast.Node)

_OP_LEVEL = {op: level for level, ops in enumerate(_LEVELS) for op in ops}


def parse_document(text: str) -> ast.Expr | ast.Section:
    """A query's M: an expression, or a section document."""
    p = _Parser(text)
    first = p.peek()
    if p.is_keyword("section") or first.value == "[" and p.attributes_then_section():
        return p.whole(p.section)
    return p.whole(p.expression)


def parse_expression(text: str) -> ast.Expr:
    p = _Parser(text)
    return p.whole(p.expression)


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens = tokenize(text)
        self.pos = 0

    def whole(self, rule: Callable[[], N]) -> N:
        """``rule`` over all of the text. Nesting deeper than Python's stack allows is a syntax error."""
        try:
            node = rule()
        except RecursionError:
            raise MSyntaxError("Expression nested too deeply", self.text, self.peek().start) from None
        self.expect_end()
        return node

    # --- token helpers ------------------------------------------------------

    def peek(self, ahead: int = 0) -> Token:
        return self.tokens[min(self.pos + ahead, len(self.tokens) - 1)]

    def next(self) -> Token:
        tok = self.peek()
        self.pos += 1
        return tok

    def is_punct(self, p: str, ahead: int = 0) -> bool:
        tok = self.peek(ahead)
        return tok.kind == "punct" and tok.value == p

    def is_keyword(self, k: str, ahead: int = 0) -> bool:
        tok = self.peek(ahead)
        return tok.kind == "keyword" and tok.value == k

    def is_word(self, w: str, ahead: int = 0) -> bool:
        """An identifier the grammar reads as a word in context: "optional", "nullable", "catch", "table", ..."""
        tok = self.peek(ahead)
        return tok.kind == "identifier" and tok.value == w

    def accept(self, p: str) -> bool:
        if self.is_punct(p):
            self.pos += 1
            return True
        return False

    def expect(self, p: str) -> Token:
        if not self.is_punct(p):
            self.fail(f"Expected {p!r}")
        return self.next()

    def expect_keyword(self, k: str) -> Token:
        if not self.is_keyword(k):
            self.fail(f"Expected {k!r}")
        return self.next()

    def expect_end(self) -> None:
        if self.peek().kind != "end":
            self.fail("Expected the end of the expression")

    def fail(self, message: str) -> NoReturn:
        tok = self.peek()
        found = "the end of the text" if tok.kind == "end" else repr(tok.text)
        raise MSyntaxError(f"{message}, found {found}", self.text, tok.start)

    def name(self) -> str:
        """An identifier or a quoted identifier."""
        tok = self.peek()
        if tok.kind not in ("identifier", "quoted"):
            self.fail("Expected a name")
        self.pos += 1
        return tok.value  # type: ignore[return-value]

    def generalized_name(self) -> str:
        """A record field name: a quoted identifier, or words as written up to "=", "," or "]"."""
        tok = self.peek()
        if tok.kind == "quoted":
            self.pos += 1
            return tok.value  # type: ignore[return-value]
        first = tok
        last = None
        while self.peek().kind in ("identifier", "keyword") or last is not None and self.peek().kind == "number":
            last = self.next()
        if last is None:
            self.fail("Expected a field name")
        return self.text[first.start:last.end]

    def node(self, cls: type[N], start: int, *args, **kwargs) -> N:
        n = cls(*args, **kwargs)
        n.start, n.end = start, self.tokens[self.pos - 1].end
        return n

    # --- documents ----------------------------------------------------------

    def attributes_then_section(self) -> bool:
        save = self.pos
        try:
            self.record_literal()
            return self.is_keyword("section")
        except MSyntaxError:
            return False
        finally:
            self.pos = save

    def section(self) -> ast.Section:
        start = self.peek().start
        attributes = self.record_literal() if self.is_punct("[") else None
        self.expect_keyword("section")
        name = None if self.is_punct(";") else self.name()
        self.expect(";")
        members = []
        while self.peek().kind != "end":
            mstart = self.peek().start
            mattrs = self.record_literal() if self.is_punct("[") else None
            shared = False
            if self.is_keyword("shared"):
                self.next()
                shared = True
            mname = self.name()
            self.expect("=")
            value = self.expression()
            self.expect(";")
            members.append(self.node(ast.SectionMember, mstart, mname, value, shared, mattrs))
        return self.node(ast.Section, start, name, members, attributes)

    # --- expressions --------------------------------------------------------

    def expression(self) -> ast.Expr:
        tok = self.peek()
        start = tok.start
        if tok.kind == "keyword":
            if tok.value == "each":
                self.next()
                body = self.expression()
                param = ast.Parameter("_", start=start, end=start)
                return self.node(ast.Function, start, [param], None, body, each_=True)
            if tok.value == "let":
                return self.let()
            if tok.value == "if":
                self.next()
                cond = self.expression()
                self.expect_keyword("then")
                then = self.expression()
                self.expect_keyword("else")
                other = self.expression()
                return self.node(ast.If, start, cond, then, other)
            if tok.value == "error":
                self.next()
                return self.node(ast.ErrorRaise, start, self.expression())
            if tok.value == "try":
                return self.try_()
        if self.is_punct("("):
            fn = self.maybe_function()
            if fn is not None:
                return fn
        return self.coalesce()

    def let(self) -> ast.Let:
        start = self.expect_keyword("let").start
        bindings = []
        while True:
            bstart = self.peek().start
            bname = self.name()
            self.expect("=")
            bindings.append(self.node(ast.Binding, bstart, bname, self.expression()))
            if not self.accept(","):
                break
        self.expect_keyword("in")
        return self.node(ast.Let, start, bindings, self.expression())

    def try_(self) -> ast.Try:
        start = self.expect_keyword("try").start
        protected = self.expression()
        if self.is_keyword("otherwise"):
            self.next()
            return self.node(ast.Try, start, protected, otherwise=self.expression())
        if self.is_word("catch"):
            self.next()
            fstart = self.peek().start
            self.expect("(")
            params = [] if self.is_punct(")") else [self.node(ast.Parameter, self.peek().start, self.name())]
            self.expect(")")
            self.expect("=>")
            handler = self.node(ast.Function, fstart, params, None, self.expression())
            return self.node(ast.Try, start, protected, catch=handler)
        return self.node(ast.Try, start, protected)

    def maybe_function(self) -> Optional[ast.Function]:
        """``(params) as type => body``, or None (position unchanged) when "(" opens something else."""
        save = self.pos
        start = self.peek().start
        try:
            self.expect("(")
            params = []
            if not self.is_punct(")"):
                while True:
                    params.append(self.parameter())
                    if not self.accept(","):
                        break
            self.expect(")")
            ret = None
            if self.is_keyword("as"):
                self.next()
                ret = self.nullable_primitive_type()
            if not self.is_punct("=>"):
                raise MSyntaxError("not a function", self.text, self.peek().start)
        except MSyntaxError:
            self.pos = save
            return None
        self.next()
        return self.node(ast.Function, start, params, ret, self.expression())

    def parameter(self) -> ast.Parameter:
        start = self.peek().start
        optional = False
        if self.is_word("optional") and self.peek(1).kind in ("identifier", "quoted"):
            self.next()
            optional = True
        pname = self.name()
        ptype = None
        if self.is_keyword("as"):
            self.next()
            ptype = self.nullable_primitive_type()
        return self.node(ast.Parameter, start, pname, ptype, optional)

    def coalesce(self) -> ast.Expr:
        start = self.peek().start
        left = self.binary(0)
        if self.accept("??"):
            return self.node(ast.Binary, start, "??", left, self.coalesce())
        return left

    def binary(self, level: int) -> ast.Expr:
        """Precedence climbing over ``_LEVELS``: operators at ``level`` or tighter."""
        start = self.peek().start
        left = self.metadata()
        while True:
            tok = self.peek()
            op_level = _OP_LEVEL.get(tok.text) if tok.kind in ("punct", "keyword") else None
            if op_level is None or op_level < level:
                return left
            self.next()
            right: Union[ast.Expr, ast.Type]
            if tok.value in ("is", "as"):
                right = self.nullable_primitive_type()
            else:
                right = self.binary(op_level + 1)
            left = self.node(ast.Binary, start, tok.value, left, right)

    def metadata(self) -> ast.Expr:
        start = self.peek().start
        left = self.unary()
        if self.is_keyword("meta"):
            self.next()
            return self.node(ast.Binary, start, "meta", left, self.unary())
        return left

    def unary(self) -> ast.Expr:
        tok = self.peek()
        if tok.kind == "punct" and tok.value in ("+", "-") or self.is_keyword("not"):
            self.next()
            return self.node(ast.Unary, tok.start, tok.value, self.unary())
        if self.is_keyword("type"):
            self.next()
            return self.node(ast.TypeExpr, tok.start, self.type_(primary=True))
        return self.postfix(self.primary())

    def primary(self) -> ast.Expr:
        tok = self.peek()
        start = tok.start
        if tok.kind == "number":
            self.next()
            return self.node(ast.Literal, start, "number", tok.value)
        if tok.kind == "text":
            self.next()
            return self.node(ast.Literal, start, "text", tok.value)
        if tok.kind == "verbatim":
            self.next()
            return self.node(ast.Literal, start, "verbatim", tok.value)
        if tok.kind == "keyword":
            if tok.value in ("true", "false"):
                self.next()
                return self.node(ast.Literal, start, "logical", tok.value == "true")
            if tok.value == "null":
                self.next()
                return self.node(ast.Literal, start, "null", None)
            if tok.text.startswith("#"):
                self.next()
                return self.node(ast.Identifier, start, tok.value)
            self.fail("Expected an expression")
        if tok.kind in ("identifier", "quoted"):
            self.next()
            if tok.kind == "identifier" and self.is_punct("!") and self.peek(1).kind in ("identifier", "quoted"):
                self.next()
                member = self.name()
                return self.node(ast.SectionAccess, start, tok.value, member)
            return self.node(ast.Identifier, start, tok.value)
        if self.accept("@"):
            return self.node(ast.Identifier, start, self.name(), True)
        if self.accept("("):
            inner = self.expression()
            self.expect(")")
            return inner
        if self.is_punct("{"):
            return self.list_literal()
        if self.is_punct("["):
            return self.bracket()
        if self.accept("..."):
            return self.node(ast.NotImplementedExpr, start)
        self.fail("Expected an expression")

    def postfix(self, target: ast.Expr) -> ast.Expr:
        start = target.start
        while True:
            if self.is_punct("["):
                if self.is_punct("[", 1):
                    names = self.projection()
                    target = self.node(ast.FieldProjection, start, target, names, self.accept("?"))
                else:
                    self.next()
                    fname = self.generalized_name()
                    self.expect("]")
                    target = self.node(ast.FieldAccess, start, target, fname, self.accept("?"))
            elif self.is_punct("{"):
                self.next()
                index = self.expression()
                self.expect("}")
                target = self.node(ast.ItemAccess, start, target, index, self.accept("?"))
            elif self.is_punct("("):
                self.next()
                args = []
                if not self.is_punct(")"):
                    while True:
                        args.append(self.expression())
                        if not self.accept(","):
                            break
                self.expect(")")
                target = self.node(ast.Invoke, start, target, args)
            else:
                return target

    def projection(self) -> list[str]:
        self.expect("[")
        names = []
        while True:
            self.expect("[")
            names.append(self.generalized_name())
            self.expect("]")
            if not self.accept(","):
                break
        self.expect("]")
        return names

    def bracket(self) -> ast.Expr:
        """``[`` at the start of a primary: a record, ``_``'s field, or a projection of ``_``."""
        start = self.peek().start
        if self.is_punct("[", 1):
            names = self.projection()
            return self.node(ast.FieldProjection, start, None, names, self.accept("?"))
        save = self.pos
        self.next()
        if not self.is_punct("]"):
            self.generalized_name()
            if self.is_punct("]"):
                self.pos = save + 1
                fname = self.generalized_name()
                self.expect("]")
                return self.node(ast.FieldAccess, start, None, fname, self.accept("?"))
        self.pos = save
        return self.record_literal()

    def record_literal(self) -> ast.RecordExpr:
        start = self.expect("[").start
        fields = []
        if not self.is_punct("]"):
            while True:
                fstart = self.peek().start
                fname = self.generalized_name()
                self.expect("=")
                fields.append(self.node(ast.RecordField, fstart, fname, self.expression()))
                if not self.accept(","):
                    break
        self.expect("]")
        return self.node(ast.RecordExpr, start, fields)

    def list_literal(self) -> ast.ListExpr:
        start = self.expect("{").start
        items: list[Union[ast.Expr, ast.Range]] = []
        if not self.is_punct("}"):
            while True:
                istart = self.peek().start
                item: Union[ast.Expr, ast.Range] = self.expression()
                if self.accept(".."):
                    item = self.node(ast.Range, istart, item, self.expression())
                items.append(item)
                if not self.accept(","):
                    break
        self.expect("}")
        return self.node(ast.ListExpr, start, items)

    # --- types ----------------------------------------------------------------

    def nullable_primitive_type(self) -> ast.Type:
        start = self.peek().start
        if self.is_word("nullable"):
            self.next()
            return self.node(ast.NullableType, start, self.primitive_type())
        return self.primitive_type()

    def primitive_type(self) -> ast.PrimitiveType:
        tok = self.peek()
        if tok.kind in ("identifier", "keyword") and tok.value in PRIMITIVE_TYPES:
            self.next()
            return self.node(ast.PrimitiveType, tok.start, tok.value)
        self.fail("Expected a primitive type")

    def type_(self, primary: bool = False) -> ast.Type:
        """A field's, list item's or nullable's type: a primary type or a primary expression
        (``Int64.Type``, ``_t``). After the ``type`` keyword (``primary``), a primary type only."""
        tok = self.peek()
        start = tok.start
        if self.is_word("nullable"):
            self.next()
            return self.node(ast.NullableType, start, self.type_())
        if self.is_punct("["):
            return self.record_type()
        if self.is_punct("{"):
            self.next()
            item = self.type_()
            self.expect("}")
            return self.node(ast.ListType, start, item)
        if self.is_word("function") and self.is_punct("(", 1):
            self.next()
            self.expect("(")
            params = []
            if not self.is_punct(")"):
                while True:
                    params.append(self.parameter())
                    if not self.accept(","):
                        break
            self.expect(")")
            self.expect_keyword("as")
            return self.node(ast.FunctionType, start, params, self.nullable_primitive_type())
        if self.is_word("table") and self.is_punct("[", 1):
            self.next()
            return self.node(ast.TableType, start, self.record_type())
        if tok.kind in ("identifier", "keyword") and tok.value in PRIMITIVE_TYPES:
            self.next()
            return self.node(ast.PrimitiveType, start, tok.value)
        if primary:
            self.fail("Expected a type")
        expr = self.postfix(self.primary())
        return self.node(ast.ExprType, start, expr)

    def record_type(self) -> ast.RecordType:
        start = self.expect("[").start
        fields = []
        is_open = False
        if not self.is_punct("]"):
            while True:
                if self.accept("..."):
                    is_open = True
                    break
                fstart = self.peek().start
                optional = False
                if self.is_word("optional") and not self.is_punct("=", 1) and not self.is_punct(",", 1) \
                        and not self.is_punct("]", 1):
                    self.next()
                    optional = True
                fname = self.generalized_name()
                ftype = None
                if self.accept("="):
                    ftype = self.type_()
                fields.append(self.node(ast.FieldSpec, fstart, fname, ftype, optional))
                if not self.accept(","):
                    break
        self.expect("]")
        return self.node(ast.RecordType, start, fields, is_open)
