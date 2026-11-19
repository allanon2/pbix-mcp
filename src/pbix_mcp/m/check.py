"""Check M text the way Power Query's Advanced Editor does.

``check_query(text)`` answers what the editor shows while a query is typed: the first
error, its message and range, and whether the query may be saved ("Done").

Power Query's own rules, as its editor reports them:

* An empty text has no error.
* Text that doesn't read as M, or reads as a section document instead of an expression,
  reports the first syntax error and **may still be saved**: Power Query keeps such a query
  as a verbatim literal ``#!"..."`` (``escape_source_text``), so it fails only when
  evaluated.
* Text that reads cleanly is then checked for duplicate names: a function's parameters, a
  let's variables, a record's fields, a record type's fields, a section's members. The
  first duplicate (outermost first, then in reading order) is reported at its second
  occurrence's first token and the query **may not** be saved. So is a verbatim literal
  ``#!"..."`` whose text reads as an expression without error.
* A name that doesn't exist is not an error here: it is found only when the query runs.

Messages are Power Query's English texts. Positions are offsets in UTF-16 code units (what
the editor counts), with 0-based rows and columns; a row ends after ``\\n``, or after a
``\\r`` not followed by ``\\n``.

The same reader builds the tree the evaluator runs (``parse_document``, ``parse_expression``):
``pbix_mcp.m.ast`` nodes, shaped as Power Query's reader shapes them (``and``, ``or`` and ``??``
chains as balanced trees, the other binary operators by precedence, left to right), with offsets
into the text as Python indexes.
"""
from __future__ import annotations

import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from . import ast

class MSyntaxError(ValueError):
    """Text that isn't M, with Power Query's message. ``offset`` is where in the text (a Python index) the first
    token it couldn't read starts; ``line`` and ``column`` count from 1."""

    def __init__(self, message: str, text: str, offset: int):
        line = text.count("\n", 0, offset) + 1
        column = offset - (text.rfind("\n", 0, offset) + 1) + 1
        super().__init__(f"{message} (line {line}, column {column})")
        self.message, self.offset, self.line, self.column = message, offset, line, column


# --- messages ------------------------------------------------------------------------

NO_SYNTAX_ERRORS = "No syntax errors have been detected."
_EXPECTED = "Token {0} expected."
_COMMA_BEFORE = "A ',' cannot precede a {0}."
_TOO_DEEP = "This query is too complicated to run."
_REQUIRED_AFTER_OPTIONAL = "You cannot have a required argument following an optional argument."
_INVALID_TYPE = "The type identifier is invalid."
_BAD_CATCH = ("The 'catch' clause of a try/catch expression must be followed by a function definition with 0 or 1 "
              "arguments and no type constraints.")
_INVALID_IDENTIFIER = "Invalid identifier."
_INVALID_LITERAL = "Invalid literal."
_DUPLICATE_CONSTANT_FIELD = "Duplicate field named '{0}'."
_INVALID_EXPRESSION = "Invalid expression."
_DUPLICATE = {
    "parameter": "The parameter name '{0}' is a duplicate.",
    "local": "The variable named '{0}' is already defined in this scope.",
    "field": "The field named '{0}' is already defined in this scope.",
    "member": "The member named '{0}' is already defined in this scope.",
}
_VERBATIM_READS = ("Consider removing the verbatim literal brackets ( #!\" ... \" ) to enable evaluation of the "
                   "bracketed expression.")
_INVALID_NAME_SEQUENCE = "There is an invalid sequence in identifier."

_MAX_DEPTH = 800

# --- tokens ------------------------------------------------------------------------------

BOF, EOF, ERROR, IDENTIFIER, LITERAL = "Bof", "Eof", "TokenError", "Identifier", "Literal"
WHITESPACE, VERBATIM = "Whitespace", "Verbatim"

_KEYWORDS = {
    "as": "as", "otherwise": "otherwise", "each": "each", "else": "else", "in": "in", "if": "if", "is": "is",
    "let": "let", "and": "and", "or": "or", "meta": "meta", "not": "not", "section": "section",
    "shared": "shared", "then": "then", "error": "error", "try": "try", "type": "type",
}
_LITERAL_WORDS = ("null", "true", "false")

_PUNCTUATION_NAMES = {
    "&": "'&'", "@": "'@'", "!": "'!'", "??": "'??'", ",": "','", "/": "'/'", "..": "'..'", "...": "'...'",
    "=": "'='", "=>": "'=>'", ">": "'>'", ">=": "'>='", "{": "'{'", "[": "'['", "(": "'('", "<": "'<'",
    "<=": "'<='", "-": "'-'", "*": "'*'", "<>": "'<>'", "+": "'+'", "?": "'?'", "}": "'}'", "]": "']'",
    ")": "')'", ";": "';'",
}

_PRIMITIVE_TYPES = frozenset({
    "null", "time", "date", "datetime", "datetimezone", "duration", "number", "logical", "text", "binary", "list",
    "record", "table", "function", "action", "type", "any", "anynonnull", "none",
})

_PREDEFINED = frozenset({"#datetimezone", "#datetime", "#duration", "#infinity", "#binary", "#table", "#date",
                         "#time", "#nan"})

_NEWLINES = "\n\r\u0085  "


def _message_text(kind: str) -> str:
    """How a token kind is named in "Token {0} expected."."""
    if kind in (BOF, EOF, ERROR, IDENTIFIER, LITERAL, WHITESPACE):
        return kind
    if kind in _PUNCTUATION_NAMES:
        return _PUNCTUATION_NAMES[kind]
    if kind == VERBATIM:
        return "'#!'"
    return f"'{kind}'"   # keywords, '#shared', '#sections'


def _is_whitespace(ch: str) -> bool:
    if " " < ch < "\x80":
        return False
    if ch in "\t\n\x0b\x0c\r \u0085  ":
        return True
    if ch == "᠎":
        return False
    return unicodedata.category(ch) == "Zs"


def _token_start_char(ch: str) -> bool:
    if "a" <= ch <= "z" or "A" <= ch <= "Z" or ch == "_":
        return True
    return ch >= "\x80" and not _is_whitespace(ch)


def _token_part_char(ch: str) -> bool:
    return "0" <= ch <= "9" or _token_start_char(ch)


@dataclass
class _Tok:
    kind: str
    start: int
    end: int


def _read_word(text: str, i: int, n: int) -> int:
    """From the character after a word's first: letters, digits, and a dot not followed by a dot."""
    i += 1
    while i < n and (_token_part_char(text[i]) or text[i] == "." and i + 1 < n and text[i + 1] != "."):
        i += 1
    return i


def _read_string(text: str, i: int, n: int) -> int:
    """From an opening quote at ``i``: past the closing quote, or to the end of the text."""
    i += 1
    while i < n:
        ch = text[i]
        i += 1
        if ch == '"':
            if i >= n or text[i] != '"':
                return i
            i += 1
    return i


def _read_number(text: str, i: int, n: int) -> int:
    while i < n and "0" <= text[i] <= "9":
        i += 1
    if i < n and text[i] == "." and i + 1 < n and "0" <= text[i + 1] <= "9":
        i += 2
        while i < n and "0" <= text[i] <= "9":
            i += 1
    if i < n and text[i] in "eE":
        j = i + 1
        if j < n and text[j] in "+-":
            j += 1
        if j < n and "0" <= text[j] <= "9":
            i = j
            while i < n and "0" <= text[i] <= "9":
                i += 1
    if i < n and text[i] in "dD":
        i += 1
    return i


def tokenize(text: str) -> list[_Tok]:
    """Every token, whitespace and comments included, between a Bof and an Eof token."""
    toks = [_Tok(BOF, 0, 0)]
    i, n = 0, len(text)
    while i < n:
        start = i
        ch = text[i]
        i += 1
        if ch in "\t\n\x0b\x0c\r \u0085  ":
            while i < n and _is_whitespace(text[i]):
                i += 1
            kind = WHITESPACE
        elif "0" <= ch <= "9":
            if ch == "0" and i < n and text[i] in "xX":
                i += 1
                while i < n and text[i] in "0123456789abcdefABCDEF":
                    i += 1
            else:
                i = _read_number(text, start, n)
            kind = LITERAL
        elif ch == '"':
            i = _read_string(text, start, n)
            kind = LITERAL
        elif ch == "/":
            kind = "/"
            if i < n and text[i] == "/":
                while i < n and text[i] not in _NEWLINES:
                    i += 1
                kind = WHITESPACE
            elif i < n and text[i] == "*":
                end = text.find("*/", i + 1)
                if end < 0:
                    i, kind = n, ERROR
                else:
                    i, kind = end + 2, WHITESPACE
        elif ch == ".":
            kind = ERROR
            if i < n and "0" <= text[i] <= "9":
                i = _read_number(text, start, n)
                kind = LITERAL
            elif i < n and text[i] == ".":
                i += 1
                kind = ".."
                if i < n and text[i] == ".":
                    i += 1
                    kind = "..."
        elif ch == "#":
            kind = ERROR
            if i < n:
                nxt = text[i]
                if nxt == '"':
                    i = _read_string(text, i, n)
                    kind = IDENTIFIER
                elif nxt == "!":
                    i += 1
                    kind = VERBATIM
                elif _token_start_char(nxt):
                    word_start = i
                    i = _read_word(text, i, n)
                    word = text[word_start:i]
                    kind = "#shared" if word == "shared" else "#sections" if word == "sections" else LITERAL
        elif ch in "=<>?":
            kind = ch
            if ch == "=" and i < n and text[i] == ">":
                i, kind = i + 1, "=>"
            elif ch == ">" and i < n and text[i] == "=":
                i, kind = i + 1, ">="
            elif ch == "<":
                if i < n and text[i] == "=":
                    i, kind = i + 1, "<="
                if i < n and text[i] == ">":
                    i, kind = i + 1, "<>"
            elif ch == "?" and i < n and text[i] == "?":
                i, kind = i + 1, "??"
        elif ch in "!&,{[(-*+}])@;":
            kind = ch
        elif _token_start_char(ch):
            i = _read_word(text, start, n)
            word = text[start:i]
            kind = LITERAL if word in _LITERAL_WORDS else _KEYWORDS.get(word, IDENTIFIER)
        elif ch >= "\x80" and unicodedata.category(ch) == "Zs":
            while i < n and _is_whitespace(text[i]):
                i += 1
            kind = WHITESPACE
        else:
            kind = ERROR
        toks.append(_Tok(kind, start, i))
    toks.append(_Tok(EOF, n, n))
    return toks


# --- literal and identifier values ------------------------------------------------------

def _hex_value(s: str) -> Optional[int]:
    if not 0 < len(s) <= 8 or any(c not in "0123456789abcdefABCDEF" for c in s):
        return None
    return int(s, 16)


def _escape_char(body: str) -> Optional[str]:
    if body == "#":
        return "#"
    if body == "lf":
        return "\n"
    if body == "cr":
        return "\r"
    if body == "tab":
        return "\t"
    if len(body) == 4:
        v = _hex_value(body)
        return None if v is None else chr(v)
    if len(body) == 8:
        v = _hex_value(body)
        if v is None or v > 0x10FFFF or v < 0xFFFF and 0xD800 <= v <= 0xDFFF:
            return None
        return _utf16(chr(v))   # beyond U+FFFF: its surrogate pair, as every other text here
    return None


def _string_value(token: str) -> Optional[str]:
    """A text literal's value, or None when it isn't one (unterminated, a bad escape)."""
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        return None
    body = token[1:-1]
    out, i, n = [], 0, len(body)
    while i < n:
        ch = body[i]
        if ch == '"':
            if i + 1 >= n or body[i + 1] != '"':
                return None
            out.append('"')
            i += 2
        elif ch == "#" and i + 1 < n and body[i + 1] == "(":
            close = body.find(")", i + 2)
            if close < 0:
                return None
            inner = body[i + 2:close]
            if not inner:
                return None
            for part in inner.split(","):
                c = _escape_char(part)
                if c is None:
                    return None
                out.append(c)
            i = close + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _number_valid(token: str) -> bool:
    if len(token) > 2 and token[1] in "xX":
        return all(c in "0123456789abcdefABCDEF" for c in token[2:])
    try:
        float(token)
    except ValueError:
        return False
    return not token.endswith(("d", "D"))


def _literal_valid(token: str) -> bool:
    if not token:
        return False
    c = token[0]
    if "0" <= c <= "9" or c == ".":
        return _number_valid(token)
    if c == '"':
        return _string_value(token) is not None
    if token in _LITERAL_WORDS:
        return True
    if c == "#":
        return token in _PREDEFINED
    return False


def _name_start(ch: str) -> bool:
    if "a" <= ch <= "z" or "A" <= ch <= "Z" or ch == "_":
        return True
    return ch >= "\x80" and unicodedata.category(ch) in ("Lu", "Ll", "Lt", "Lm", "Lo", "Nl")


def _name_part(ch: str) -> bool:
    if "a" <= ch <= "z" or "A" <= ch <= "Z" or "0" <= ch <= "9" or ch == "_":
        return True
    return ch >= "\x80" and unicodedata.category(ch) in ("Lu", "Ll", "Lt", "Lm", "Lo", "Mn", "Mc", "Nd", "Nl", "Cf",
                                                         "Pc")


def _identifier_value(text: str) -> Optional[str]:
    """A name as written (``a.b``, ``#"a b"``), or None when it isn't one."""
    if text and _name_start(text[0]) and _name_part(text[-1]):
        for k in range(1, len(text) - 1):
            ch = text[k]
            if not _name_part(ch) and (ch != "." or text[k - 1] == "."):
                return None
        return text
    if text.startswith("#"):
        return _string_value(text[1:])
    return None


def _field_name_value(text: str) -> Optional[str]:
    """A record field's name: a name, or words joined by spaces and single dots."""
    v = _identifier_value(text)
    if v is not None:
        return v
    if not text or not _name_part(text[0]) or not _name_part(text[-1]):
        return None
    for k, ch in enumerate(text):
        if ch == ".":
            if k + 1 < len(text) and text[k + 1] == ".":
                return None
        elif ch != " " and not _name_part(ch):
            return None
    return text


# --- errors ----------------------------------------------------------------------------------

@dataclass
class SourceError:
    """An error as the editor shows it. Offsets in UTF-16 code units; rows and columns 0-based."""
    text: str
    startPosition: int
    endPosition: int
    startRow: int
    startColumn: int
    endRow: int
    endColumn: int


@dataclass
class CheckResult:
    sourceError: Optional[SourceError]
    canCommit: bool

    @property
    def message(self) -> str:
        """The editor's message row."""
        return NO_SYNTAX_ERRORS if self.sourceError is None else self.sourceError.text

    def to_json(self) -> dict:
        e = self.sourceError
        return {"sourceError": None if e is None else dict(e.__dict__), "canCommit": self.canCommit}


class _Found(Exception):
    def __init__(self, message: str, start: int, end: int):
        super().__init__(message)
        self.message, self.start, self.end = message, start, end


def _utf16(text: str) -> str:
    """``text`` as UTF-16 code units, one per character: a character beyond U+FFFF becomes its
    surrogate pair. Power Query reads text this way, so such a character is no letter to it."""
    if all(ord(ch) <= 0xFFFF for ch in text):
        return text
    out = []
    for ch in text:
        o = ord(ch)
        if o > 0xFFFF:
            o -= 0x10000
            out.append(chr(0xD800 + (o >> 10)))
            out.append(chr(0xDC00 + (o & 0x3FF)))
        else:
            out.append(ch)
    return "".join(out)


def _source_error(units: str, message: str, start: int, end: int) -> SourceError:
    """The error at ``units[start:end]`` (UTF-16 code units) as the editor gets it.

    Rows and columns come from the text: a row ends after ``\\n``, or after a ``\\r`` not followed by
    ``\\n``. The offsets are then rebuilt from them line by line, each line counting its length plus
    one, so a ``\\r\\n`` counts once: after a CRLF they are smaller than the plain index, as Power
    Query's are. A row past the last line adds its preceding lines only."""
    def row_col(i: int) -> tuple[int, int]:
        row, line_start = 0, 0
        for k in range(i):
            ch = units[k]
            if ch == "\n" or ch == "\r" and (k + 1 == len(units) or units[k + 1] != "\n"):
                row, line_start = row + 1, k + 1
        return row, i - line_start

    lines = _read_lines(units)

    def offset(row: int, column: int) -> int:
        return sum(len(line) + 1 for line in lines[:row]) + (column if row < len(lines) else 0)

    sr, sc = row_col(start)
    er, ec = row_col(end)
    # A name in the message is in code units: join its surrogate pairs back into characters.
    message = message.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return SourceError(message, offset(sr, sc), offset(er, ec), sr, sc, er, ec)


def _read_lines(units: str) -> list[str]:
    """The text's lines, split at ``\\r\\n``, ``\\n`` or ``\\r``; a final line break opens no line."""
    lines, k, start, n = [], 0, 0, len(units)
    while k < n:
        ch = units[k]
        if ch in "\r\n":
            lines.append(units[start:k])
            k += 2 if ch == "\r" and k + 1 < n and units[k + 1] == "\n" else 1
            start = k
        else:
            k += 1
    if start < n:
        lines.append(units[start:])
    return lines


# --- the reader -----------------------------------------------------------------------------

@dataclass
class _Node:
    """A node of the tree: what the duplicate and verbatim checks need (names, children, verbatim text), each expression's
    range in the text (its first token's start to its last token's end), and what Applied Steps reads of a step."""
    kind: str = "expr"           # expr, let, record, verbatim, identifier, field, element, constant, parens, implicit, ...
    dup: Optional[str] = None    # parameter, local, field, member
    names: list = field(default_factory=list)   # (name, start, end) of each name's first token
    children: list = field(default_factory=list)
    verbatim: Optional[str] = None
    start: int = 0
    end: int = 0
    ranged: bool = False         # start and end are set
    target: Optional["_Node"] = None   # a field or item access's target
    key: Optional["_Node"] = None      # an item access's key
    ident: Optional[str] = None        # an identifier's name
    inclusive: bool = False            # an identifier written @name
    tree: Optional[ast.Node] = None    # the evaluator's node (offsets in UTF-16 code units until parse_document)


class _Reader:
    def __init__(self, text: str):
        self.text = text
        self.toks = [t for t in tokenize(text) if t.kind != WHITESPACE]
        self.pos = 0
        self.depth = 0

    # token helpers
    @property
    def cur(self) -> str:
        return self.toks[self.pos].kind

    def tok_text(self, i: int) -> str:
        t = self.toks[i]
        return self.text[t.start:t.end]

    def error_at(self, i: int, message: str) -> None:
        t = self.toks[i]
        raise _Found(message, t.start, t.end)

    def read(self) -> int:
        r = self.pos
        if self.toks[self.pos].kind != EOF:
            self.pos += 1
        return r

    def expect(self, kind: str) -> int:
        if self.cur != kind:
            self.error_at(self.pos, _EXPECTED.format(_message_text(kind)))
        return self.read()

    def cur_is_word(self, word: str) -> bool:
        return self.cur == IDENTIFIER and self.tok_text(self.pos) == word

    def try_read(self, kind: str) -> bool:
        if self.cur != kind:
            return False
        self.read()
        return True

    def mk(self, cls, first: int, *args, **kwargs):
        """An evaluator node over the tokens from ``first`` to the last one read."""
        n = cls(*args, **kwargs)
        n.start = self.toks[first].start
        n.end = self.toks[self.pos - 1].end if self.pos > first else n.start
        return n

    def identifier(self) -> tuple[str, int, int]:
        i = self.expect(IDENTIFIER)
        t = self.toks[i]
        value = _identifier_value(self.tok_text(i))
        if value is None:
            self.error_at(i, _INVALID_IDENTIFIER)
        return value, t.start, t.end

    def field_identifier(self) -> tuple[str, int, int]:
        first = last = self.pos
        while self.cur not in (EOF, "]", "=", ","):
            last = self.pos
            self.read()
        a, b = self.toks[first], self.toks[last]
        value = _field_name_value(self.text[a.start:b.end])
        if value is None:
            self.error_at(first, _INVALID_IDENTIFIER)
        return value, a.start, a.end

    def separator(self, terminator: str) -> None:
        if self.cur != terminator and self.cur != EOF:
            comma = self.expect(",")
            if self.cur == terminator:
                self.error_at(comma, _COMMA_BEFORE.format(_message_text(terminator)))

    # documents
    def document(self) -> _Node:
        """The whole text: an expression, or a section document (``kind == "section"``)."""
        self.expect(BOF)
        if self.cur == "section":
            node = self.section()
        else:
            node = self.expression()
            if node.kind == "record" and self.cur == "section":
                self.pos = 0
                self.expect(BOF)
                node = self.section()
            else:
                self.expect(EOF)
        self.expect(EOF)
        return node

    def section(self) -> _Node:
        first = self.pos
        node = _Node(kind="section", dup="member")
        attributes = None
        if self.cur == "[":
            record = self.constant_record()
            node.children.append(record)
            attributes = record.tree
        self.expect("section")
        section_name = self.identifier()[0]
        self.expect(";")
        members = []
        last = None
        while last != self.pos:
            last = self.pos
            save = self.pos
            attribute = self.constant_record() if self.cur == "[" else None
            if self.cur == "section":
                self.pos = save
                break
            if self.cur == EOF:
                break
            shared = self.try_read("shared")
            name = self.identifier()
            self.expect("=")
            value = self.expression()
            self.expect(";")
            node.names.append(name)
            node.children.append(value)   # aligned with names; a constant attribute holds nothing to check
            members.append(self.mk(ast.SectionMember, save, _to_text(name[0]), value.tree, shared,
                                   None if attribute is None else attribute.tree))
        node.kind = "section"
        node.tree = self.mk(ast.Section, first, _to_text(section_name), members, attributes)
        return node

    def constant_record(self) -> _Node:
        first = self.expect("[")
        seen = set()
        last = None
        node = _Node()
        fields = []
        while self.cur not in ("]", EOF) and last != self.pos:
            last = self.pos
            name = self.field_identifier()
            self.expect("=")
            value = self.constant_expression()
            node.children.append(value)
            fields.append(self.mk(ast.RecordField, last, _to_text(name[0]), value.tree))
            if name[0] in seen:
                self.error_at(self.pos, _DUPLICATE_CONSTANT_FIELD.format(name[0]))
            seen.add(name[0])
            self.separator("]")
        self.expect("]")
        node.tree = self.mk(ast.RecordExpr, first, fields)
        return node

    def constant_expression(self) -> _Node:
        if self.cur == "{":
            first = self.expect("{")
            last = None
            node = _Node()
            while self.cur not in ("}", EOF) and last != self.pos:
                last = self.pos
                node.children.append(self.constant_expression())
                self.separator("}")
            self.expect("}")
            node.tree = self.mk(ast.ListExpr, first, [c.tree for c in node.children])
            return node
        if self.cur == "[":
            return self.constant_record()
        return self.literal()

    # expressions
    def ranged(self, node: _Node, first: int) -> _Node:
        """``node`` covering the tokens from ``first`` to the last one read, unless it has its range already."""
        if not node.ranged and self.pos > first:
            node.start, node.end, node.ranged = self.toks[first].start, self.toks[self.pos - 1].end, True
        return node

    def expression(self) -> _Node:
        first = self.pos
        return self.ranged(self.expression_inner(), first)

    def expression_inner(self) -> _Node:
        if self.depth > _MAX_DEPTH:
            self.error_at(self.pos, _TOO_DEEP)
        self.depth += 1
        try:
            first = self.pos
            kind = self.cur
            if kind == "error":
                self.read()
                value = self.expression()
                return _Node(children=[value], tree=self.mk(ast.ErrorRaise, first, value.tree))
            if kind == "each":
                self.read()
                body = self.expression()
                at = self.toks[first].start
                param = ast.Parameter("_", start=at, end=at)
                return _Node(children=[body], tree=self.mk(ast.Function, first, [param], None, body.tree, each_=True))
            if kind == "(":
                return self.function() if self.is_function() else self.coalesce()
            if kind == "let":
                return self.let()
            if kind == "if":
                self.read()
                a = self.expression()
                self.expect("then")
                b = self.expression()
                self.expect("else")
                c = self.expression()
                return _Node(children=[a, b, c], tree=self.mk(ast.If, first, a.tree, b.tree, c.tree))
            if kind == "try":
                return self.try_()
            return self.coalesce()
        finally:
            self.depth -= 1

    def let(self) -> _Node:
        first = self.expect("let")
        node = _Node(kind="let", dup="local")
        bindings = []
        last = None
        while self.cur not in ("in", EOF) and last != self.pos:
            last = self.pos
            name = self.identifier()
            self.expect("=")
            value = self.expression()
            node.names.append(name)
            node.children.append(value)
            bindings.append(self.mk(ast.Binding, last, _to_text(name[0]), value.tree))
            self.separator("in")
        self.expect("in")
        body = self.expression()
        node.children.append(body)
        node.tree = self.mk(ast.Let, first, bindings, body.tree)
        return node

    def try_(self) -> _Node:
        first = self.expect("try")
        protected = self.expression()
        if self.cur == "otherwise":
            self.read()
            other = self.expression()
            return _Node(children=[protected, other], tree=self.mk(ast.Try, first, protected.tree, otherwise=other.tree))
        if self.cur_is_word("catch"):
            self.read()
            params, min_count, has_return, body = self.function_parts()
            if len(params) > 1 or len(params) != min_count or has_return or len(params) == 1 and params[0][3]:
                self.error_at(self.pos, _BAD_CATCH)
            return _Node(children=[protected, body], tree=self.mk(ast.Try, first, protected.tree, catch=body.tree))
        return _Node(children=[protected], tree=self.mk(ast.Try, first, protected.tree))

    def function_parts(self):
        """``(params) [as type] => body``: the parameters (name, start, end, typed), required count, whether a return
        type is given, and the function's node."""
        first = self.pos
        params, min_count, type_nodes, parameters = self.function_type_parameters(explicit=False)
        has_return = self.return_type(required=False, into=type_nodes)
        return_type = type_nodes[-1].tree if has_return else None
        self.expect("=>")
        body = self.expression()
        node = _Node(dup="parameter", names=[p[:3] for p in params], children=type_nodes + [body],
                     tree=self.mk(ast.Function, first, parameters, return_type, body.tree))
        return params, min_count, has_return, node

    def function(self) -> _Node:
        return self.function_parts()[3]

    def function_type_parameters(self, explicit: bool):
        self.expect("(")
        params, type_nodes, parameters = [], [], []
        optional_seen = 0
        min_count = 0
        if self.cur != ")":
            while True:
                last = self.pos
                required = True
                if self.cur_is_word("optional"):
                    required = False
                    self.read()
                name = self.identifier()
                typed = self.return_type(required=explicit, into=type_nodes)
                if required:
                    if optional_seen:
                        self.error_at(self._token_index_at(name[1]), _REQUIRED_AFTER_OPTIONAL)
                    min_count += 1
                else:
                    optional_seen += 1
                params.append((name[0], name[1], name[2], typed))
                parameters.append(self.mk(ast.Parameter, last, _to_text(name[0]),
                                          type_nodes[-1].tree if typed else None, not required))
                self.separator(")")
                if self.cur in (")", EOF) or last == self.pos:
                    break
        self.expect(")")
        return params, min_count, type_nodes, parameters

    def _token_index_at(self, start: int) -> int:
        for k, t in enumerate(self.toks):
            if t.start == start and t.kind != BOF:
                return k
        return self.pos

    def return_type(self, required: bool, into: list) -> bool:
        if required or self.cur == "as":
            self.expect("as")
            into.append(self.type_() if required else self.nullable_primitive_type())
            return True
        return False

    def is_function(self) -> bool:
        save = self.pos
        try:
            depth = 0
            while True:
                kind = self.cur
                if kind == "(":
                    depth += 1
                elif kind == ")":
                    depth -= 1
                elif kind == EOF:
                    return False
                self.read()
                if kind == ")" and depth == 0:
                    break
            self.return_type(required=False, into=[])
            return self.cur == "=>"
        finally:
            self.pos = save

    def chain(self, op: str, operand) -> _Node:
        """``operand (op operand)*``, as a balanced tree."""
        node = _Node(children=[operand()])
        while self.cur == op:
            self.read()
            node.children.append(operand())
        if len(node.children) == 1:
            return node.children[0]
        node.tree = _balanced([c.tree for c in node.children], op)
        return node

    def coalesce(self) -> _Node:
        return self.chain("??", self.logical_or)

    def logical_or(self) -> _Node:
        return self.chain("or", self.logical_and)

    def logical_and(self) -> _Node:
        return self.chain("and", self.is_)

    def is_(self) -> _Node:
        node = self.as_()
        while self.cur == "is":
            self.read()
            t = self.nullable_primitive_type()
            node = _Node(children=[node, t], tree=_binary("is", node.tree, t.tree))
        return node

    def as_(self) -> _Node:
        node = self.equality()
        while self.cur == "as":
            self.read()
            t = self.nullable_primitive_type()
            node = _Node(children=[node, t], tree=_binary("as", node.tree, t.tree))
        return node

    _BINARY = frozenset({"/", "*", "&", "-", "+", ">", ">=", "<", "<=", "=", "<>"})

    def equality(self) -> _Node:
        """The arithmetic, comparison and equality operators, read as Power Query does: operands and operators
        on two stacks, an operator first combining every pending one as tight or tighter (so left to right)."""
        node = _Node(children=[self.metadata()])
        operands = [node.children[0].tree]
        operators: list[str] = []
        while self.cur in self._BINARY:
            op = self.cur
            while operators and _PRECEDENCE[operators[-1]] <= _PRECEDENCE[op]:
                right, left = operands.pop(), operands.pop()
                operands.append(_binary(operators.pop(), left, right))
            operators.append(op)
            self.read()
            node.children.append(self.metadata())
            operands.append(node.children[-1].tree)
        while operators:
            right, left = operands.pop(), operands.pop()
            operands.append(_binary(operators.pop(), left, right))
        if len(node.children) == 1:
            return node.children[0]
        node.tree = operands[0]
        return node

    def metadata(self) -> _Node:
        node = self.unary()
        while self.cur == "meta":
            self.read()
            right = self.unary()
            node = _Node(children=[node, right], tree=_binary("meta", node.tree, right.tree))
        return node

    def unary(self) -> _Node:
        if self.depth > _MAX_DEPTH:
            self.error_at(self.pos, _TOO_DEEP)
        self.depth += 1
        try:
            first = self.pos
            if self.cur in ("+", "-", "not"):
                op = self.cur
                self.read()
                operand = self.unary()
                return _Node(children=[operand], tree=self.mk(ast.Unary, first, op, operand.tree))
            if self.cur == "type":
                self.read()
                found = self.type_constructor()
                t = found if found is not None else self.primitive_type()
                return _Node(children=[t], tree=self.mk(ast.TypeExpr, first, t.tree))
            return self.primary()
        finally:
            self.depth -= 1

    # types
    def try_primitive_type(self) -> Optional[str]:
        if self.cur in (IDENTIFIER, LITERAL, "type") and self.tok_text(self.pos) in _PRIMITIVE_TYPES:
            name = self.tok_text(self.pos)
            self.read()
            return name
        return None

    def primitive_type(self) -> _Node:
        first = self.pos
        name = self.try_primitive_type()
        if name is None:
            self.error_at(self.pos, _INVALID_TYPE)
        return _Node(tree=self.mk(ast.PrimitiveType, first, name))

    def nullable_primitive_type(self) -> _Node:
        first = self.pos
        if self.cur_is_word("nullable"):
            self.read()
            inner = self.primitive_type()
            return _Node(tree=self.mk(ast.NullableType, first, inner.tree))
        return self.primitive_type()

    def type_constructor(self) -> Optional[_Node]:
        first = self.pos
        if self.cur == "[":
            return self.record_type()
        if self.cur == "{":
            self.expect("{")
            item = self.type_()
            self.expect("}")
            return _Node(children=[item], tree=self.mk(ast.ListType, first, item.tree))
        name = self.try_primitive_type()
        if name is not None:
            if name == "function" and self.cur == "(":
                params, _min, type_nodes, parameters = self.function_type_parameters(explicit=True)
                self.return_type(required=True, into=type_nodes)
                return _Node(dup="parameter", names=[p[:3] for p in params], children=type_nodes,
                             tree=self.mk(ast.FunctionType, first, parameters, type_nodes[-1].tree))
            if name == "table":
                if self.cur == "[":
                    row = self.record_type()
                    return _Node(children=[row], tree=self.mk(ast.TableType, first, row.tree))
                if self.cur in (IDENTIFIER, "@", "("):
                    row = self.primary()
                    return _Node(children=[row], tree=self.mk(ast.TableType, first, row.tree))
            return _Node(tree=self.mk(ast.PrimitiveType, first, name))
        if self.cur_is_word("nullable"):
            self.read()
            inner = self.type_()
            return _Node(children=[inner], tree=self.mk(ast.NullableType, first, inner.tree))
        return None

    def type_(self) -> _Node:
        first = self.pos
        found = self.type_constructor()
        if found is not None:
            return found
        expr = self.primary()
        return _Node(children=[expr], tree=self.mk(ast.ExprType, first, expr.tree))

    def record_type(self) -> _Node:
        first = self.expect("[")
        node = _Node(dup="field")
        specs = []
        is_open = False
        last = None
        while self.cur not in ("]", EOF) and last != self.pos:
            last = self.pos
            if self.try_read("..."):
                is_open = True
                break
            optional = False
            if self.cur_is_word("optional"):
                self.read()
                optional = True
            name = self.field_identifier()
            node.names.append(name)
            ftype = None
            if self.try_read("="):
                t = self.type_()
                node.children.append(t)
                ftype = t.tree
            specs.append(self.mk(ast.FieldSpec, last, _to_text(name[0]), ftype, optional))
            self.separator("]")
        self.expect("]")
        node.tree = self.mk(ast.RecordType, first, specs, is_open)
        return node

    # primaries
    def primary(self) -> _Node:
        first = self.pos
        node = self.simple()
        while True:
            if self.cur == "(":
                node = self.ranged(self.invocation(node, first), first)
            elif self.cur == "[":
                node = self.ranged(self.field_access(node, first), first)
            elif self.cur == "{":
                self.expect("{")
                key = self.expression()
                self.expect("}")
                optional = self.try_read("?")
                node = self.ranged(_Node(kind="element", children=[node, key], target=node, key=key,
                                         tree=self.mk(ast.ItemAccess, first, node.tree, key.tree, optional)), first)
            else:
                return node

    def invocation(self, target: _Node, first: int) -> _Node:
        node = _Node(kind="invoke", children=[target], target=target)
        self.expect("(")
        if self.cur != ")":
            while True:
                last = self.pos
                node.children.append(self.expression())
                self.separator(")")
                if self.cur in (")", EOF) or last == self.pos:
                    break
        self.expect(")")
        node.tree = self.mk(ast.Invoke, first, target.tree, [c.tree for c in node.children[1:]])
        return node

    def is_field_projection(self) -> bool:
        return self.cur == "[" and self.toks[min(self.pos + 1, len(self.toks) - 1)].kind == "["

    def field_access(self, target: _Node, first: int) -> _Node:
        kind = "projection" if self.is_field_projection() else "field"
        names = []
        if kind == "projection":
            self.expect("[")
            while True:
                last = self.pos
                self.expect("[")
                names.append(_to_text(self.field_identifier()[0]))
                self.expect("]")
                self.separator("]")
                if self.cur in (EOF, "]") or last == self.pos:
                    break
            self.expect("]")
        else:
            self.expect("[")
            names.append(_to_text(self.field_identifier()[0]))
            self.expect("]")
        optional = self.try_read("?")
        on = None if target.kind == "implicit" else target.tree
        tree = self.mk(ast.FieldProjection, first, on, names, optional) if kind == "projection" \
            else self.mk(ast.FieldAccess, first, on, names[0], optional)
        return _Node(kind=kind, children=[target], target=target, tree=tree)

    def is_field_selection(self) -> bool:
        save = self.pos
        result = False
        if self.try_read("[") and self.cur != "]":
            result = True
            if self.cur != "[":
                while self.cur not in (EOF, "]"):
                    if self.cur == "=":
                        result = False
                        break
                    self.read()
        self.pos = save
        return result

    def simple(self) -> _Node:
        first = self.pos
        return self.ranged(self.simple_inner(), first)

    def simple_inner(self) -> _Node:
        first = self.pos
        kind = self.cur
        if kind == "@":
            self.read()
            name = self.identifier()[0]
            return _Node(kind="identifier", ident=name, inclusive=True,
                         tree=self.mk(ast.Identifier, first, _to_text(name), True))
        if kind == IDENTIFIER:
            name = self.identifier()[0]
            if self.try_read("!"):
                member = self.identifier()[0]
                return _Node(kind="section_identifier",
                             tree=self.mk(ast.SectionAccess, first, _to_text(name), _to_text(member)))
            return _Node(kind="identifier", ident=name, tree=self.mk(ast.Identifier, first, _to_text(name)))
        if kind == "(":
            self.expect("(")
            inner = self.expression()
            self.expect(")")
            return _Node(kind="parens", children=[inner], tree=inner.tree)
        if kind == "[":
            if self.is_field_selection():
                return _Node(kind="implicit")   # _, read by the field access that follows
            return self.record()
        if kind == "{":
            return self.list_()
        if kind == "...":
            self.read()
            return _Node(kind=kind, tree=self.mk(ast.NotImplementedExpr, first))
        if kind in ("#shared", "#sections"):
            self.read()
            return _Node(kind=kind, tree=self.mk(ast.Identifier, first, kind))
        if kind == VERBATIM:
            start = self.toks[self.read()].start
            lit = self.pos
            self.literal()
            value = _string_value(self.tok_text(lit)) if self.tok_text(lit).startswith('"') else None
            return _Node(kind="verbatim", verbatim=value, start=start, end=self.toks[lit].end, ranged=True,
                         tree=self.mk(ast.Literal, first, "verbatim", None if value is None else _to_text(value)))
        return self.literal()

    def literal(self) -> _Node:
        i = self.expect(LITERAL)
        token = self.tok_text(i)
        if not _literal_valid(token):
            self.error_at(i, _INVALID_LITERAL)
        cls, *args = _literal_tree(token)
        return _Node(kind="constant", start=self.toks[i].start, end=self.toks[i].end, ranged=True,
                     tree=self.mk(cls, i, *args))

    def record(self) -> _Node:
        first = self.expect("[")
        node = _Node(kind="record", dup="field")
        fields = []
        last = None
        while self.cur not in ("]", EOF) and last != self.pos:
            last = self.pos
            name = self.field_identifier()
            node.names.append(name)
            self.expect("=")
            value = self.expression()
            node.children.append(value)
            fields.append(self.mk(ast.RecordField, last, _to_text(name[0]), value.tree))
            self.separator("]")
        self.expect("]")
        node.tree = self.mk(ast.RecordExpr, first, fields)
        return node

    def list_(self) -> _Node:
        first = self.expect("{")
        node = _Node()
        items = []
        last = None
        while self.cur not in ("}", EOF) and last != self.pos:
            last = self.pos
            item = self.expression()
            node.children.append(item)
            tree = item.tree
            if self.cur == "..":
                self.read()
                end = self.expression()
                node.children.append(end)
                tree = self.mk(ast.Range, last, item.tree, end.tree)
            items.append(tree)
            self.separator("}")
        self.expect("}")
        node.tree = self.mk(ast.ListExpr, first, items)
        return node


# The arithmetic, comparison and equality operators' precedence, tightest first (Power Query's reader)
_PRECEDENCE = {"/": 0, "*": 0, "&": 1, "-": 1, "+": 1, ">": 2, ">=": 2, "<": 2, "<=": 2, "=": 3, "<>": 3}


def _binary(op: str, left: ast.Node, right: ast.Node) -> ast.Binary:
    return ast.Binary(op, left, right, start=left.start, end=right.end)


def _balanced(operands: list, op: str) -> ast.Node:
    """``a op b op c op d`` as Power Query's reader builds it: neighbours paired, level by level, an odd one out
    carried up: ``(a op b) op (c op d)``; ``a op b op c`` is ``(a op b) op c``."""
    level = list(operands)
    while len(level) > 1:
        paired = [_binary(op, level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            paired.append(level[-1])
        level = paired
    return level[0]


def _number_value(token: str):
    """A valid number literal's value: an int for a whole decimal or hexadecimal number, else a float."""
    if len(token) > 2 and token[1] in "xX":
        return int(token[2:], 16)
    if "." in token or "e" in token or "E" in token:
        return float(token)
    return int(token)


def _literal_tree(token: str) -> tuple:
    """A valid literal token as the evaluator's node: (class, args...)."""
    c = token[0]
    if "0" <= c <= "9" or c == ".":
        return ast.Literal, "number", _number_value(token)
    if c == '"':
        return ast.Literal, "text", _to_text(_string_value(token))
    if token == "null":
        return ast.Literal, "null", None
    if token in ("true", "false"):
        return ast.Literal, "logical", token == "true"
    return ast.Identifier, token          # #date, #table, #infinity, ...


class NoCheckResult(Exception):
    """Power Query's editor gets no answer for this text: its check fails outright. A verbatim literal
    holding anything but text (``#!1``), where it survives the duplicate check, or nesting deep enough
    to overflow the reader's stack (``type {{{…``, which has no depth limit; the depth at which Power Query's
    own reader overflows is unmeasured, ours past about 24,000 list or nullable levels)."""


def _read(units: str) -> tuple[Optional[_Found], Optional[_Node]]:
    """Read ``units`` (UTF-16 code units): the first error, or the tree the later checks walk."""
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 60 * _MAX_DEPTH))
    try:
        return None, _Reader(units).document()
    except _Found as found:
        return found, None
    except RecursionError:
        raise NoCheckResult("the text nests too deeply for Power Query's reader") from None
    finally:
        sys.setrecursionlimit(limit)


def _first_duplicate_of(node: _Node) -> Optional[tuple[str, int, int]]:
    seen = set()
    for name, start, end in node.names:
        if name in seen:
            return _DUPLICATE[node.dup].format(name), start, end
        seen.add(name)
    return None


def _compile_checks(root: _Node) -> Optional[tuple[str, int, int]]:
    """The checks after reading, in Power Query's order: duplicates (outermost first, then in reading
    order), then verbatim literals over what the duplicate check leaves.

    A function, function type, record or record type with a duplicate becomes an error as a whole,
    so nothing inside it is checked further; a let or section drops only the entries whose name is
    duplicated. A verbatim literal left after that holding anything but text fails the check outright
    (``NoCheckResult``), even after an error was found; one holding text that reads as an expression
    is an error."""
    first: Optional[tuple[str, int, int]] = None
    verbatims: list[_Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        kids = node.children
        if node.dup:
            dup = _first_duplicate_of(node)
            if dup is not None:
                if first is None:
                    first = dup
                if node.dup in ("parameter", "field"):
                    kids = []
                else:
                    counts: dict[str, int] = {}
                    for name, _s, _e in node.names:
                        counts[name] = counts.get(name, 0) + 1
                    n = len(node.names)
                    kids = [kids[k] for k in range(n) if counts[node.names[k][0]] == 1] + kids[n:]
        if node.kind == "verbatim":
            verbatims.append(node)
        stack.extend(reversed(kids))
    for node in verbatims:
        if node.verbatim is None:
            raise NoCheckResult("a verbatim literal holds a value that isn't text")
    if first is not None:
        return first
    for node in verbatims:
        found, inner = _read(node.verbatim)
        if found is None and inner.kind != "section":
            return _VERBATIM_READS, node.start, node.end
    return None


def check_query(text: str) -> CheckResult:
    """What the Advanced Editor shows for ``text``: the first error and whether "Done" may save it.
    Raises ``NoCheckResult`` where Power Query's own check gets no answer."""
    if not text:
        return CheckResult(None, True)
    units = _utf16(text)
    found, root = _read(units)
    if found is not None:
        return CheckResult(_source_error(units, found.message, found.start, found.end), True)
    if root.kind == "section":
        return CheckResult(_source_error(units, _INVALID_EXPRESSION, 0, len(units)), True)
    hit = _compile_checks(root)
    if root.kind == "let":
        for name, start, end in root.names:
            if not _surrogates_paired(name):
                return CheckResult(_source_error(units, _INVALID_NAME_SEQUENCE, start, end), False)
    if hit is not None:
        return CheckResult(_source_error(units, *hit), False)
    return CheckResult(None, True)


def _surrogates_paired(units: str) -> bool:
    """Every high surrogate followed by a low one, and no low one alone."""
    k, n = 0, len(units)
    while k < n:
        o = ord(units[k])
        if 0xD800 <= o <= 0xDBFF:
            if k + 1 >= n or not 0xDC00 <= ord(units[k + 1]) <= 0xDFFF:
                return False
            k += 2
            continue
        if 0xDC00 <= o <= 0xDFFF:
            return False
        k += 1
    return True


def escape_source_text(text: str) -> str:
    """What Power Query stores for a query's text: the text itself when it reads as an expression
    (a trailing ``//`` comment closed by a line break, a trailing unclosed ``/*`` by ``*/``), else a
    verbatim literal ``#!"..."`` holding the text, so the query fails only when evaluated."""
    units = _utf16(text)
    found, root = _read(units)
    if found is not None or root.kind == "section":
        return '#!' + _quoted(text)
    toks = tokenize(units)
    for t in reversed(toks[1:-1]):
        if t.kind != WHITESPACE:
            break
        piece = units[t.start:t.end]
        if piece.startswith("//"):
            return text + "\r\n"
        if piece.startswith("/*") and not piece.endswith("*/"):
            return text + "*/"
        if piece.startswith("/"):
            break
    return text


def _quoted(text: str) -> str:
    """``text`` as an M text literal, as Power Query writes one: ``"`` doubled; line feed, carriage
    return and tab as ``#(lf)``, ``#(cr)``, ``#(tab)`` (a CRLF as two escapes); ``#(`` as ``#(#)(``;
    U+0085, U+2028, U+2029, control characters and the non-characters U+FDD0-FDEF, U+FFFE and U+FFFF
    as four-digit hex escapes."""
    out = []
    for k, ch in enumerate(text):
        o = ord(ch)
        if ch == "\n":
            out.append("#(lf)")
        elif ch == "\r":
            out.append("#(cr)")
        elif ch == "\t":
            out.append("#(tab)")
        elif ch == "#":
            out.append("#(#)" if k + 1 < len(text) and text[k + 1] == "(" else "#")
        elif ch == '"':
            out.append('""')
        elif ch in "\u0085  " or unicodedata.category(ch) == "Cc" \
                or 0xFDD0 <= o <= 0xFDEF or 0xFFFE <= o <= 0xFFFF:
            out.append(f"#({o:04X})")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


# --- Applied Steps -------------------------------------------------------------------------------------------

@dataclass
class Step:
    """One applied step, as Power Query's editor lists it. Offsets are UTF-16 code units into the query's text."""
    name: str                     # the variable's name, unescaped (#"Changed Type" -> Changed Type)
    name_start: int
    name_end: int
    text: str                     # the step's expression as written; for a verbatim literal step, the text it holds
    start: int                    # the expression's range in the query's text
    end: int
    description: str              # its leading comments (\r\n between them), "" for none
    error: Optional[SourceError]  # the parse error of a verbatim literal step's text (the error icon), else None
    navigation: bool              # the step shown for the run of navigation steps after the source, under its name
    hidden: bool                  # an earlier step of that run: not listed


@dataclass
class QuerySteps:
    """A query's applied steps, or none (``has_steps`` false: the list shows one item named after the query)."""
    has_steps: bool
    steps: list
    selected: int                 # the step a query opens on: the first with an error, else the last; -1 with none
    error: Optional[SourceError] = None   # with no steps, the one item's error: the text's own parse error


def _to_text(units: str) -> str:
    return units.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


_NET_TRIM = set("\t\n\x0b\x0c\r \x85\xa0")


def _net_trim(s: str) -> str:
    """Trim as .NET's String.Trim does: its white space (not Python's, which also strips U+001C-U+001F)."""
    def ws(c: str) -> bool:
        return c in _NET_TRIM or unicodedata.category(c) in ("Zs", "Zl", "Zp")
    a, b = 0, len(s)
    while a < b and ws(s[a]):
        a += 1
    while b > a and ws(s[b - 1]):
        b -= 1
    return s[a:b]


def _comment_text(comment: str) -> str:
    """A comment's description text: ``//`` stripped and trimmed; a ``/* */`` comment's lines (split at CRLF) trimmed."""
    if comment.startswith("//"):
        return _net_trim(comment[2:])
    if comment.startswith("/*") and comment.endswith("*/") and len(comment) >= 4:
        return "\r\n".join(_net_trim(line) for line in comment[2:-2].split("\r\n"))
    return ""


def _leading_comment(toks: list, name_start: int, units: str) -> str:
    """The comments directly before a step's name: back over whitespace and comments only, nearest last."""
    at = next((k for k, t in enumerate(toks) if t.start == name_start and t.kind != BOF), None)
    if at is None:
        return ""
    parts: list[str] = []
    for t in reversed(toks[1:at]):
        if t.kind != WHITESPACE:
            break
        piece = units[t.start:t.end]
        if piece.startswith("/"):
            parts.insert(0, _comment_text(piece))
    return "\r\n".join(parts)


def _is_navigation(node: _Node) -> bool:
    """A navigation step: X{[k = v, ...]}[F] or X{constant}[F] with X a name, X[F] with X a name, or _{constant}."""
    if node.kind == "field":
        t = node.target
        if t is not None and t.kind == "element":
            return t.target is not None and t.target.kind == "identifier" and t.key is not None \
                and t.key.kind in ("record", "constant")
        return t is not None and t.kind == "identifier"
    if node.kind == "element":
        return node.key is not None and node.key.kind == "constant"
    return False


def query_steps(text: str) -> QuerySteps:
    """The applied steps Power Query's editor lists for a query's text.

    Only a ``let`` with at least one variable whose body is a plain name naming its last variable has steps; each is a
    variable, in order. From the second step, a run of navigation steps shows as its last one, under the query's
    navigation step name; the earlier ones are hidden. A step holding a verbatim literal ``#!"..."`` shows the text it
    holds, with that text's parse error. The query opens on its first step with an error, else its last. A step is
    looked up by name, so of two variables with one name the first answers for both (text, error, description).
    With no steps, ``error`` is the one item's: the text's parse error, or a verbatim literal's text's.
    Raises ``NoCheckResult`` where Power Query's editor gets no answer (a verbatim literal holding anything but text).
    """
    units = _utf16(text)
    found, root = _read(units)
    if found is not None:   # stored as a verbatim literal: one item, with the text's error
        return QuerySteps(False, [], -1, _source_error(units, found.message, found.start, found.end))
    if root.kind == "verbatim":
        return QuerySteps(False, [], -1, _verbatim_error(root))
    if root.kind != "let" or not root.names or not _has_parts(root):
        return QuerySteps(False, [], -1)
    toks = tokenize(units)
    # A step is looked up by its name, the first variable of that name answering for every one of them
    first_of: dict[str, int] = {}
    for k, (name, _s, _e) in enumerate(root.names):
        first_of.setdefault(name, k)
    values = [root.children[first_of[name]] for name, _s, _e in root.names]
    nav_end = len(values) - 1
    for k in range(1, len(values)):
        if not _is_navigation(values[k]):
            nav_end = k - 1
            break
    steps = []
    for k, ((name, name_start, name_end), value) in enumerate(zip(root.names, values)):
        error = None
        if value.kind == "verbatim":
            step_text = _to_text(value.verbatim) if value.verbatim is not None else ""
            error = _verbatim_error(value)
        else:
            step_text = _to_text(units[value.start:value.end])
        named = root.names[first_of[name]]
        steps.append(Step(
            name=_to_text(name), name_start=name_start, name_end=name_end, text=step_text,
            start=value.start, end=value.end,
            description=_to_text(_leading_comment(toks, named[1], units)),
            error=error,
            navigation=k == nav_end and k != 0,
            hidden=0 < k < nav_end,
        ))
    first_error = next((k for k, s in enumerate(steps) if s.error is not None), None)
    chosen = first_error if first_error is not None else len(steps) - 1
    return QuerySteps(True, steps, first_of[root.names[chosen][0]])   # selected by name too


def _has_parts(root: _Node) -> bool:
    """A let whose body is a plain (not @) name naming its last variable."""
    body = root.children[-1]
    return body.kind == "identifier" and not body.inclusive and body.ident == root.names[-1][0]


def _verbatim_error(node: _Node) -> Optional[SourceError]:
    """A verbatim literal's text's first parse error, or None. One holding anything but text has no text at all: Power
    Query's editor fails outright (NoCheckResult)."""
    if node.verbatim is None:
        raise NoCheckResult("a verbatim literal holds a value that isn't text")
    inner_found, _ = _read(node.verbatim)
    if inner_found is None:
        return None
    return _source_error(node.verbatim, inner_found.message, inner_found.start, inner_found.end)


# --- the evaluator's tree ------------------------------------------------------------------------------------

def parse_document(text: str) -> ast.Expr | ast.Section:
    """A query's M as the evaluator's tree (``pbix_mcp.m.ast``): an expression, or a section document. Its offsets
    are indexes into ``text``. Text that doesn't read raises ``MSyntaxError`` with Power Query's message, at the
    first token it couldn't read; so does text nested too deeply for the reader."""
    units = _utf16(text)
    try:
        found, root = _read(units)
    except NoCheckResult:
        raise MSyntaxError(_TOO_DEEP, text, 0) from None
    index = _python_indexes(text, units)
    if found is not None:
        message = found.message.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
        raise MSyntaxError(message, text, found.start if index is None else index[found.start])
    tree = root.tree
    if index is not None:
        for node in ast.walk(tree):
            node.start, node.end = index[node.start], index[node.end]
    return tree


def parse_expression(text: str) -> ast.Expr:
    """``parse_document`` for an expression only: a section document is an ``MSyntaxError``."""
    tree = parse_document(text)
    if isinstance(tree, ast.Section):
        raise MSyntaxError(_INVALID_EXPRESSION, text, 0)
    return tree


def _python_indexes(text: str, units: str) -> Optional[list[int]]:
    """Each UTF-16 offset into ``units`` as an index into ``text``, or None where they are the same."""
    if len(units) == len(text):
        return None
    out = []
    for k, ch in enumerate(text):
        out.extend([k, k] if ord(ch) > 0xFFFF else [k])
    out.append(len(text))
    return out
