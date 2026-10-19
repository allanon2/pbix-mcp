"""Refresh tables of an open model: re-run their Power Query (M) and give the
DAX engine the new rows, as Desktop's refresh reloads a table.

``refresh_tables(alias, names)`` evaluates each named table's partition M
(``pbix_mcp.m``; other queries it names are evaluated from their own M), loads
every value into its model column's data type, recomputes the table's
calculated columns and every calculated table built on a refreshed table,
then installs the result as the model's data in one step: an evaluation
running meanwhile sees the old tables or the new ones, never a mix.

Nothing is written to the file. A failure anywhere raises ``RefreshError``
naming the table and leaves the model as it was: an M error, a cell error the
model doesn't return as null (``returnErrorValuesAsNull``), a column the
query no longer produces, or a calculated column or table that can't be
recomputed faithfully (never left stale).
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from pbix_mcp.m import MError, MSyntaxError, Table, evaluate
from pbix_mcp.m.load import load_rows

# AMO Column.ExplicitDataType -> pbix_mcp.m.load's names, and the calc-column materializer's.
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
    calculated_tables: list[str] = field(default_factory=list)     # recomputed, in order


def refresh_tables(alias: str, names: Sequence[str]) -> RefreshResult:
    """Refresh ``names`` (tables whose partition is M) of the model open as ``alias``."""
    from pbix_mcp import server

    info = server._ensure_open(alias)
    ctx = server._get_dax_context(alias)
    meta = _Metadata(info)
    tables = dict(ctx["tables"])
    result = RefreshResult()

    cache: dict[str, Any] = {}

    def resolve(name: str) -> Any:
        if name not in cache:
            text = meta.m.get(name)
            if text is None:
                raise MError("Expression.Error", f"The name '{name}' wasn't recognized.")
            cache[name] = evaluate(text, resolve=resolve, culture=meta.culture)
        return cache[name]

    for name in names:
        if name not in meta.partition_m:
            raise RefreshError(name, "it has no M partition to refresh")
        try:
            value = resolve(name)
            if not isinstance(value, Table):
                raise MError("Expression.Error", "The query doesn't return a table.")
            data = meta.data_columns(name)
            rows = load_rows(value, [(src, _LOAD_TYPE.get(t, "string")) for _, src, t in data],
                             errors_as_null=meta.errors_as_null)
        except (MError, MSyntaxError) as e:
            raise RefreshError(name, str(e)) from None
        tables[name] = _with_calculated_columns(server, name, data, rows, meta, tables, ctx["relationships"])
        result.tables[name] = len(rows)

    result.calculated_tables = _recompute_calculated_tables(info, meta, tables, set(names), ctx["relationships"])
    new_ctx = dict(ctx)
    new_ctx["tables"] = tables
    server._dax_cache[alias] = new_ctx      # one assignment: readers see the old context or the new one
    return result


def _with_calculated_columns(server: Any, name: str, data: list[tuple[str, str, int]], rows: list[list[Any]],
                             meta: "_Metadata", tables: dict, relationships: list) -> dict:
    """The table's rows with its calculated columns recomputed, in the column order the engine has."""
    specs = [{"column": c, "expression": e, "declared_type": _CALC_TYPE.get(t)}
             for c, e, t in meta.calculated_columns(name)]
    columns = [{"name": n, "data_type": _CALC_TYPE.get(t, "String")} for n, _, t in data]
    dict_rows = [dict(zip([n for n, _, _ in data], r)) for r in rows]
    if specs:
        try:
            columns, dict_rows, _ = server._materialize_table_calc_columns(
                name, columns, dict_rows, specs, relationships,
                lookup_provider=lambda t: tables.get(t))
        except ValueError as e:
            raise RefreshError(name, f"a calculated column can't be recomputed: {e}") from None
    order = list(tables[name]["columns"]) if name in tables else [c["name"] for c in columns]
    missing = [c for c in order if c not in {x["name"] for x in columns}]
    if missing:
        raise RefreshError(name, f"columns {missing} aren't produced by the refresh")
    return {"columns": order, "rows": [[r.get(c) for c in order] for r in dict_rows]}


def _recompute_calculated_tables(info: dict, meta: "_Metadata", tables: dict, refreshed: set[str],
                                 relationships: list) -> list[str]:
    """Drop and recompute every calculated table that refers, directly or through another one, to a refreshed
    table; refuse when one doesn't come back."""
    stale: list[str] = []
    changed = set(refreshed)
    grew = True
    while grew:
        grew = False
        for t, expr in meta.calculated_tables.items():
            if t not in stale and _dax_table_names(expr, meta.table_names) & changed:
                stale.append(t)
                changed.add(t)
                grew = True
    if not stale:
        return []
    from pbix_mcp import server
    for t in _in_dependency_order(stale, meta):
        tables[t] = _calculated_table(server, t, meta, tables, relationships)
    return stale


def _in_dependency_order(names: list[str], meta: "_Metadata") -> list[str]:
    out: list[str] = []

    def visit(t: str, path: tuple[str, ...]) -> None:
        if t in out:
            return
        if t in path:
            raise RefreshError(t, "calculated tables refer to each other in a cycle")
        for dep in sorted(_dax_table_names(meta.calculated_tables[t], meta.table_names) & set(names)):
            if dep != t:
                visit(dep, path + (t,))
        out.append(t)
    for t in names:
        visit(t, ())
    return out


def _calculated_table(server: Any, name: str, meta: "_Metadata", tables: dict, relationships: list) -> dict:
    """A calculated table from its own DAX expression (pbix-mcp's engine) and its calculated columns (the
    row-context materializer), in the column order the engine had."""
    from pbix_mcp.dax import engine as dax_engine
    from pbix_mcp.dax.calc_tables import _flatten_part_rows

    expr = meta.calculated_tables[name].strip()
    try:
        ctx = dax_engine.DAXContext(tables, {}, None, None, None, relationships)
        result = dax_engine.DAXEngine()._eval_expr(expr, ctx)
        result = _flatten_part_rows(result) if isinstance(result, list) else result
    except Exception as e:  # noqa: BLE001 - any engine failure refuses the refresh
        raise RefreshError(name, f"its expression can't be evaluated: {e}") from None
    if not isinstance(result, list) or any(not isinstance(r, dict) for r in result):
        raise RefreshError(name, "its expression doesn't evaluate to a table")
    own = meta.expression_columns(name)        # (name, source name, AMO type)
    rows = []
    for r in result:
        if "__column__" in r and "__value__" in r:
            values = {_column_key(r["__column__"]): r["__value__"]}
        else:
            values = {_column_key(k): v for k, v in r.items() if not k.startswith("__")}
        row = []
        for col, source, amo in own:
            key = _column_key(source or col)
            if key not in values:
                raise RefreshError(name, f"its expression doesn't produce the column {col!r}")
            row.append(_typed(values[key], amo, name, col))
        rows.append(row)
    data = [(c, c, t) for c, _, t in own]
    return _with_calculated_columns(server, name, data, rows, meta, tables, relationships)


def _column_key(name: str) -> str:
    """A result column's name as a key: without a table prefix and brackets, ignoring case (as DAX does)."""
    n = name.strip()
    if n.endswith("]") and "[" in n:
        n = n[n.rindex("[") + 1:-1]
    return n.lower()


def _typed(value: Any, amo: int, table: str, column: str) -> Any:
    """A calculated table column's value as its declared type stores it."""
    import datetime as dt

    from pbix_mcp.formats.vertipaq_decoder import _oa_date_to_python
    from pbix_mcp.m.library import datetime_to_ole
    if value is None:
        return None
    if amo == 9:
        if isinstance(value, str):
            try:
                value = dt.datetime.fromisoformat(value)
            except ValueError:
                raise RefreshError(table, f"{column}: {value!r} isn't a date") from None
        if isinstance(value, dt.datetime):
            return _oa_date_to_python(datetime_to_ole(value))
        if isinstance(value, dt.date):
            return _oa_date_to_python(datetime_to_ole(dt.datetime(value.year, value.month, value.day)))
    elif amo == 6 and isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer():
        return int(value)
    elif amo in (8, 10) and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    elif amo == 2 and isinstance(value, str):
        return value
    elif amo == 11 and isinstance(value, bool):
        return value
    raise RefreshError(table, f"{column}: a {type(value).__name__} value for a column of type {amo}")


def _dax_table_names(expression: str, known: set[str]) -> set[str]:
    """The model tables a DAX expression names: 'quoted' names and bare words, outside strings and comments,
    that are table names (compared without case, as DAX does)."""
    by_lower = {k.lower(): k for k in known}
    found: set[str] = set()
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
        elif ch == "'":
            j = i + 1
            name = []
            while j < n:
                if expression[j] == "'":
                    if j + 1 < n and expression[j + 1] == "'":
                        name.append("'")
                        j += 2
                        continue
                    break
                name.append(expression[j])
                j += 1
            hit = by_lower.get("".join(name).lower())
            if hit:
                found.add(hit)
            i = j + 1
        elif ch == "[":
            j = expression.find("]", i + 1)
            i = n if j < 0 else j + 1          # a column or measure name, not a table
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and (expression[j].isalnum() or expression[j] in "_."):
                j += 1
            hit = by_lower.get(expression[i:j].lower())
            if hit:
                found.add(hit)
            i = j
        else:
            i += 1
    return found


class _Metadata:
    """What a refresh reads from the model's metadata."""

    def __init__(self, info: dict):
        import os

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
        self.errors_as_null = bool(options.get("returnErrorValuesAsNull", False))
        self.culture: str = (model["Culture"] if "Culture" in keys else None) or "en-US"
        self.table_names = {r["Name"] for r in conn.execute("select Name from [Table]")}
        self.partition_m: dict[str, str] = {}
        self.calculated_tables: dict[str, str] = {}
        for r in conn.execute("select t.Name, p.Type, p.QueryDefinition from Partition p join [Table] t "
                              "on t.ID = p.TableID order by p.ID"):
            if r["Type"] == 4 and r["QueryDefinition"] and r["Name"] not in self.partition_m:
                self.partition_m[r["Name"]] = r["QueryDefinition"]
            elif r["Type"] == 2 and r["QueryDefinition"]:
                self.calculated_tables[r["Name"]] = r["QueryDefinition"]
        has_expressions = conn.execute("select 1 from sqlite_master where name = 'Expression'").fetchone()
        shared = {r["Name"]: r["Expression"] for r in conn.execute("select Name, Expression from Expression")
                  } if has_expressions else {}
        self.m: dict[str, str] = {**shared, **self.partition_m}
        self._columns: dict[str, list[sqlite3.Row]] = {}
        for r in conn.execute("select t.Name as TableName, c.ExplicitName, c.InferredName, c.SourceColumn, c.Type, "
                              "c.ExplicitDataType, c.InferredDataType, c.Expression from [Column] c join [Table] t on t.ID = c.TableID "
                              "order by c.ID"):
            self._columns.setdefault(r["TableName"], []).append(r)

    def data_columns(self, table: str) -> list[tuple[str, str, int]]:
        """(model name, source column in the query, AMO data type) of each data column."""
        return [(r["ExplicitName"] or r["InferredName"], r["SourceColumn"] or r["ExplicitName"], _data_type(r))
                for r in self._columns.get(table, []) if r["Type"] == 1]

    def expression_columns(self, table: str) -> list[tuple[str, str, int]]:
        """A calculated table's columns its expression produces: (name, source name, AMO data type)."""
        return [(r["ExplicitName"] or r["InferredName"], (r["SourceColumn"] or "").strip(), _data_type(r))
                for r in self._columns.get(table, []) if r["Type"] == 4]

    def calculated_columns(self, table: str) -> list[tuple[str, str, int]]:
        return [(r["ExplicitName"], r["Expression"], _data_type(r))
                for r in self._columns.get(table, []) if r["Type"] == 2 and r["Expression"]]


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
