"""Refresh tables of an open model: re-run their Power Query (M) and give the
DAX engine the new rows, as Desktop's refresh reloads a table.

``refresh_tables(alias, names)``:

1. evaluates each named table's M partitions (``pbix_mcp.m``, in the model's
   ``SourceQueryCulture``; another query a partition names is evaluated from
   its own M; several partitions are appended) and loads every value into
   its model column's data type (``returnErrorValuesAsNull`` from the model's
   DataAccessOptions);
2. recomputes everything calculated that depends on a refreshed table: a
   table's calculated columns, and calculated tables, found by the tables
   their DAX names -- through the measures it names, and through the tables
   related to a named one (a VALUES over a dimension gains a blank row from
   its facts) -- in dependency order; each from its own expression in the
   model's culture, each value checked against its declared type;
3. installs the result as the model's tables in one assignment, and keeps it
   as the model's refresh overlay: the engine's context, rebuilt after any
   cache drop, carries the refreshed tables for as long as the model's
   DataModel is the one refreshed (an edit of the model drops them).

Nothing is written to the file: tools that read the file itself
(pbix_get_table_data, a save) see its saved rows. A failure raises
``RefreshError`` naming the table, and the model keeps what it had.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from pbix_mcp.m import MError, MSyntaxError, ScriptNotRun, Table, UnmappedSource, evaluate
from pbix_mcp.m.load import load_rows

# AMO Column data type -> pbix_mcp.m.load's names, and the calc-column materializer's.
_LOAD_TYPE = {2: "string", 6: "int64", 8: "double", 9: "dateTime", 10: "decimal", 11: "boolean"}
_CALC_TYPE = {2: "String", 6: "Int64", 8: "Double", 9: "DateTime", 10: "Decimal", 11: "Boolean"}


class RefreshError(Exception):
    """A refresh that failed; ``table`` is the table it failed on."""

    def __init__(self, table: str, message: str):
        super().__init__(f"{table}: {message}")
        self.table, self.message = table, message


@dataclass
class RefreshResult:
    tables: dict[str, int] = field(default_factory=dict)          # refreshed table -> rows
    calculated_columns: list[str] = field(default_factory=list)    # tables whose calculated columns were recomputed
    calculated_tables: list[str] = field(default_factory=list)     # recomputed, in order
    unmapped: dict[str, str] = field(default_factory=dict)         # table -> a file the mapping doesn't cover
    not_run: dict[str, ScriptNotRun] = field(default_factory=dict)  # table -> a Python step the runner didn't run


def datamodel_fingerprint(info: dict) -> str:
    """The open model's DataModel as it is now (a refresh overlay holds while this is unchanged)."""
    with open(os.path.join(info["work_dir"], "DataModel"), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def refresh_tables(alias: str, names: Sequence[str], *,
                   files: Optional[Callable[[str], bytes]] = None,
                   python: Optional[Callable[[str, dict[str, bytes]], dict[str, bytes]]] = None) -> RefreshResult:
    """Refresh ``names`` (tables whose partitions are M) of the model open as ``alias``. ``files`` reads the files
    the M names (the caller's path mapping, raising UnmappedSource for one it doesn't map): a table whose M needs an
    unmapped file keeps its saved data and is listed in ``unmapped`` (ADR-0013), not failed. ``python`` runs its
    Python.Execute steps (pbix_mcp.m.python): a table whose step it doesn't run (ScriptNotRun) keeps its saved data
    and is listed in ``not_run``."""
    try:
        return _refresh(alias, list(names), files, python)
    except RefreshError:
        raise
    except Exception as e:  # noqa: BLE001 - whatever failed, the model is untouched; say so as a refresh error
        raise RefreshError(", ".join(names) or "(none)", f"{type(e).__name__}: {e}") from e


def _refresh(alias: str, names: list[str], files: Optional[Callable[[str], bytes]],
             python: Optional[Callable[[str, dict[str, bytes]], dict[str, bytes]]] = None) -> RefreshResult:
    from pbix_mcp import server

    info = server._ensure_open(alias)
    ctx = server._get_dax_context(alias)
    fingerprint = datamodel_fingerprint(info)
    meta = _Metadata(info)
    result = RefreshResult()
    work: dict[str, dict] = dict(ctx["tables"])

    # 1. Every named table's data, before anything calculated reads it.
    cache: dict[str, Any] = {}

    def resolve(name: str) -> Any:
        if name not in cache:
            texts = meta.m.get(name)
            if not texts:
                raise MError("Expression.Error", f"The name '{name}' wasn't recognized.")
            cache[name] = _append([evaluate(t, resolve=resolve, culture=meta.m_culture, files=files, python=python)
                                   for t in texts], name)
        return cache[name]

    for name in names:
        if name not in meta.partition_m:
            raise RefreshError(name, "it has no M partition to refresh")
        if name in meta.other_partitions:
            raise RefreshError(name, "it has partitions that aren't M as well")
        data = meta.data_columns(name)
        try:
            value = resolve(name)
            if not isinstance(value, Table):
                raise MError("Expression.Error", "The query doesn't return a table.")
            rows = load_rows(value, [(src, _LOAD_TYPE.get(t, "string")) for _, src, t in data],
                             errors_as_null=meta.errors_as_null)
        except UnmappedSource as e:
            result.unmapped[name] = e.path
            continue
        except ScriptNotRun as e:
            result.not_run[name] = e
            continue
        except (MError, MSyntaxError) as e:
            raise RefreshError(name, str(e)) from None
        rows = [[_stored(v) for v in r] for r in rows]
        work[name] = {"columns": [n for n, _, _ in data], "rows": rows}
        result.tables[name] = len(rows)

    names = [n for n in names if n not in result.unmapped and n not in result.not_run]

    # 2. What's calculated over them, in dependency order.
    nodes = _stale_nodes(meta, ctx, set(names))
    for kind, name in _ordered(nodes, meta, ctx):
        snapshot = dict(work)       # each step evaluates over its own mapping: engine caches key on its identity
        if kind == "columns":
            base = work[name] if name in names else _data_part(name, work[name], meta)
            work[name] = _with_calculated_columns(server, name, base, meta, snapshot, ctx)
            result.calculated_columns.append(name)
        else:
            work[name] = _calculated_table(server, name, meta, snapshot, ctx)
            result.calculated_tables.append(name)

    # Refreshed tables without calculated columns still need their stored column order.
    for name in names:
        if ("columns", name) not in nodes:
            work[name] = _in_order(name, work[name], ctx["tables"].get(name), meta)

    # 3. Install: a fresh mapping, kept as the model's refresh overlay.
    changed = {t: work[t] for t in [*names, *(n for _, n in nodes)]}
    overlay = info.get("refresh_overlay")
    tables = {**(overlay["tables"] if overlay and overlay["datamodel"] == fingerprint else {}), **changed}
    info["refresh_overlay"] = {"datamodel": fingerprint, "tables": tables}
    new_ctx = dict(ctx)
    new_ctx["tables"] = dict(work)
    server._dax_cache[alias] = new_ctx      # one assignment: readers see the old context or the new one
    return result


def _append(tables: list[Any], name: str) -> Any:
    """A table's partitions, appended (column names must agree)."""
    if len(tables) == 1:
        return tables[0]
    for t in tables:
        if not isinstance(t, Table):
            raise MError("Expression.Error", f"A partition of '{name}' doesn't return a table.")
    first = tables[0]
    for t in tables[1:]:
        if sorted(t.columns) != sorted(first.columns):
            raise MError("Expression.Error", f"The partitions of '{name}' return different columns.")
    rows = [r for t in tables for r in ([r[t.columns.index(c)] for c in first.columns] for r in t.rows)]
    return Table(list(first.columns), rows, list(first.types))


def _stored(value: Any) -> Any:
    """A loaded value as the model's decoded data holds it: a datetime with its OLE serial."""
    if isinstance(value, dt.datetime) and not hasattr(value, "oa_serial"):
        from pbix_mcp.formats.vertipaq_decoder import _oa_date_to_python
        from pbix_mcp.m.library import datetime_to_ole
        return _oa_date_to_python(datetime_to_ole(value))
    return value


# --- what depends on what -------------------------------------------------------

def _stale_nodes(meta: "_Metadata", ctx: dict, refreshed: set[str]) -> list[tuple[str, str]]:
    """The calculated things a refresh must recompute: ("columns", table) for a table's calculated columns and
    ("table", name) for a calculated table, whenever what they read changed."""
    reads = _reads(meta, ctx)
    changed = set(refreshed)
    stale: list[tuple[str, str]] = []
    grew = True
    while grew:
        grew = False
        for node, tables in reads.items():
            if node not in stale and tables & changed:
                stale.append(node)
                changed.add(node[1])
                grew = True
    return stale


def _reads(meta: "_Metadata", ctx: dict) -> dict[tuple[str, str], set[str]]:
    measures = ctx.get("measure_defs") or {}
    related = _related(ctx.get("relationships") or [])
    out: dict[tuple[str, str], set[str]] = {}
    for table in meta.tables_with_calculated_columns():
        exprs = [e for _, e, _ in meta.calculated_columns(table)]
        out[("columns", table)] = _closure(exprs, meta, measures, related) | {table}
    for table, expr in meta.calculated_tables.items():
        exprs = [expr] + [e for _, e, _ in meta.calculated_columns(table)]
        out[("table", table)] = _closure(exprs, meta, measures, related) - {table}
    return out


def _closure(exprs: list[str], meta: "_Metadata", measures: dict[str, str], related: dict[str, set[str]]) -> set[str]:
    """The tables some DAX reads: those it names, through the measures it names, and the many-side tables of
    those."""
    tables: set[str] = set()
    seen: set[str] = set()
    todo = list(exprs)
    by_lower = {k.lower(): k for k in measures}
    while todo:
        names, brackets = _dax_names(todo.pop(), meta.table_names)
        tables |= names
        for b in brackets:
            m = by_lower.get(b.lower())
            if m is not None and m not in seen:
                seen.add(m)
                todo.append(measures[m] or "")
    out = set(tables)
    for t in tables:
        out |= related.get(t, set())
    return out


def _related(relationships: list[dict]) -> dict[str, set[str]]:
    """Each one-side table's many-side tables: what a VALUES or ALL over the one side reads besides it (the
    blank row its unmatched keys add)."""
    out: dict[str, set[str]] = {}
    for r in relationships:
        many, one = r.get("FromTable"), r.get("ToTable")
        if many and one and (r.get("FromCardinality") or 2) == 2 and (r.get("ToCardinality") or 1) == 1:
            out.setdefault(one, set()).add(many)
    return out


def _ordered(nodes: list[tuple[str, str]], meta: "_Metadata", ctx: dict) -> list[tuple[str, str]]:
    """``nodes`` so that each comes after the stale nodes whose tables it reads; a cycle is refused."""
    reads = _reads(meta, ctx)
    by_table: dict[str, tuple[str, str]] = {n[1]: n for n in nodes}
    out: list[tuple[str, str]] = []

    def visit(node: tuple[str, str], path: tuple[tuple[str, str], ...]) -> None:
        if node in out:
            return
        if node in path:
            raise RefreshError(node[1], "calculated columns and tables depend on each other in a cycle")
        for t in sorted(reads.get(node, set())):
            dep = by_table.get(t)
            if dep is not None and dep != node:
                visit(dep, path + (node,))
        out.append(node)
    for n in nodes:
        visit(n, ())
    return out


def _dax_names(expression: str, known: set[str]) -> tuple[set[str], list[str]]:
    """The model tables a DAX expression names ('quoted', or a bare word not followed by "(") and the
    [bracketed] names in it, outside strings and comments. Table names compare without case, as DAX does."""
    by_lower = {k.lower(): k for k in known}
    found: set[str] = set()
    brackets: list[str] = []
    i, n = 0, len(expression)
    while i < n:
        ch = expression[i]
        if ch == '"':
            i += 1
            while i < n:
                if expression[i] == '"':
                    if i + 1 < n and expression[i + 1] == '"':
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif expression.startswith("//", i) or expression.startswith("--", i):
            while i < n and expression[i] != "\n":
                i += 1
        elif expression.startswith("/*", i):
            j = expression.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif ch in "'[":
            close = "'" if ch == "'" else "]"
            j = i + 1
            name = []
            while j < n:
                if expression[j] == close:
                    if j + 1 < n and expression[j + 1] == close:   # '' and ]] escape themselves
                        name.append(close)
                        j += 2
                        continue
                    break
                name.append(expression[j])
                j += 1
            text = "".join(name)
            if ch == "'":
                hit = by_lower.get(text.lower())
                if hit:
                    found.add(hit)
            else:
                brackets.append(text)
            i = j + 1
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and (expression[j].isalnum() or expression[j] in "_."):
                j += 1
            k = j
            while k < n and expression[k] in " \t\r\n":
                k += 1
            hit = by_lower.get(expression[i:j].lower())
            if hit and not (k < n and expression[k] == "("):
                found.add(hit)
            i = j
        else:
            i += 1
    return found, brackets


def _dax_table_names(expression: str, known: set[str]) -> set[str]:
    return _dax_names(expression, known)[0]


# --- recomputing ---------------------------------------------------------------

def _data_part(name: str, table: dict, meta: "_Metadata") -> dict:
    """A table's data columns only (its stored calculated columns are about to be recomputed)."""
    cols = [n for n, _, _ in meta.data_columns(name)]
    idx = []
    for c in cols:
        if c not in table["columns"]:
            raise RefreshError(name, f"its data has no column {c!r}")
        idx.append(table["columns"].index(c))
    return {"columns": cols, "rows": [[r[i] for i in idx] for r in table["rows"]]}


def _with_calculated_columns(server: Any, name: str, base: dict, meta: "_Metadata", tables: dict,
                             ctx: dict) -> dict:
    """``base`` (a table's data columns) with its calculated columns recomputed and type-checked, in the column
    order the engine had."""
    specs = [{"column": c, "expression": e, "declared_type": _CALC_TYPE.get(t)}
             for c, e, t in meta.calculated_columns(name)]
    types = {n: t for n, _, t in meta.data_columns(name)}
    columns = [{"name": c, "data_type": _CALC_TYPE.get(types.get(c, 2), "String")} for c in base["columns"]]
    dict_rows = [dict(zip(base["columns"], r)) for r in base["rows"]]
    try:
        columns, dict_rows, _ = server._materialize_table_calc_columns(
            name, columns, dict_rows, specs, ctx["relationships"],
            lookup_provider=lambda t: tables.get(t), culture=ctx.get("culture"))
    except ValueError as e:
        raise RefreshError(name, f"a calculated column can't be recomputed: {e}") from None
    for c, _, amo in meta.calculated_columns(name):
        for r in dict_rows:
            r[c] = _typed(r.get(c), amo, name, c)
    return _in_order(name, {"columns": [c["name"] for c in columns],
                            "rows": [[r.get(c["name"]) for c in columns] for r in dict_rows]},
                     ctx["tables"].get(name), meta)


def _in_order(name: str, table: dict, before: Optional[dict], meta: "_Metadata") -> dict:
    """``table`` in the column order the engine had (or the model's), every column present."""
    order = list(before["columns"]) if before else []
    order += [c for c in meta.column_order(name) if c not in order]
    missing = [c for c in order if c not in table["columns"]]
    if missing:
        raise RefreshError(name, f"columns {missing} aren't produced by the refresh")
    idx = [table["columns"].index(c) for c in order]
    return {"columns": order, "rows": [[r[i] for i in idx] for r in table["rows"]]}


def _calculated_table(server: Any, name: str, meta: "_Metadata", tables: dict, ctx: dict) -> dict:
    """A calculated table from its own DAX expression and its calculated columns."""
    from pbix_mcp.dax.calc_tables import evaluate_calc_table_expression

    got, err = evaluate_calc_table_expression(
        meta.calculated_tables[name], tables, ctx.get("measure_defs") or {}, ctx["relationships"],
        culture=ctx.get("culture"), measure_tables=ctx.get("measure_tables"), model_columns=ctx.get("model_columns"))
    if err:
        raise RefreshError(name, f"its expression can't be recomputed: {err}")
    assert got is not None
    keys = {_column_key(c): i for i, c in enumerate(got["columns"])}
    own = meta.expression_columns(name)
    idx = []
    for col, source, _ in own:
        k = _column_key(source or col)
        if k not in keys:
            raise RefreshError(name, f"its expression doesn't produce the column {col!r}")
        idx.append(keys[k])
    rows = [[_typed(r[i], amo, name, col) for i, (col, _, amo) in zip(idx, own)] for r in got["rows"]]
    base = {"columns": [c for c, _, _ in own], "rows": rows}
    if meta.calculated_columns(name):
        return _with_calculated_columns(server, name, base, meta, tables, ctx)
    return _in_order(name, base, ctx["tables"].get(name), meta)


def _column_key(name: str) -> str:
    """A result column's name as a key: without a table prefix and brackets, ignoring case (as DAX does)."""
    n = name.strip()
    if n.endswith("]") and "[" in n:
        n = n[n.rindex("[") + 1:-1]
    return n.lower()


def _typed(value: Any, amo: int, table: str, column: str) -> Any:
    """A calculated value as its declared type stores it; another type is refused."""
    if value is None:
        return None
    if amo == 9:
        if isinstance(value, str):
            try:
                value = dt.datetime.fromisoformat(value)
            except ValueError:
                raise RefreshError(table, f"{column}: {value!r} isn't a date") from None
        if isinstance(value, dt.datetime):
            return _stored(value)
        if isinstance(value, dt.date):
            return _stored(dt.datetime(value.year, value.month, value.day))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            from pbix_mcp.formats.vertipaq_decoder import _oa_date_to_python
            got = _oa_date_to_python(float(value))     # the engine's date arithmetic yields the OLE serial
            if isinstance(got, dt.datetime):
                return got
    elif amo == 6 and isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer():
        return int(value)
    elif amo == 6 and isinstance(value, dt.datetime):
        # a date difference (BASEDATE - TODAY()) is a datetime in DAX; a whole number column holds its OLE serial
        from pbix_mcp.m.library import datetime_to_ole
        serial = datetime_to_ole(value)
        if float(serial).is_integer():
            return int(serial)
    elif amo in (8, 10) and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    elif amo == 2 and isinstance(value, str):
        return value
    elif amo == 11 and isinstance(value, bool):
        return value
    raise RefreshError(table, f"{column}: a {type(value).__name__} value for a column of type "
                              f"{_CALC_TYPE.get(amo, amo)}")


# --- the model's metadata ------------------------------------------------------

class _Metadata:
    """What a refresh reads from the model's metadata."""

    def __init__(self, info: dict):
        from pbix_mcp.formats.abf_rebuild import read_metadata_sqlite
        from pbix_mcp.formats.datamodel_roundtrip import decompress_datamodel

        with open(os.path.join(info["work_dir"], "DataModel"), "rb") as f:
            meta = read_metadata_sqlite(decompress_datamodel(f.read()))
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.write(meta)
        tmp.close()
        try:
            conn = sqlite3.connect(tmp.name)
            try:
                self._read(conn)
            finally:
                conn.close()
        finally:
            os.unlink(tmp.name)

    def _read(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        model = conn.execute("select * from Model").fetchone()
        keys = model.keys()
        options = json.loads(model["DataAccessOptions"]) if "DataAccessOptions" in keys and model["DataAccessOptions"] else {}
        # Absent, Analysis Services' default: an error cell fails its table.
        self.errors_as_null = bool(options.get("returnErrorValuesAsNull", False))
        # The culture M reads source text in (Desktop's "regional settings" for the file), else the model's.
        self.m_culture: str = ((model["SourceQueryCulture"] if "SourceQueryCulture" in keys else None)
                               or (model["Culture"] if "Culture" in keys else None) or "en-US")
        self.table_names = {r["Name"] for r in conn.execute("select Name from [Table]")}
        self.partition_m: dict[str, list[str]] = {}
        self.other_partitions: set[str] = set()
        self.calculated_tables: dict[str, str] = {}
        for r in conn.execute("select t.Name, p.Type, p.QueryDefinition from Partition p join [Table] t "
                              "on t.ID = p.TableID order by p.ID"):
            if r["Type"] == 4 and r["QueryDefinition"]:
                self.partition_m.setdefault(r["Name"], []).append(r["QueryDefinition"])
            elif r["Type"] == 2 and r["QueryDefinition"]:
                self.calculated_tables[r["Name"]] = r["QueryDefinition"]
            else:
                self.other_partitions.add(r["Name"])
        has_expressions = conn.execute("select 1 from sqlite_master where name = 'Expression'").fetchone()
        shared = {r["Name"]: [r["Expression"]] for r in conn.execute("select Name, Expression from Expression")
                  if r["Expression"]} if has_expressions else {}
        self.m: dict[str, list[str]] = {**shared, **self.partition_m}
        self._columns: dict[str, list[sqlite3.Row]] = {}
        for r in conn.execute("select t.Name as TableName, c.ExplicitName, c.InferredName, c.SourceColumn, c.Type, "
                              "c.ExplicitDataType, c.InferredDataType, c.Expression from [Column] c join [Table] t "
                              "on t.ID = c.TableID order by c.ID"):
            self._columns.setdefault(r["TableName"], []).append(r)

    def data_columns(self, table: str) -> list[tuple[str, str, int]]:
        """(model name, source column in the query, AMO data type) of each data column."""
        out = []
        for r in self._columns.get(table, []):
            if r["Type"] == 1:
                name = r["ExplicitName"] or r["InferredName"]
                out.append((name, r["SourceColumn"] or name, _data_type(r)))
        return out

    def expression_columns(self, table: str) -> list[tuple[str, str, int]]:
        """A calculated table's columns its expression produces: (name, source name, AMO data type)."""
        return [(r["ExplicitName"] or r["InferredName"], (r["SourceColumn"] or "").strip(), _data_type(r))
                for r in self._columns.get(table, []) if r["Type"] == 4]

    def calculated_columns(self, table: str) -> list[tuple[str, str, int]]:
        return [(r["ExplicitName"] or r["InferredName"], r["Expression"], _data_type(r))
                for r in self._columns.get(table, []) if r["Type"] == 2 and r["Expression"]]

    def tables_with_calculated_columns(self) -> list[str]:
        return [t for t in self._columns if t not in self.calculated_tables and self.calculated_columns(t)]

    def column_order(self, table: str) -> list[str]:
        return [r["ExplicitName"] or r["InferredName"] for r in self._columns.get(table, []) if r["Type"] in (1, 2, 4)]


def _data_type(column: sqlite3.Row) -> int:
    """A column's AMO data type: the declared one, or the inferred one when it is declared Automatic (1)."""
    return int(column["InferredDataType"] if column["ExplicitDataType"] == 1 else column["ExplicitDataType"])


def unchanged(alias: str, before: dict, names: Optional[Sequence[str]] = None) -> dict[str, bool]:
    """For a test: whether each table in ``before`` (an earlier context's tables) holds the same rows now,
    compared as multisets."""
    from collections import Counter

    from pbix_mcp import server
    now = server._get_dax_context(alias)["tables"]
    out = {}
    for t in names or before:
        a, b = before.get(t), now.get(t)
        if a is None or b is None or a["columns"] != b["columns"]:
            out[t] = False
            continue
        out[t] = Counter(map(_row_key, a["rows"])) == Counter(map(_row_key, b["rows"]))
    return out


def _row_key(row: list[Any]) -> tuple:
    out: list[Any] = []
    for v in row:
        if hasattr(v, "hour") and hasattr(v, "year"):
            out.append(("datetime", v.year, v.month, v.day, v.hour, v.minute, v.second, v.microsecond))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(round(float(v), 9))
        else:
            out.append(v)
    return tuple(out)
