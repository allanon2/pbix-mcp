"""DAX queries: ``[DEFINE ...] EVALUATE <table> [ORDER BY ...]``.

A query is split into its statements by a scanner that knows DAX's strings,
quoted names, bracketed names, comments and parentheses; each expression is
handed to the engine as written, never rewritten. Each EVALUATE's table is
evaluated as a measure is (DAXEngine.evaluate_measure with ``as_table``), so
every check, error record, unsupported-function record and time budget of a
measure evaluation applies to it.

Before anything runs, the query's syntax is checked (_check_syntax) and its
names resolved (_resolve_names), with Power BI Desktop 2.157's texts and
(line, column) positions, as Desktop does: a name error in a later EVALUATE
returns no result at all. DEFINE MEASURE, VAR, TABLE and COLUMN live for the
query alone (a VAR is a query-level constant every expression sees, in any
order of definition; a TABLE or COLUMN goes into a transient copy of the
tables, never the model).

Unknown is never silent: a construct this route does not run yet (DEFINE
FUNCTION, START AT) is refused by name (DAXUnsupportedError), an expression
that hit an unsupported function fails the query naming it, an error fails
the query with its message (an error at run time carries the engine's text,
without a position), and an expression that gives no table fails it too. A
result is never an empty table standing in for one of those.

What comes back per EVALUATE: ``{"columns": [{"name", "type",
"type_source"}], "rows": [[...]], "columns_known"}``, rows as positional
lists in the engine's order (or ORDER BY's, BLANK just below its column's
zero). A column of a model table is named ``Table[Column]``, any other
``[Name]``. Types are Boolean, DateTime, Text, Decimal, Double, Integer or
Variant (``type_source``): a model column's from the model when the caller
passes its column types (``"model"``), else from its cells (``"values"``);
a new column's from its expression (``"static"``, DAXEngine._static_type:
MEDIAN of integers is a Variant, BLANK() alone an Integer) or, when that is
unknown, from its values (``"values"``). An empty result keeps the columns
its expression declares (_schema); for a shape not read there, its
``columns`` is empty and ``columns_known`` False.
"""
from __future__ import annotations

import datetime as _dt
import decimal
from dataclasses import dataclass, field
from typing import Optional

from pbix_mcp.errors import (
    DAXEvaluationError,
    DAXParseError,
    DAXUnsupportedError,
)

# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


@dataclass
class _Tok:
    kind: str        # 'word', 'str', 'qname', 'bname', 'punct', 'open', 'close'
    text: str
    start: int
    end: int
    depth: int       # parenthesis/brace depth before the token


def _scan(text: str) -> list:
    """The tokens of ``text``: comments and whitespace dropped, strings,
    'quoted' and [bracketed] names kept whole (DAX doubles a quote or a
    closing bracket to escape it)."""
    toks: list = []
    i, n, depth = 0, len(text), 0
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        two = text[i:i + 2]
        if two in ('//', '--'):
            j = text.find('\n', i)
            i = n if j < 0 else j + 1
            continue
        if two == '/*':
            j = text.find('*/', i + 2)
            if j < 0:
                raise DAXParseError("The query has a comment that is never closed (/* without */).")
            i = j + 2
            continue
        if ch in '"\'[':
            close = ']' if ch == '[' else ch
            j = i + 1
            while True:
                k = text.find(close, j)
                if k < 0:
                    what = {'"': 'text', "'": 'table name', '[': 'name'}[ch]
                    raise DAXParseError(f"The query has a {what} that is never closed, at {text[i:i + 30]!r}.")
                if k + 1 < n and text[k + 1] == close:
                    j = k + 2
                    continue
                break
            kind = {'"': 'str', "'": 'qname', '[': 'bname'}[ch]
            toks.append(_Tok(kind, text[i:k + 1], i, k + 1, depth))
            i = k + 1
            continue
        if ch in '({':
            toks.append(_Tok('open', ch, i, i + 1, depth))
            depth += 1
            i += 1
            continue
        if ch in ')}':
            if depth == 0:
                # hypothesis: Desktop's syntax error at the token, as for
                # any token out of place (not measured for a closing one)
                raise _syntax_error(text, _Tok('close', ch, i, i + 1, 0))
            depth -= 1
            toks.append(_Tok('close', ch, i, i + 1, depth))
            i += 1
            continue
        if ch.isalnum() or ch in '_.':
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in '_.'):
                j += 1
            toks.append(_Tok('word', text[i:j], i, j, depth))
            i = j
            continue
        toks.append(_Tok('punct', ch, i, i + 1, depth))
        i += 1
    if depth:
        # Desktop 2.157: EVALUATE TOPN(2, 'Sales stage' -> no position
        raise DAXParseError(_END_OF_INPUT)
    return toks


# ---------------------------------------------------------------------------
# Syntax: Desktop's messages, and where
# ---------------------------------------------------------------------------
#
# Power BI Desktop 2.157 (2026-11-12, over its loopback ADOMD; the DAX
# query view shows the same text): a token out of place is
#   Query (1, 22) The syntax for '*' is incorrect. (EVALUATE ROW("x", 1 +* 2)).
# -- (line, column), 1-based, of the token, and the whole query in the
# brackets -- and a query that stops before it is complete (an open
# parenthesis, DEFINE with no EVALUATE) is "The end of the input was
# reached." with no position. Desktop checks a query's syntax and names
# before it runs any EVALUATE: a name error in the second of three
# EVALUATEs returns no result at all.

_END_OF_INPUT = "The end of the input was reached."


def _position(text: str, offset: int) -> tuple:
    """(line, column) of ``offset`` in ``text``, both 1-based."""
    line = text.count('\n', 0, offset) + 1
    return line, offset - (text.rfind('\n', 0, offset) + 1) + 1


def _query_error(text: str, offset: int, message: str) -> str:
    line, col = _position(text, offset)
    return f"Query ({line}, {col}) {message}"


def _syntax_error(text: str, tok: '_Tok') -> DAXParseError:
    return DAXParseError(_query_error(
        text, tok.start, f"The syntax for '{tok.text}' is incorrect. ({text})."))


# Operators between two operands. Two-character ones are scanned as two
# 'punct' tokens side by side.
_BINARY = frozenset(['+', '-', '*', '/', '^', '&', '=', '<', '>',
                     '<=', '>=', '<>', '==', '&&', '||'])
_TWO_CHAR = frozenset(['<=', '>=', '<>', '==', '&&', '||'])


def _operator_at(toks: list, k: int, j: int) -> tuple:
    """The operator text at token ``k`` (before ``j``) and the index after it."""
    t = toks[k]
    if k + 1 < j:
        u = toks[k + 1]
        if u.kind == 'punct' and u.start == t.end and t.text + u.text in _TWO_CHAR:
            return t.text + u.text, k + 2
    return t.text, k + 1


def _check_syntax(text: str, toks: list, i: int, j: int) -> None:
    """Raise Desktop's syntax error for the first token of toks[i:j] (one
    expression) that can't stand where it is, or its end-of-input error
    when the expression stops before it is complete.

    A recogniser, never a rewrite: operands (a number, text, a name, a
    'Table'[Column] or Table[Column], a call, a parenthesised expression, a
    {table constructor}) and binary operators alternate; + - and NOT may
    open an operand; VAR name = expression ... RETURN expression. An empty
    argument of a call (ROW("x", ) or a skipped optional one) is not a
    syntax error: the function decides whether it needs the argument."""
    # frames: [kind, open VAR blocks]; kind 'top', 'call', 'group', 'brace',
    # 'tuple'. A VAR block can be a VAR's value (VAR a = VAR b = 1 RETURN b
    # RETURN a), so the blocks open in a frame are counted.
    frames: list = [['top', 0]]
    continuing = False           # the VAR read next continues an open block
    want_operand = True
    just_opened = False          # the last token opened a call or followed a call's comma
    k = i
    while k < j:
        t = toks[k]
        frame = frames[-1]
        if want_operand:
            if t.kind == 'word':
                w = t.text.upper()
                if w == 'NOT':
                    k += 1
                    just_opened = False
                    continue
                if w == 'VAR':
                    # VAR <name> = <expression>
                    if k + 2 >= j or toks[k + 1].kind not in ('word', 'bname') or not (
                            toks[k + 2].kind == 'punct' and toks[k + 2].text == '='):
                        bad = toks[k + 1] if k + 1 < j else None
                        if bad is not None and bad.kind in ('word', 'bname') and k + 2 < j:
                            bad = toks[k + 2]
                        if bad is None:
                            raise DAXParseError(_END_OF_INPUT)
                        raise _syntax_error(text, bad)
                    if not continuing:
                        frame[1] += 1
                    continuing = False
                    k += 3
                    just_opened = False
                    continue
                if w == 'RETURN':
                    raise _syntax_error(text, t)
                # a name, number or function: Table[Column] is one operand
                if k + 1 < j and toks[k + 1].kind == 'open' and toks[k + 1].text == '(':
                    frames.append(['call', 0])
                    k += 2
                    just_opened = True
                    continue
                if k + 1 < j and toks[k + 1].kind == 'bname':
                    k += 1
                k += 1
                want_operand = False
                just_opened = False
                continue
            if t.kind in ('str', 'bname'):
                k += 1
                want_operand = False
                just_opened = False
                continue
            if t.kind == 'qname':
                k += 2 if (k + 1 < j and toks[k + 1].kind == 'bname') else 1
                want_operand = False
                just_opened = False
                continue
            if t.kind == 'open':
                # a ( inside { } is a row of a table constructor: {(1, "a")}
                kind = 'brace' if t.text == '{' else ('tuple' if frame[0] == 'brace' else 'group')
                frames.append([kind, 0])
                k += 1
                just_opened = False
                continue
            if t.kind == 'punct' and t.text in ('+', '-'):
                k += 1
                just_opened = False
                continue
            if t.kind == 'punct' and t.text == ',' and frame[0] == 'call' and not frame[1]:
                k += 1               # an empty argument
                just_opened = True
                continue
            if t.kind == 'close' and t.text == ')' and frame[0] == 'call' and just_opened:
                frames.pop()         # f() or a trailing empty argument
                k += 1
                want_operand = False
                just_opened = False
                continue
            raise _syntax_error(text, t)
        # an operand has been read: an operator, a separator or a close
        if t.kind == 'punct':
            if t.text == ',':
                # a 'group' too: a row constructor, ('T'[a], 'T'[b]) IN {(1, "x")}
                if frame[0] in ('call', 'brace', 'tuple', 'group') and not frame[1]:
                    k += 1
                    want_operand = True
                    just_opened = frame[0] == 'call'
                    continue
                raise _syntax_error(text, t)
            op, nk = _operator_at(toks, k, j)
            if op in _BINARY:
                k = nk
                want_operand = True
                just_opened = False
                continue
            raise _syntax_error(text, t)
        if t.kind == 'word':
            w = t.text.upper()
            if w == 'IN':
                k += 1
                want_operand = True
                continue
            if w == 'RETURN' and frame[1]:
                frame[1] -= 1
                k += 1
                want_operand = True
                continue
            if w == 'VAR' and frame[1]:
                want_operand = True       # the next declaration of the block
                continuing = True
                continue
            raise _syntax_error(text, t)
        if t.kind == 'close':
            want_open = '(' if t.text == ')' else '{'
            if frame[0] == 'top' or frame[1] or (want_open == '{') != (frame[0] == 'brace'):
                raise _syntax_error(text, t)
            frames.pop()
            k += 1
            continue
        raise _syntax_error(text, t)
    if want_operand or len(frames) > 1 or frames[0][1]:
        raise DAXParseError(_END_OF_INPUT)


def _unquote_name(tok: _Tok) -> str:
    if tok.kind == 'qname':
        return tok.text[1:-1].replace("''", "'")
    if tok.kind == 'bname':
        return tok.text[1:-1].replace(']]', ']')
    return tok.text


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_DEFINE_KINDS = ('MEASURE', 'VAR', 'TABLE', 'COLUMN', 'FUNCTION')


@dataclass
class Definition:
    kind: str                    # MEASURE, VAR, TABLE, COLUMN, FUNCTION
    table: Optional[str]         # MEASURE / COLUMN: the table named
    name: str
    expression: str


@dataclass
class OrderItem:
    expression: str
    descending: bool


@dataclass
class Evaluate:
    expression: str
    order_by: list = field(default_factory=list)
    start_at: list = field(default_factory=list)
    offset: int = 0              # where its EVALUATE keyword starts in the query
    span: tuple = (0, 0)         # its expression's tokens, [first, last + 1)


@dataclass
class DaxQuery:
    definitions: list
    evaluates: list
    text: str = ''
    toks: list = field(default_factory=list)
    heads: frozenset = frozenset()   # indices of the tokens that name a definition


def _is_word(tok: _Tok, *words: str) -> bool:
    return tok.kind == 'word' and tok.depth == 0 and tok.text.upper() in words


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = _scan(text)
        self.i = 0
        self.heads: set = set()

    def peek(self, k: int = 0) -> Optional[_Tok]:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def at_word(self, *words: str) -> bool:
        t = self.peek()
        return t is not None and _is_word(t, *words)

    def at_order_by(self) -> bool:
        t, u = self.peek(), self.peek(1)
        return (t is not None and u is not None and _is_word(t, 'ORDER') and _is_word(u, 'BY'))

    def at_start_at(self) -> bool:
        t, u = self.peek(), self.peek(1)
        return (t is not None and u is not None and _is_word(t, 'START') and _is_word(u, 'AT'))

    def expect_word(self, word: str) -> None:
        t = self.peek()
        if t is None or not _is_word(t, word):
            found = 'the end of the query' if t is None else repr(t.text)
            raise DAXParseError(f"The query expects {word} here, found {found}.")
        self.i += 1

    def expression(self, what: str, stop_order: bool = False, check: bool = True) -> str:
        """The text of one expression from here to the next statement at
        depth 0. VAR belongs to the expression where it starts it, follows
        its RETURN, or follows a VAR of the expression whose RETURN has not
        come yet; anywhere else it starts a definition."""
        start = self.i
        fresh = True            # nothing of the expression read since its start or a RETURN
        open_vars = False       # a VAR of the expression waits for its RETURN
        while True:
            t = self.peek()
            if t is None:
                break
            if t.depth == 0 and t.kind == 'word':
                w = t.text.upper()
                if w == 'VAR':
                    if not (fresh or open_vars):
                        break
                    open_vars = True
                    self.i += 1
                    fresh = False
                    continue
                if w == 'RETURN':
                    open_vars = False
                    self.i += 1
                    fresh = True
                    continue
                if w in ('EVALUATE', 'DEFINE') or (w in _DEFINE_KINDS and w != 'VAR'):
                    break
                if stop_order and (self.at_order_by() or self.at_start_at()):
                    break
            self.i += 1
            fresh = False
        if self.i == start:
            t = self.peek()
            if t is None:
                raise DAXParseError(_END_OF_INPUT)
            raise _syntax_error(self.text, t)
        if check:
            _check_syntax(self.text, self.toks, start, self.i)
        return self.text[self.toks[start].start:self.toks[self.i - 1].end].strip()

    def comma_list(self, what: str) -> list:
        """Expressions separated by commas at depth 0, up to the next
        statement; each is the list of its tokens."""
        items: list = []
        start = self.i
        while True:
            t = self.peek()
            end = t is None or (t.depth == 0 and t.kind == 'word' and (
                t.text.upper() in ('EVALUATE', 'DEFINE') or self.at_start_at() or self.at_order_by()))
            if end or (t is not None and t.kind == 'punct' and t.text == ',' and t.depth == 0):
                if self.i == start:
                    raise DAXParseError(f"The query has an empty item in {what}.")
                items.append(self.toks[start:self.i])
                if end:
                    break
                self.i += 1
                start = self.i
                continue
            self.i += 1
        return items

    def parse(self) -> DaxQuery:
        defs: list = []
        evals: list = []
        if not self.toks:
            raise DAXParseError("The query is empty.")
        if not self.at_word('DEFINE', 'EVALUATE'):
            # Desktop 2.157: EVALUAT ROW("x", 1) is a syntax error at ROW,
            # the first token that can't follow what came before
            _check_syntax(self.text, self.toks, 0, len(self.toks))
        if self.at_word('DEFINE'):
            self.i += 1
            if not self.at_word(*_DEFINE_KINDS):
                raise DAXParseError("DEFINE is followed by no definition (MEASURE, VAR, TABLE, COLUMN or FUNCTION).")
            while self.at_word(*_DEFINE_KINDS):
                defs.append(self.definition())
        while self.peek() is not None:
            if self.at_word('DEFINE'):
                raise DAXParseError("DEFINE comes once, before the first EVALUATE.")
            at = self.peek()
            self.expect_word('EVALUATE')
            first = self.i
            ev = Evaluate(self.expression('EVALUATE', stop_order=True))
            ev.offset, ev.span = at.start, (first, self.i)
            if self.at_order_by():
                self.i += 2
                for toks in self.comma_list('ORDER BY'):
                    desc = False
                    last = toks[-1]
                    if len(toks) > 1 and _is_word(last, 'ASC', 'DESC'):
                        desc = last.text.upper() == 'DESC'
                        toks = toks[:-1]
                    first = self.toks.index(toks[0])
                    _check_syntax(self.text, self.toks, first, first + len(toks))
                    ev.order_by.append(OrderItem(self.text[toks[0].start:toks[-1].end].strip(), desc))
            if self.at_start_at():
                self.i += 2
                ev.start_at = [self.text[ts[0].start:ts[-1].end].strip()
                               for ts in self.comma_list('START AT')]
            evals.append(ev)
            t = self.peek()
            if t is not None and not _is_word(t, 'EVALUATE', 'DEFINE'):
                raise DAXParseError(f"The query has {t.text!r} where a statement (EVALUATE) belongs.")
        if not evals:
            # Desktop 2.157: DEFINE VAR v = 1 alone
            raise DAXParseError(_END_OF_INPUT)
        return DaxQuery(defs, evals, self.text, self.toks, frozenset(self.heads))

    def definition(self) -> Definition:
        head = self.peek()
        assert head is not None             # parse() calls it at a definition keyword
        kind = head.text.upper()
        self.i += 1
        table = None
        if kind in ('MEASURE', 'COLUMN'):
            t, b = self.peek(), self.peek(1)
            if (t is not None and b is not None and t.kind in ('qname', 'word') and b.kind == 'bname'):
                table, name = _unquote_name(t), _unquote_name(b)
                self.heads.update((self.i, self.i + 1))
                self.i += 2
            elif t is not None and t.kind == 'bname':
                # Desktop 2.157: DEFINE MEASURE [m] = 1 is a syntax error at
                # the '=': a query measure needs its table
                if b is None:
                    raise DAXParseError(_END_OF_INPUT)
                raise _syntax_error(self.text, b)
            else:
                raise DAXParseError(f"DEFINE {kind} names a table and a name, as 'Table'[Name].")
        else:
            t = self.peek()
            if t is None or t.kind not in ('word', 'qname', 'bname'):
                raise DAXParseError(f"DEFINE {kind} is followed by no name.")
            name = _unquote_name(t)
            self.heads.add(self.i)
            self.i += 1
        t = self.peek()
        if t is None or not (t.kind == 'punct' and t.text == '='):
            raise DAXParseError(f"DEFINE {kind} {name} has no '=' before its expression.")
        self.i += 1
        # a FUNCTION's parameters (=>) aren't read here: refused by name later
        return Definition(kind, table, name, self.expression(f"DEFINE {kind} {name}",
                                                             check=kind != 'FUNCTION'))


def parse_query(text: str) -> DaxQuery:
    """The statements of DAX query ``text`` (DAXParseError when it is no query)."""
    return _Parser(text or '').parse()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

# Model DataType names -> the result column types (Boolean, DateTime, Text,
# Decimal, Double, Integer, Variant).
_MODEL_TYPES = {
    'string': 'Text', 'int64': 'Integer', 'double': 'Double', 'decimal': 'Decimal',
    'datetime': 'DateTime', 'boolean': 'Boolean',
}


def _value_type(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, bool):
        return 'Boolean'
    if isinstance(v, (_dt.datetime, _dt.date)):
        return 'DateTime'
    if isinstance(v, str):
        return 'Text'
    if type(v).__name__ == '_Currency' or isinstance(v, decimal.Decimal):
        return 'Decimal'
    if isinstance(v, int):
        return 'Integer'
    if isinstance(v, float):
        return 'Double'
    return 'Variant'


def _column_type(values: list) -> str:
    kinds = {k for k in map(_value_type, values) if k is not None}
    if kinds == {'Integer', 'Double'}:
        return 'Double'
    if len(kinds) == 1:
        return kinds.pop()
    return 'Variant'


_SCALARS = (type(None), bool, int, float, str, _dt.datetime, _dt.date, decimal.Decimal)


def _cell(v):
    if isinstance(v, decimal.Decimal) or type(v).__name__ == '_Currency':
        return float(v)
    return v


_CONSTRUCTOR_NAME = frozenset(['value', 'date'])   # {..}'s [Value], CALENDAR's [Date]


def _resolve_names(q: DaxQuery, tables: dict, measures: dict,
                   model_columns: Optional[dict] = None,
                   measure_tables: Optional[dict] = None) -> None:
    """Desktop's name errors, at their tokens, before any EVALUATE runs.

    Power BI Desktop 2.157 resolves a query's names first: a name error in
    the second of three EVALUATEs returns no result at all (2026-11-12,
    "multi middle semantic error"). Measured texts:
      EVALUATE NoSuchTable -> Query (1, 10) Failed to resolve name
        'NoSuchTable'. It is not a valid table, variable, or function name.
      EVALUATE 'No Such Table' -> Query (1, 10) Cannot find table 'No Such Table'.
      SUM('Sales stage'[NoSuchColumn]) -> Query (1, 23) Column 'NoSuchColumn'
        in table 'Sales stage' cannot be found or may not be used in this
        expression.  (at the table's token)
      ROW("x", [NoSuchMeasure]) -> Query (1, 19) The value for
        'NoSuchMeasure' cannot be determined. Either the column doesn't
        exist, or there is no current row for this column.
    Only a name that resolves nowhere is refused: a bare [Name] that is no
    measure, no column of any table, no name the query writes as text (ROW,
    ADDCOLUMNS ...) and not a constructor's [Value]/[ValueN] or CALENDAR's
    [Date]. Anything subtler (a column outside a row context) is left to the
    engine. A bare word is checked only as a whole EVALUATE (the case
    measured): inside an expression it may be a keyword argument (ASC, SKIP,
    REL, INTEGER ...).

    Names are the model's: ``model_columns`` (every table and column, rows or
    none), the materialised ``tables`` and the tables measures live on --
    ``tables`` alone holds only tables with rows. A bare [Name] is checked
    only when the query calls no function that makes columns of names of its
    own (COLUMNSTATISTICS and the other SYSTEM functions, INFO.*) and none
    the engine's catalog doesn't know."""
    from pbix_mcp.dax.function_catalog import FUNCTION_CATALOG
    text, toks = q.text, q.toks
    mcols = {str(t): {str(c) for c in (cs or ())} for t, cs in (model_columns or {}).items()}
    for t, tb in tables.items():
        if isinstance(tb, dict):
            mcols.setdefault(str(t), set()).update(str(c) for c in (tb.get('columns') or ()))
    for t in (measure_tables or {}).values():
        if isinstance(t, str):
            mcols.setdefault(t, set())
    tlow = {t.lower(): t for t in mcols}
    col_low = {t.lower(): {c.lower() for c in cs} for t, cs in mcols.items()}
    def_tables = {d.name.lower() for d in q.definitions if d.kind == 'TABLE'}
    var_names = {d.name.lower() for d in q.definitions if d.kind == 'VAR'}
    var_names |= {toks[k + 1].text.lower() for k in range(len(toks) - 1)
                  if _is_word_any(toks[k], 'VAR') and toks[k + 1].kind == 'word'}
    m_low = {m.lower() for m in measures}
    m_low |= {d.name.lower() for d in q.definitions if d.kind == 'MEASURE'}
    def_cols = {(d.table.lower(), d.name.lower()) for d in q.definitions if d.kind == 'COLUMN'}
    all_cols = {c for cs in col_low.values() for c in cs}
    all_cols |= {n for _t, n in def_cols}
    known_fns = {n: i for n, i in FUNCTION_CATALOG}
    called = {toks[k].text.upper() for k in range(len(toks) - 1)
              if toks[k].kind == 'word' and toks[k + 1].kind == 'open' and toks[k + 1].text == '('}
    bare_checked = not any(f not in known_fns or known_fns[f] == 'SYSTEM' or f.startswith('INFO.')
                           for f in called)
    literals = {t.text[1:-1].replace('""', '"').lower() for t in toks if t.kind == 'str'}

    for ev in q.evaluates:
        a, b = ev.span
        if b - a == 1:
            t = toks[a]
            w = t.text.lower()
            if (t.kind == 'word' and not t.text[0].isdigit() and w not in tlow
                    and w not in def_tables and w not in var_names):
                raise DAXEvaluationError(_query_error(
                    text, t.start, f"Failed to resolve name '{t.text}'. It is not a valid "
                    "table, variable, or function name."))

    def column_check(k: int, tname: str) -> None:
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        if nxt is None or nxt.kind != 'bname' or (k + 1) in q.heads:
            return
        col = _unquote_name(nxt).lower()
        cols = col_low[tname.lower()]
        if col not in cols and col not in m_low and (tname.lower(), col) not in def_cols:
            raise DAXEvaluationError(_query_error(
                text, toks[k].start, f"Column '{_unquote_name(nxt)}' in table '{tname}' cannot "
                "be found or may not be used in this expression."))

    for k, t in enumerate(toks):
        if k in q.heads:
            continue
        if t.kind == 'qname':
            tname = _unquote_name(t)
            if tname == '':
                continue                      # ''[Measure]
            if tname.lower() in tlow:
                column_check(k, tname)
            elif tname.lower() not in def_tables:
                raise DAXEvaluationError(_query_error(text, t.start, f"Cannot find table '{tname}'."))
        elif t.kind == 'word' and t.text.lower() in tlow:
            column_check(k, t.text)
        elif t.kind == 'bname':
            prev = toks[k - 1] if k else None
            if prev is not None and prev.kind in ('qname', 'word'):
                continue                      # Table[Column], a variable's t[Value]
            if not bare_checked:
                continue
            n = _unquote_name(t).lower()
            if (n in m_low or n in all_cols or n in literals or n in _CONSTRUCTOR_NAME
                    or (n.startswith('value') and n[5:].isdigit())):
                continue
            raise DAXEvaluationError(_query_error(
                text, t.start, f"The value for '{_unquote_name(t)}' cannot be determined. Either "
                "the column doesn't exist, or there is no current row for this column."))


def _is_word_any(tok: _Tok, *words: str) -> bool:
    return tok.kind == 'word' and tok.text.upper() in words


def _engine_message(msg: str) -> str:
    """The engine's error text without the code its exceptions print
    before it ("[DAX_EVAL_FAILED] ..."): Desktop shows the message alone."""
    if msg.startswith('[DAX_'):
        end = msg.find('] ')
        if end > 0:
            return msg[end + 2:]
    return msg


def _qualified_measure(defs_table: str, name: str, ctx_tables: dict, kind: str = 'MEASURE') -> None:
    if defs_table not in ctx_tables and defs_table.lower() not in {t.lower() for t in ctx_tables}:
        raise DAXEvaluationError(
            f"DEFINE {kind} '{defs_table}'[{name}]: the model has no table '{defs_table}'.")


def execute_query(text: str, tables: dict, measures: dict, *,
                  date_table: Optional[str] = None, date_column: Optional[str] = None,
                  relationships: Optional[list] = None,
                  measure_tables: Optional[dict] = None,
                  model_columns: Optional[dict] = None,
                  culture: Optional[str] = None,
                  date_tables: Optional[dict] = None,
                  column_types: Optional[dict] = None) -> list:
    """Run DAX query ``text`` over a model: a list with one result per
    EVALUATE (see the module's docstring), or DAXParseError,
    DAXUnsupportedError or DAXEvaluationError.

    ``column_types`` maps (table, column) to the model's DataType name
    (String, Int64, Double, Decimal, DateTime, Boolean); a column of a model
    table not in it is typed by its values. The query's own measures live in
    a copy of ``measures`` for the query alone."""
    from pbix_mcp.dax import engine as de

    query = parse_query(text)
    for d in query.definitions:
        if d.kind == 'FUNCTION':
            raise DAXUnsupportedError(
                "DEFINE FUNCTION",
                f"DEFINE FUNCTION ({d.name}) is not supported by this engine's query route yet.")
    for ev in query.evaluates:
        if ev.start_at:
            raise DAXUnsupportedError(
                "START AT", "START AT is not supported by this engine's query route yet.")
    if not query.evaluates:
        raise DAXParseError("The query has no EVALUATE statement, so it returns no table.")
    _resolve_names(query, tables, measures, model_columns, measure_tables)

    # The query's measures: the model's, each DEFINE MEASURE replacing the
    # model's measure of its name (names compare case-insensitively).
    qm = dict(measures)
    qt = dict(measure_tables or {})
    lower = {m.lower(): m for m in qm}
    defined: dict = {}
    for d in query.definitions:
        key = (d.kind if d.kind in ('VAR', 'TABLE') else 'MEASURE/COLUMN', d.name.lower())
        if d.kind == 'COLUMN':
            key = ('COLUMN', (d.table or '').lower(), d.name.lower())
        if key in defined:
            raise DAXEvaluationError(f"DEFINE {d.kind} {d.name} is defined more than once.")
        defined[key] = d
        if d.kind in ('MEASURE', 'COLUMN'):
            _qualified_measure(d.table, d.name, tables, d.kind)
        if d.kind != 'MEASURE':
            continue
        old = lower.get(d.name.lower())
        if old is not None and old != d.name:
            qm.pop(old, None)
            qt.pop(old, None)
        qm[d.name] = d.expression
        qt[d.name] = d.table
        lower[d.name.lower()] = d.name
    # each EVALUATE's table, and each DEFINE TABLE's, as a measure of a name
    # no model measure can have
    slots = []
    for n, ev in enumerate(query.evaluates, 1):
        slot = f"\x00EVALUATE {n}"
        qm[slot] = ev.expression
        slots.append(slot)
    for d in query.definitions:
        if d.kind == 'TABLE':
            qm[f"\x00TABLE {d.name}"] = d.expression

    eng = de._engine
    variables = [d for d in query.definitions if d.kind == 'VAR']
    # the expressions that see the DEFINE VARs: the query's own, never a
    # model measure (a model measure's bare name is no query variable)
    own = frozenset(slots) | {d.name for d in query.definitions if d.kind == 'MEASURE'} | {
        f"\x00TABLE {d.name}" for d in query.definitions if d.kind == 'TABLE'}

    def context(tbls: dict):
        """A context of the query over ``tbls``, its DEFINE VARs bound to it:
        query-level constants, evaluated where first used in the query's
        own (unfiltered) context, in any order of definition (Power BI
        Desktop 2.157, 2026-11-12: a MEASURE sees a VAR defined after
        it, and a VAR sees a MEASURE; _LazyVar refuses one that refers to
        itself)."""
        ctx = de.DAXContext(tbls, qm, date_table, date_column, {}, relationships or [])
        ctx.column_types = dict(column_types or {})
        eng._query_measures = own
        ctx.date_tables = date_tables or {}
        ctx.culture = culture
        ctx.measure_tables = qt
        ctx.model_columns = model_columns or {}
        eng._query_vars = {d.name.lower(): de._LazyVar(d.expression, ctx, {}) for d in variables}
        return ctx

    groups, formats = de._calc_registry(measures)
    if groups or formats:
        de.set_calculation_groups(qm, groups, formats)
    prev_vars = eng._query_vars
    try:
        # DEFINE COLUMN and DEFINE TABLE add to the model for this query
        # alone: each makes a new transient mapping (its own filter cache,
        # filled before it is read: _TransientTables' contract), so each
        # sees the definitions before it and the model is never touched.
        cur = tables
        for d in query.definitions:
            if d.kind == 'COLUMN':
                tname = context(cur).model_table(d.table) or d.table
                values = _column_values(eng, de, context(cur), tname, d)
                base = cur[tname]
                nxt = de._TransientTables(cur)
                nxt[tname] = {'columns': list(base['columns']) + [d.name],
                              'rows': [list(r) + [v] for r, v in zip(base['rows'], values)]}
                cur = nxt
            elif d.kind == 'TABLE':
                nxt = de._TransientTables(cur)
                nxt[d.name] = _table_value(eng, de, context(cur), d)
                cur = nxt
        ctx = context(cur)
        return [_run_one(eng, de, ctx, slot, ev, column_types or {}, query.text)
                for slot, ev in zip(slots, query.evaluates)]
    finally:
        eng._query_vars = prev_vars
        eng._query_measures = frozenset()
        if groups or formats:
            de.set_calculation_groups(qm, None, None)


def _column_values(eng, de, ctx, tname: str, d: Definition) -> list:
    """DEFINE COLUMN's values, one per row of model table ``tname`` in its
    storage order: the expression in each row's context, as a calculated
    column's (DEFINE COLUMN 'Sales stage'[c] = 'Sales stage'[Probability] *
    2 is 0.2, 0.4, ... on Desktop 2.157)."""
    what = f"DEFINE COLUMN '{d.table}'[{d.name}]"
    _clear(eng)
    rows = eng._eval_expr("'" + tname.replace("'", "''") + "'", ctx)
    if not isinstance(rows, list) or len(rows) != len(ctx.tables[tname].get('rows') or ()):
        raise DAXEvaluationError(f"{what}: the engine could not read the rows of '{tname}'.")
    out = []
    for r in rows:
        row_ctx = eng._make_row_context(r, ctx, shadow=rows)
        out.append(de._scalarize(eng._resolve_row_result(eng._eval_expr(d.expression, row_ctx), r, row_ctx)))
    if eng.eval_errors:
        raise DAXEvaluationError(_engine_message(next(iter(eng.eval_errors.values()))))
    if eng.unsupported_functions:
        names = ', '.join(sorted(eng.unsupported_functions))
        raise DAXUnsupportedError(names, f"{what} uses unsupported DAX function(s): {names}")
    for v in out:
        if not isinstance(v, _SCALARS):
            raise DAXEvaluationError(f"{what}: a row's value is no value ({v!r}).")
    return out


def _table_value(eng, de, ctx, d: Definition) -> dict:
    """DEFINE TABLE's rows as a model table of the query: its columns keep
    their names and become the new table's (DEFINE TABLE T2 = {1, 2}
    EVALUATE T2 heads T2[Value] on Desktop 2.157)."""
    slot = f"\x00TABLE {d.name}"
    what = f"DEFINE TABLE {d.name}"
    _clear(eng)
    rows = eng.evaluate_measure(slot, ctx, as_table=True)
    _check(eng, slot, what)
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise DAXEvaluationError(f"{what}: the expression is not a table expression.")
    if not rows:
        raise DAXUnsupportedError(
            "DEFINE TABLE", f"{what}: an empty table's columns are not known to this engine yet.")
    cells = [de.DAXEngine._lineage_cells(r, ctx) for r in rows]
    names = [n for (_t, n), _v in cells[0]]
    if len({n.lower() for n in names}) != len(names):
        raise DAXEvaluationError(f"{what}: the table has two columns of one name.")
    return {'columns': names, 'rows': [[v for _lin, v in c] for c in cells]}


def _clear(eng) -> None:
    eng.unsupported_functions.clear()
    eng.unsupported_by_measure.clear()
    eng.eval_errors.clear()
    eng.timed_out.clear()


def _check(eng, slot: str, what: str) -> None:
    """Raise for what the engine recorded while evaluating ``slot``."""
    if slot in eng.timed_out:
        raise DAXEvaluationError(
            f"{what}: evaluation budget exceeded ({eng._max_eval_seconds:.0f}s); "
            "raise PBIX_DAX_MAX_SECONDS to allow longer")
    if slot in eng.eval_errors:
        # the engine's text, as Desktop shows its engine's (no position: the
        # engine doesn't know where in the query it failed)
        raise DAXEvaluationError(_engine_message(eng.eval_errors[slot]))
    hit = eng.unsupported_by_measure.get(slot) or eng.unsupported_functions
    if hit:
        names = ', '.join(sorted(hit))
        raise DAXUnsupportedError(names, f"{what} uses unsupported DAX function(s): {names}")


def _run_one(eng, de, ctx, slot: str, ev: Evaluate, column_types: dict, text: str = '') -> dict:
    _clear(eng)
    rows = eng.evaluate_measure(slot, ctx, as_table=True)
    what = f"EVALUATE {ev.expression[:60]}"
    _check(eng, slot, what)
    if rows is None:
        raise DAXEvaluationError(
            f"{what}: the expression gave no table (an unknown name, or a "
            "construct the engine could not evaluate).")
    if not isinstance(rows, list):
        # Desktop 2.157: EVALUATE 1+1 -> at the EVALUATE keyword
        raise DAXEvaluationError(_query_error(
            text, ev.offset, "The expression specified in the query is not a valid table expression."))
    if any(not isinstance(r, dict) for r in rows):
        raise DAXEvaluationError(f"{what}: the expression gave values the engine cannot read as rows.")

    cells = [de.DAXEngine._lineage_cells(r, ctx) for r in rows]
    heads = [lin for lin, _v in cells[0]] if cells else []
    names = [n for (_t, n) in heads]
    for c in cells:
        if [lin for lin, _v in c] != heads and [n for (_t, n), _v in c] != names:
            # rows that differ only by a column's table (a UNION's later
            # sides lose their lineage) take the first row's headings, as
            # Power BI Desktop 2.157 heads UNION(VALUES(Opp[Rank]),
            # VALUES(Opp[Name])) Opp[Rank] (2026-11-18, #342)
            raise DAXEvaluationError(f"{what}: the engine gave rows of different columns.")
    if len({(t.lower() if t else None, n.lower()) for t, n in heads}) != len(heads):
        raise DAXEvaluationError(f"{what}: the result has two columns of one name.")
    for c in cells:
        for (t, n), v in c:
            if not isinstance(v, _SCALARS):
                # what the engine leaves for a name it could not resolve (a
                # column marker) or a table where a value belongs
                raise DAXEvaluationError(
                    f"{what}: column {f'{t}[{n}]' if t else f'[{n}]'} holds {v!r}, "
                    "which is no value (an unknown name, or a table where a value belongs).")

    if ev.order_by:
        rows, cells = _order(eng, de, ctx, slot, ev, rows, cells, heads, what)

    # the columns as the expression declares them (_schema): an empty
    # result's names, and each new column's static type
    try:
        schema = _schema(eng, de, ctx, ev.expression, _query_var_exprs(eng))
    except Exception:
        schema = None
    if schema is not None and cells and [(t.lower() if t else None, n.lower()) for t, n, _c in schema] != [
            (t.lower() if t else None, n.lower()) for t, n in heads]:
        schema = None                    # the walker read it otherwise: trust the rows
    if not cells and schema is not None:
        heads = [(t, n) for t, n, _c in schema]
    static = {j: c for j, (_t, _n, c) in enumerate(schema or ()) if c is not None}

    columns = []
    for j, (t, n) in enumerate(heads):
        col: dict = {'name': f"{t}[{n}]" if t is not None else f"[{n}]"}
        model_type = column_types.get((t, n)) if t is not None else None
        mapped = _MODEL_TYPES.get(str(model_type).lower()) if model_type is not None else None
        held = _column_type([c[j][1] for c in cells]) if mapped is not None and cells else None
        if held is not None and _KIND_OF_TYPE.get(held) not in (None, _KIND_OF_TYPE.get(mapped)):
            # a model column's heading over values of another kind: a UNION's
            # unified column under its first table's name (Power BI Desktop
            # 2.157, 2026-11-18, #342): UNION(VALUES(Opp[Rank]),
            # VALUES(Opp[Name])) is a String column Opp[Rank],
            # UNION(VALUES(Opp[When]), VALUES(Opp[Rank])) a Double column
            # Opp[When], UNION(VALUES(Opp[Flag]), VALUES(Opp[Rank])) an Int64
            # column Opp[Flag]
            col['type'], col['type_source'] = held, 'values'
        elif mapped is not None:
            col['type'], col['type_source'] = mapped, 'model'
        elif j in static and (t is None or not cells):
            # a model column's code is read off its table's cells: 'values'
            col['type'] = _STATIC_TYPE_NAMES[static[j]]
            col['type_source'] = 'static' if t is None else 'values'
        elif cells:
            col['type'], col['type_source'] = _column_type([c[j][1] for c in cells]), 'values'
        else:
            col['type'], col['type_source'] = 'Variant', 'unknown'
        columns.append(col)
    out: dict = {'columns': columns, 'rows': [[_cell(v) for _lin, v in c] for c in cells]}
    out['columns_known'] = bool(cells) or schema is not None
    return out


# a result column type's kind of value: a model type is kept unless its column holds another kind
_KIND_OF_TYPE = {'Text': 'text', 'Integer': 'number', 'Double': 'number', 'Decimal': 'number', 'Boolean': 'bool',
                 'DateTime': 'date'}

# _static_type's codes -> the result column types
_STATIC_TYPE_NAMES = {'I': 'Integer', 'D': 'Double', 'C': 'Decimal', 'S': 'Text', 'L': 'Boolean',
                      'T': 'DateTime', 'V': 'Variant'}

# table functions whose result has the columns of their table argument (its index)
_SAME_COLUMNS = {'FILTER': 0, 'CALCULATETABLE': 0, 'KEEPFILTERS': 0, 'TOPN': 1, 'SAMPLE': 1,
                 'DISTINCT': 0, 'VALUES': 0, 'ALL': 0, 'ALLNOBLANKROW': 0, 'ALLSELECTED': 0}


def _query_var_exprs(eng) -> dict:
    return {k: v.expr for k, v in (eng._query_vars or {}).items()}


def _ext_code(eng, de, ctx, expr: str):
    """A new column's static type code (DAXEngine._static_type), None when
    unknown. BLANK() alone is an Int64 column (Power BI Desktop 2.157,
    2026-11-12: ROW("x", BLANK()) and SUMMARIZECOLUMNS' extension)."""
    code = eng._static_type(expr, ctx)
    if code in _STATIC_TYPE_NAMES:
        return code
    plan = de._PLAN_CACHE.get(expr.strip()) or de._analyze_expr(expr.strip())
    if plan[0][0] == de._P_FUNC and plan[0][1][0] == 'BLANK' and not plan[0][1][1].strip():
        return 'I'
    return None


def _literal_name(a: str) -> Optional[str]:
    a = a.strip()
    if len(a) >= 2 and a[0] == '"' and a[-1] == '"':
        return a[1:-1].replace('""', '"')
    return None


def _schema(eng, de, ctx, expr: str, qvars: dict, depth: int = 0) -> Optional[list]:
    """The columns table expression ``expr`` declares, read from the
    engine's plan of it -- [(model table or None, name, static type code or
    None)] -- or None for a shape not read here. Power BI Desktop 2.157
    (2026-11-12): an empty result keeps its columns and their types
    (FILTER('Sales stage', FALSE()) is 3 columns, 0 rows; FILTER(ROW("x", 1,
    "t", "a"), FALSE()) [x] Int64 and [t] String; FILTER({1, 2}, FALSE())
    [Value] Int64; SUMMARIZECOLUMNS('Sales stage'[Sales Stage], "m",
    BLANK()) Sales stage[Sales Stage] String and [m] Int64). A model
    column's code is None: its type is the model's."""
    if depth > 24:
        return None
    expr = expr.strip()
    if not expr:
        return None
    plan = de._PLAN_CACHE.get(expr) or de._analyze_expr(expr)
    kind, data = plan[0]
    rec = lambda e: _schema(eng, de, ctx, e, qvars, depth + 1)   # noqa: E731

    def model_columns(tname):
        # typed from the model's own type where the caller passed it
        # (column_types, in _run_one), else from the cells (_col_num_type)
        return [(tname, c, eng._col_num_type(ctx, tname, c)) for c in ctx.tables[tname]['columns']
                if not str(c).startswith('RowNumber-')]

    def column_ref(a):
        ref = eng._parse_column_ref(a.strip())
        if ref is None:
            return None
        tname = ctx.model_table(ref[0])
        tbl = ctx.tables.get(tname) if tname else None
        if not isinstance(tbl, dict):
            return None
        idx = ctx._find_col_idx(tbl.get('columns') or [], ref[1])
        return ((tname, tbl['columns'][idx], eng._col_num_type(ctx, tname, tbl['columns'][idx]))
                if idx >= 0 else None)

    def pairs(args):
        out = []
        for k in range(0, len(args) - 1, 2):
            name = _literal_name(args[k])
            if name is None:
                return None
            out.append((None, name, _ext_code(eng, de, ctx, args[k + 1])))
        return out

    if kind == de._P_PAREN:
        return rec(data)
    if kind == de._P_TABLECTOR:
        elems = [e.strip() for e in eng._split_top_level(data, ',') if e.strip()] if data else []
        if not elems:
            return None
        cols: list = []
        for e in elems:
            parts = (eng._split_top_level(e[1:-1], ',')
                     if e.startswith('(') and e.endswith(')') and eng._wraps_whole(e) else [e])
            for i, part in enumerate(parts):
                if i == len(cols):
                    cols.append(set())
                cols[i].add(eng._static_type(part, ctx))
        out = []
        for i, types in enumerate(cols):
            types.discard(None)
            code = ('S' if 'S' in types and len(types) > 1 else 'D' if types == {'I', 'D'}
                    else next(iter(types)) if len(types) == 1 else None)
            out.append((None, 'Value' if len(cols) == 1 else f'Value{i + 1}', code))
        return out
    if kind in (de._P_MAYBEVAR, de._P_TAIL):
        name = data.strip()
        if name.startswith("'") and name.endswith("'"):
            name = name[1:-1].replace("''", "'")
        if name.lower() in qvars and kind == de._P_MAYBEVAR:
            return rec(qvars[name.lower()])
        tname = ctx.model_table(name)
        if tname is not None and isinstance(ctx.tables.get(tname), dict):
            return model_columns(tname)
        return None
    if kind != de._P_FUNC:
        return None
    fname, args_text = data
    args = eng._split_args(args_text)
    if fname in _SAME_COLUMNS:
        k = _SAME_COLUMNS[fname]
        if len(args) <= k:
            return None
        if fname in ('VALUES', 'DISTINCT', 'ALL', 'ALLNOBLANKROW', 'ALLSELECTED'):
            refs = [column_ref(a) for a in args]
            if all(refs):
                return refs
        return rec(args[k])
    if fname == 'ROW':
        return pairs(args)
    if fname == 'ADDCOLUMNS' and args:
        base, new = rec(args[0]), pairs(args[1:])
        return None if base is None or new is None else base + new
    if fname == 'SELECTCOLUMNS' and args:
        out = []
        k = 1
        while k < len(args):
            name = _literal_name(args[k])
            if name is not None:
                if k + 1 >= len(args):
                    return None
                ref = column_ref(args[k + 1])
                out.append((None, name, (_ext_code(eng, de, ctx, args[k + 1]) if ref is None
                                         else eng._col_num_type(ctx, ref[0], ref[1]))))
                k += 2
            else:
                ref = column_ref(args[k])
                if ref is None:
                    return None
                out.append(ref)
                k += 1
        return out
    if fname == 'SUMMARIZECOLUMNS':
        out = []
        k = 0
        while k < len(args):
            name = _literal_name(args[k])
            if name is not None:
                if k + 1 >= len(args):
                    return None
                out.append((None, name, _ext_code(eng, de, ctx, args[k + 1])))
                k += 2
                continue
            ref = column_ref(args[k])
            if ref is not None:
                out.append(ref)
            k += 1                       # a filter table adds no column
        return out
    if fname in ('CALENDAR', 'CALENDARAUTO'):
        return [(None, 'Date', 'T')]
    return None


def _order(eng, de, ctx, slot, ev, rows, cells, heads, what):
    """The rows in ORDER BY's order: each item evaluated in the row's
    context; rows tied under every item keep the engine's order. A column
    reference must name a column of the result.

    BLANK sorts as its column's zero, just below it (_blank_keys): Power BI
    Desktop 2.157 (2026-11-12) orders {3, BLANK(), 1, 0, -1} ASC as -1,
    BLANK, 0, 1, 3 and DESC as the exact reverse; a date's BLANK sits between
    1899-12-29 and day zero, text's below "", a Boolean's below FALSE. Not
    first, as the engine's TOPN keys put it (de._order_key)."""
    keys: list = [[] for _ in rows]
    by_lineage = {}
    for j, (t, n) in enumerate(heads):
        by_lineage[((t or '').lower(), n.lower())] = j
    for item in ev.order_by:
        ref = de.DAXEngine._parse_column_ref(item.expression)
        if ref is not None:
            tname = ctx.model_table(ref[0])
            j = by_lineage.get(((tname or ref[0]).lower(), ref[1].lower()))
            if j is None:
                raise DAXEvaluationError(
                    f"{what}: ORDER BY {item.expression} names no column of the result.")
            for k, c in enumerate(cells):
                keys[k].append(c[j][1])
            continue
        bare = item.expression.strip()
        if bare.startswith('[') and bare.endswith(']'):
            j = by_lineage.get(('', _unbracket(bare).lower()))
            if j is not None:
                for k, c in enumerate(cells):
                    keys[k].append(c[j][1])
                continue
        _clear(eng)
        try:
            for k, r in enumerate(rows):
                row_ctx = eng._make_row_context(r, ctx, shadow=rows)
                v = eng._resolve_row_result(eng._eval_expr(item.expression, row_ctx), r, row_ctx)
                keys[k].append(de._scalarize(v))
        except Exception as exc:
            raise DAXEvaluationError(f"{what}: ORDER BY {item.expression}: {exc}") from exc
        _check(eng, slot, f"{what}: ORDER BY {item.expression}")
        if eng.eval_errors:
            raise DAXEvaluationError(
                f"{what}: ORDER BY {item.expression}: " + next(iter(eng.eval_errors.values())))
    order = list(range(len(rows)))
    for p in range(len(ev.order_by) - 1, -1, -1):
        col = de._meet_key_column(_blank_keys(de, [keys[k][p] for k in range(len(rows))]))
        order.sort(key=col.__getitem__, reverse=ev.order_by[p].descending)
    return [rows[k] for k in order], [cells[k] for k in order]


_DAY_ZERO = _dt.datetime(1899, 12, 30)


def _blank_keys(de, values: list) -> list:
    """Sort keys for one ORDER BY item's values: de._order_key's, with a
    last item that puts a BLANK just below its column's zero (0, day zero,
    "" or FALSE, by the type of the column's other values)."""
    zero = 0
    for v in values:
        if v is None:
            continue
        if isinstance(v, str):
            zero = ''
        elif isinstance(v, bool):
            zero = False
        elif isinstance(v, (_dt.datetime, _dt.date)):
            zero = _DAY_ZERO
        break
    blank = de._order_key(zero) + (0,)
    return [blank if v is None else de._order_key(v) + (1,) for v in values]


def _unbracket(s: str) -> str:
    return s.strip()[1:-1].replace(']]', ']')
