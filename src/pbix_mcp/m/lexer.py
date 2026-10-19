"""The M (Power Query formula language) lexer, from the language specification's
lexical grammar (learn.microsoft.com/powerquery-m, "Lexical Structure").

Whitespace is every Unicode Zs character (so the no-break space Desktop's editor
leaves in some queries), tab, the line terminators and VT/FF. Comments are
whitespace. A dot joins two identifiers into one, so ``Table.AddColumn`` is one
identifier, while ``{1..3}`` keeps its range and ``Column1.1`` needs quoting.
``#"..."`` is a quoted identifier, ``#table`` and the other ``#`` words are
keywords, and a text literal decodes its ``""`` and ``#(...)`` escapes.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

KEYWORDS = frozenset({
    "and", "as", "each", "else", "error", "false", "if", "in", "is", "let", "meta", "not", "null", "or",
    "otherwise", "section", "shared", "then", "true", "try", "type",
    "#binary", "#date", "#datetime", "#datetimezone", "#duration", "#infinity", "#nan", "#sections",
    "#shared", "#table", "#time",
})

# Longest first, so "<=" wins over "<" and "..." over "..".
PUNCTUATORS = ("...", "=>", "<=", ">=", "<>", "??", "..", ",", ";", "=", "<", ">", "+", "-", "*", "/",
               "&", "(", ")", "[", "]", "{", "}", "@", "!", "?")

LINE_TERMINATORS = "\r\n\u0085  "

_ESCAPE_NAMES = {"cr": "\r", "lf": "\n", "tab": "\t"}


class MSyntaxError(ValueError):
    """Text that isn't M. ``offset`` is where in the text the problem is."""

    def __init__(self, message: str, text: str, offset: int):
        line = text.count("\n", 0, offset) + 1
        column = offset - (text.rfind("\n", 0, offset) + 1) + 1
        super().__init__(f"{message} (line {line}, column {column})")
        self.message, self.offset, self.line, self.column = message, offset, line, column


@dataclass(frozen=True)
class Token:
    kind: str       # "identifier", "quoted", "keyword", "number", "text", "punct", "verbatim", "end"
    text: str       # the token as written
    value: object   # identifier name, keyword, decoded text, number, or the punctuator
    start: int
    end: int


def is_whitespace(ch: str) -> bool:
    return ch in "\t\x0b\x0c" or ch in LINE_TERMINATORS or unicodedata.category(ch) == "Zs"


def _is_identifier_start(ch: str) -> bool:
    return ch == "_" or unicodedata.category(ch) in ("Lu", "Ll", "Lt", "Lm", "Lo", "Nl")


def _is_identifier_part(ch: str) -> bool:
    return ch == "_" or unicodedata.category(ch) in ("Lu", "Ll", "Lt", "Lm", "Lo", "Nl", "Nd", "Pc", "Mn", "Mc",
                                                     "Cf")


def tokenize(text: str) -> list[Token]:
    """Every token in ``text``, ending with one ``end`` token."""
    tokens: list[Token] = []
    i, n = 0, len(text)
    while True:
        i = _skip_whitespace(text, i)
        if i >= n:
            tokens.append(Token("end", "", None, n, n))
            return tokens
        ch = text[i]
        if ch == '"':
            value, j = _text_literal(text, i + 1)
            tokens.append(Token("text", text[i:j], value, i, j))
        elif ch == "#":
            tokens.append(_hash_token(text, i))
            j = tokens[-1].end
        elif ch.isdigit() and ch.isascii() or ch == "." and i + 1 < n and text[i + 1].isdigit():
            number, j = _number_literal(text, i)
            tokens.append(Token("number", text[i:j], number, i, j))
        elif _is_identifier_start(ch):
            j = _identifier_end(text, i)
            word = text[i:j]
            tokens.append(Token("keyword" if word in KEYWORDS else "identifier", word, word, i, j))
        else:
            for p in PUNCTUATORS:
                if text.startswith(p, i):
                    j = i + len(p)
                    tokens.append(Token("punct", p, p, i, j))
                    break
            else:
                raise MSyntaxError(f"Unexpected character {ch!r}", text, i)
        i = j


def _skip_whitespace(text: str, i: int) -> int:
    n = len(text)
    while i < n:
        ch = text[i]
        if is_whitespace(ch):
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] not in LINE_TERMINATORS:
                i += 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise MSyntaxError("Unterminated comment", text, i)
            i = j + 2
        else:
            break
    return i


def _identifier_end(text: str, i: int) -> int:
    n = len(text)
    i += 1
    while i < n:
        ch = text[i]
        if _is_identifier_part(ch):
            i += 1
        elif ch == "." and i + 1 < n and _is_identifier_start(text[i + 1]):
            i += 1
        else:
            break
    return i


def _hash_token(text: str, i: int) -> Token:
    if text.startswith('#"', i):
        value, j = _text_literal(text, i + 2)
        return Token("quoted", text[i:j], value, i, j)
    if text.startswith('#!"', i):
        value, j = _text_literal(text, i + 3)
        return Token("verbatim", text[i:j], value, i, j)
    j = i + 1
    while j < len(text) and text[j].isalpha():
        j += 1
    word = text[i:j]
    if word not in KEYWORDS:
        raise MSyntaxError(f"Unknown keyword {word!r}", text, i)
    return Token("keyword", word, word, i, j)


def _text_literal(text: str, i: int) -> tuple[str, int]:
    """Decode a text literal whose opening quote ends at ``i``; return it and the index after its closing quote."""
    out: list[str] = []
    n = len(text)
    start = i
    while i < n:
        ch = text[i]
        if ch == '"':
            if i + 1 < n and text[i + 1] == '"':
                out.append('"')
                i += 2
                continue
            return "".join(out), i + 1
        if text.startswith("#(", i):
            j = text.find(")", i + 2)
            if j < 0:
                raise MSyntaxError("Unterminated escape sequence", text, i)
            out.append(_escape(text, i, text[i + 2:j]))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    raise MSyntaxError("Unterminated text literal", text, start - 1)


def _escape(text: str, at: int, body: str) -> str:
    if body == "#":
        return "#("
    out = []
    for part in body.split(","):
        if part in _ESCAPE_NAMES:
            out.append(_ESCAPE_NAMES[part])
        elif len(part) in (4, 8) and all(c in "0123456789abcdefABCDEF" for c in part):
            out.append(chr(int(part, 16)))
        else:
            raise MSyntaxError(f"Invalid escape sequence #({body})", text, at)
    return "".join(out)


def _number_literal(text: str, i: int) -> tuple[int | float, int]:
    n = len(text)
    if text.startswith(("0x", "0X"), i):
        j = i + 2
        while j < n and text[j] in "0123456789abcdefABCDEF":
            j += 1
        if j == i + 2:
            raise MSyntaxError("Invalid hexadecimal number", text, i)
        return int(text[i + 2:j], 16), j
    j = i
    while j < n and text[j].isascii() and text[j].isdigit():
        j += 1
    is_float = False
    if j < n and text[j] == "." and j + 1 < n and text[j + 1].isascii() and text[j + 1].isdigit():
        is_float = True
        j += 1
        while j < n and text[j].isascii() and text[j].isdigit():
            j += 1
    if j < n and text[j] in "eE":
        k = j + 1
        if k < n and text[k] in "+-":
            k += 1
        if k < n and text[k].isascii() and text[k].isdigit():
            is_float = True
            j = k
            while j < n and text[j].isascii() and text[j].isdigit():
                j += 1
    raw = text[i:j]
    return (float(raw) if is_float else int(raw)), j
