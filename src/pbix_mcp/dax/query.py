"""DAX queries: ``[DEFINE ...] EVALUATE <table> [ORDER BY ...]``.

A query is split into its statements by a scanner that knows DAX's strings,
quoted names, bracketed names, comments and parentheses; each expression is
handed to the engine as written, never rewritten. Each EVALUATE's table is
evaluated as a measure is (DAXEngine.evaluate_measure with ``as_table``), so
every check, error record, unsupported-function record and time budget of a
measure evaluation applies to it.

Unknown is never silent: a construct this route does not run yet (DEFINE
VAR, TABLE, COLUMN, FUNCTION; START AT) is refused by name
(DAXUnsupportedError), an expression that hit an unsupported function fails
the query naming it, an error fails the query with its message, and an
expression that gives no table fails it too. A result is never an empty
table standing in for one of those.

What comes back per EVALUATE: ``{"columns": [{"name", "type"}], "rows":
[[...]]}``, rows as positional lists in the engine's order (or ORDER BY's).
A column of a model table is named ``Table[Column]``, any other ``[Name]``.
Types are Boolean, DateTime, Text, Decimal, Double, Integer or Variant: a
model column's from the model when the caller passes its column types, any
other column's from its values (``type_source``: ``"model"`` or
``"values"``); a column with no value, or values of several types, is
Variant. An empty result has no row to name its columns from: its
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
            depth -= 1
            if depth < 0:
                raise DAXParseError(f"The query closes a parenthesis it never opened, at {text[i:i + 30]!r}.")
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
        raise DAXParseError("The query opens a parenthesis it never closes.")
    return toks


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


@dataclass
class DaxQuery:
    definitions: list
    evaluates: list


def _is_word(tok: _Tok, *words: str) -> bool:
    return tok.kind == 'word' and tok.depth == 0 and tok.text.upper() in words


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = _scan(text)
        self.i = 0

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

    def expression(self, what: str, stop_order: bool = False) -> str:
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
            raise DAXParseError(f"The query has no expression for {what}.")
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
        if self.at_word('DEFINE'):
            self.i += 1
            if not self.at_word(*_DEFINE_KINDS):
                raise DAXParseError("DEFINE is followed by no definition (MEASURE, VAR, TABLE, COLUMN or FUNCTION).")
            while self.at_word(*_DEFINE_KINDS):
                defs.append(self.definition())
        while self.peek() is not None:
            if self.at_word('DEFINE'):
                raise DAXParseError("DEFINE comes once, before the first EVALUATE.")
            self.expect_word('EVALUATE')
            ev = Evaluate(self.expression('EVALUATE', stop_order=True))
            if self.at_order_by():
                self.i += 2
                for toks in self.comma_list('ORDER BY'):
                    desc = False
                    last = toks[-1]
                    if len(toks) > 1 and _is_word(last, 'ASC', 'DESC'):
                        desc = last.text.upper() == 'DESC'
                        toks = toks[:-1]
                    ev.order_by.append(OrderItem(self.text[toks[0].start:toks[-1].end].strip(), desc))
            if self.at_start_at():
                self.i += 2
                ev.start_at = [self.text[ts[0].start:ts[-1].end].strip()
                               for ts in self.comma_list('START AT')]
            evals.append(ev)
            t = self.peek()
            if t is not None and not _is_word(t, 'EVALUATE', 'DEFINE'):
                raise DAXParseError(f"The query has {t.text!r} where a statement (EVALUATE) belongs.")
        return DaxQuery(defs, evals)

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
                self.i += 2
            else:
                raise DAXParseError(f"DEFINE {kind} names a table and a name, as 'Table'[Name].")
        else:
            t = self.peek()
            if t is None or t.kind not in ('word', 'qname', 'bname'):
                raise DAXParseError(f"DEFINE {kind} is followed by no name.")
            name = _unquote_name(t)
            self.i += 1
        t = self.peek()
        if t is None or not (t.kind == 'punct' and t.text == '='):
            raise DAXParseError(f"DEFINE {kind} {name} has no '=' before its expression.")
        self.i += 1
        return Definition(kind, table, name, self.expression(f"DEFINE {kind} {name}"))


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


def _qualified_measure(defs_table: str, name: str, ctx_tables: dict) -> None:
    if defs_table not in ctx_tables:
        raise DAXEvaluationError(
            f"DEFINE MEASURE '{defs_table}'[{name}]: the model has no table '{defs_table}'.")


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
        if d.kind != 'MEASURE':
            raise DAXUnsupportedError(
                f"DEFINE {d.kind}",
                f"DEFINE {d.kind} ({d.name}) is not supported by this engine's query route yet.")
    for ev in query.evaluates:
        if ev.start_at:
            raise DAXUnsupportedError(
                "START AT", "START AT is not supported by this engine's query route yet.")
    if not query.evaluates:
        raise DAXParseError("The query has no EVALUATE statement, so it returns no table.")

    # The query's measures: the model's, each DEFINE MEASURE replacing the
    # model's measure of its name (names compare case-insensitively).
    qm = dict(measures)
    qt = dict(measure_tables or {})
    lower = {m.lower(): m for m in qm}
    defined: set = set()
    for d in query.definitions:
        _qualified_measure(d.table, d.name, tables)
        key = d.name.lower()
        if key in defined:
            raise DAXEvaluationError(f"DEFINE MEASURE {d.name} is defined more than once.")
        defined.add(key)
        old = lower.get(key)
        if old is not None and old != d.name:
            qm.pop(old, None)
            qt.pop(old, None)
        qm[d.name] = d.expression
        qt[d.name] = d.table
        lower[key] = d.name
    # each EVALUATE's table, as a measure of a name no model measure can have
    slots = []
    for n, ev in enumerate(query.evaluates, 1):
        slot = f"\x00EVALUATE {n}"
        qm[slot] = ev.expression
        slots.append(slot)

    groups, formats = de._calc_registry(measures)
    if groups or formats:
        de.set_calculation_groups(qm, groups, formats)
    try:
        ctx = de.DAXContext(tables, qm, date_table, date_column, {}, relationships or [])
        ctx.date_tables = date_tables or {}
        ctx.culture = culture
        ctx.measure_tables = qt
        ctx.model_columns = model_columns or {}
        eng = de._engine
        return [_run_one(eng, de, ctx, slot, ev, column_types or {})
                for slot, ev in zip(slots, query.evaluates)]
    finally:
        if groups or formats:
            de.set_calculation_groups(qm, None, None)


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
        raise DAXEvaluationError(f"{what}: {eng.eval_errors[slot]}")
    hit = eng.unsupported_by_measure.get(slot) or eng.unsupported_functions
    if hit:
        names = ', '.join(sorted(hit))
        raise DAXUnsupportedError(names, f"{what} uses unsupported DAX function(s): {names}")


def _run_one(eng, de, ctx, slot: str, ev: Evaluate, column_types: dict) -> dict:
    _clear(eng)
    rows = eng.evaluate_measure(slot, ctx, as_table=True)
    what = f"EVALUATE {ev.expression[:60]}"
    _check(eng, slot, what)
    if rows is None:
        raise DAXEvaluationError(
            f"{what}: the expression gave no table (an unknown name, or a "
            "construct the engine could not evaluate).")
    if not isinstance(rows, list):
        raise DAXEvaluationError(f"{what}: the expression is not a table expression.")
    if any(not isinstance(r, dict) for r in rows):
        raise DAXEvaluationError(f"{what}: the expression gave values the engine cannot read as rows.")

    cells = [de.DAXEngine._lineage_cells(r, ctx) for r in rows]
    heads = [lin for lin, _v in cells[0]] if cells else []
    for c in cells:
        if [lin for lin, _v in c] != heads:
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

    columns = []
    for j, (t, n) in enumerate(heads):
        col: dict = {'name': f"{t}[{n}]" if t is not None else f"[{n}]"}
        model_type = column_types.get((t, n)) if t is not None else None
        mapped = _MODEL_TYPES.get(str(model_type).lower()) if model_type is not None else None
        if mapped is not None:
            col['type'], col['type_source'] = mapped, 'model'
        else:
            col['type'], col['type_source'] = _column_type([c[j][1] for c in cells]), 'values'
        columns.append(col)
    out: dict = {'columns': columns, 'rows': [[_cell(v) for _lin, v in c] for c in cells]}
    out['columns_known'] = bool(cells)
    return out


def _order(eng, de, ctx, slot, ev, rows, cells, heads, what):
    """The rows in ORDER BY's order: each item evaluated in the row's
    context, BLANK first ascending (the engine's TOPN order, de._order_key);
    rows tied under every item keep the engine's order. A column reference
    must name a column of the result."""
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
        col = de._meet_key_column([de._order_key(keys[k][p]) for k in range(len(rows))])
        order.sort(key=col.__getitem__, reverse=ev.order_by[p].descending)
    return [rows[k] for k in order], [cells[k] for k in order]


def _unbracket(s: str) -> str:
    return s.strip()[1:-1].replace(']]', ']')
