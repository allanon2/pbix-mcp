"""Tier 3 of the library: local files -- File.Contents, Csv.Document,
Excel.Workbook and Parquet.Document.

A file is read only through the ``files`` callable the caller gives
``evaluate`` (a refresh's path mapping; normalising its paths, ``..`` included,
is the caller's job): with none, or for a path it doesn't map, File.Contents
raises ``UnmappedSource``. That isn't an M error -- ``try`` doesn't catch it
and a column generator doesn't turn it into an error cell -- so a refresh
always reports the table as unmapped rather than loading a fallback or nulls.

Rules follow Microsoft's function reference. Where it is silent the reader
refuses (an error naming what isn't known yet), so a refresh never stores a
guess; each such rule is settled by a Desktop refresh of a purpose-built source
(ADR-0013's D3). Excel.Workbook (openpyxl) and Parquet.Document (pyarrow) need
the ``pbix-mcp[sources]`` extra.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import posixpath
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from functools import partial
from typing import Any, Callable, Optional

from .values import ANY, Function, LazyTable, MError, MType, Record, Table, force, kind_of

# Enumerations, from Microsoft's reference pages.
QUOTE_NONE, QUOTE_CSV = 0, 1
CSV_QUOTE_AFTER_DELIMITER, CSV_QUOTE_ALWAYS = 0, 1
EXTRA_LIST, EXTRA_ERROR, EXTRA_IGNORE = 0, 1, 2
ENCODINGS = {65001: "utf-8", 1252: "cp1252", 1200: "utf-16-le", 1201: "utf-16-be", 20127: "ascii"}


class UnmappedSource(Exception):
    """A file the refresh's mapping doesn't cover (ADR-0013): the table keeps its saved data. Deliberately not an
    MError, so no M construct can absorb it."""

    def __init__(self, path: str):
        super().__init__(f"No local file is mapped for '{path}'.")
        self.path = path
        self.reason, self.message = "DataSource.NotFound", str(self)


def _error(message: str, reason: str = "Expression.Error") -> MError:
    return MError(reason, message)


def file_contents(evaluator: Any) -> Function:
    def call(path: Any, options: Any = None) -> bytes:
        if not isinstance(path, str):
            raise _error(f"File.Contents needs a path, not {kind_of(path)}.")
        if options is not None:
            raise _error("File.Contents' options are for internal use (Microsoft's reference) and aren't supported.")
        files: Optional[Callable[[str], bytes]] = getattr(evaluator, "files", None)
        if files is None:
            raise UnmappedSource(path)
        return files(path)
    return Function("File.Contents", call, 1, 2)


# --- Csv.Document ----------------------------------------------------------------

def _csv_document(source: Any, columns: Any = None, delimiter: Any = None, extra: Any = None,
                  encoding: Any = None) -> Table:
    quote: Any = QUOTE_CSV
    style: Any = CSV_QUOTE_AFTER_DELIMITER
    if isinstance(columns, Record):
        if delimiter is not None or extra is not None or encoding is not None:
            raise _error("Csv.Document: an options record applies only when delimiter, extraValues and encoding are "
                         "null (Microsoft's reference).")
        options = columns
        known = {"Delimiter", "Columns", "Encoding", "CsvStyle", "QuoteStyle", "ExtraValues", "IncludeByteOrderMark"}
        if any(k not in known for k in options.fields):
            raise _error(f"Csv.Document: unknown options {sorted(set(options.fields) - known)}.")
        get = lambda k: options[k] if k in options else None  # noqa: E731
        columns, delimiter, encoding, extra = get("Columns"), get("Delimiter"), get("Encoding"), get("ExtraValues")
        quote = QUOTE_CSV if get("QuoteStyle") is None else get("QuoteStyle")
        style = CSV_QUOTE_AFTER_DELIMITER if get("CsvStyle") is None else get("CsvStyle")
    if quote == QUOTE_NONE:
        raise _error("Csv.Document: QuoteStyle.None isn't supported until Desktop shows how quotes inside a row read.")
    if quote != QUOTE_CSV or style not in (CSV_QUOTE_AFTER_DELIMITER, CSV_QUOTE_ALWAYS):
        raise _error("Csv.Document: unknown QuoteStyle or CsvStyle.")
    if style == CSV_QUOTE_ALWAYS:
        raise _error("Csv.Document: CsvStyle.QuoteAlways isn't supported yet.")
    if delimiter is None:
        delimiter = ","
    if not (isinstance(delimiter, str) and len(delimiter) == 1):
        raise _error("Csv.Document: only a single-character delimiter is supported yet.")
    if extra is not None and not (type(extra) is int and extra in (EXTRA_ERROR, EXTRA_IGNORE)):
        raise _error("Csv.Document: ExtraValues.List isn't supported yet.")
    text = _decode(source, 65001 if encoding is None else encoding)
    # strict: an unterminated quote, or text after a closing quote, is an error -- the reference doesn't say what
    # either reads as.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"', doublequote=True,
                        strict=True)
    rows: list[list[str]] = []
    try:
        for r in reader:
            if not r:
                raise _error(f"Csv.Document: line {reader.line_num} is blank; how Desktop reads a blank line isn't "
                             "known yet.", "DataFormat.Error")
            rows.append(list(r))
    except csv.Error as e:
        raise _error(f"Csv.Document: {e} (line {reader.line_num})", "DataFormat.Error") from None
    names = _csv_columns(columns, rows)
    width = len(names)
    out = []
    for r in rows:
        if len(r) > width and extra == EXTRA_ERROR:
            raise _error("Csv.Document: a row has more values than columns (ExtraValues.Error).", "DataFormat.Error")
        # A short row: the reference's text says null, its example shows "" -- refused until measured.
        if len(r) < width:
            raise _error("Csv.Document: a row has fewer values than columns; what Desktop fills them with isn't "
                         "known yet.", "DataFormat.Error")
        out.append(r[:width])
    return Table(names, out, [ANY] * width)


def _csv_columns(columns: Any, rows: list[list[str]]) -> list[str]:
    if columns is None:
        widths = {len(r) for r in rows}
        if len(widths) > 1:
            raise _error("Csv.Document: rows have different lengths and no Columns is given; how Desktop counts "
                         "them isn't known yet.", "DataFormat.Error")
        return [f"Column{i + 1}" for i in range(widths.pop() if widths else 0)]
    if type(columns) is int and columns >= 0:
        return [f"Column{i + 1}" for i in range(columns)]
    if isinstance(columns, list) and all(isinstance(force(c), str) for c in columns):
        return [str(force(c)) for c in columns]
    if isinstance(columns, MType):
        raise _error("Csv.Document: Columns as a table type isn't supported yet (whether its types convert isn't "
                     "known).")
    raise _error("Csv.Document: Columns must be a count or a list of names.")


def _decode(source: Any, encoding: Any) -> str:
    if isinstance(source, str):
        return source
    if not isinstance(source, bytes):
        raise _error(f"Csv.Document needs text or binary, not {kind_of(source)}.")
    if type(encoding) is not int or encoding not in ENCODINGS:
        raise _error(f"The text encoding {encoding!r} isn't supported yet.")
    name = ENCODINGS[encoding]
    bom = {"utf-8": b"\xef\xbb\xbf", "utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}.get(name)
    if bom and source.startswith(bom):
        source = source[len(bom):]
    try:
        return str(source.decode(name))
    except UnicodeDecodeError:
        raise _error(f"The file isn't valid {name}.", "DataFormat.Error") from None


# --- Excel.Workbook ---------------------------------------------------------------

_NAV = ["Name", "Data", "Item", "Kind", "Hidden"]
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
_SHEETS: "OrderedDict[tuple[str, str, str], Table]" = OrderedDict()   # parsed sheets and tables, by content
_SHEETS_KEPT = 64
_SHEETS_LOCK = threading.Lock()


def _excel_workbook(workbook: Any, use_headers: Any = None, delay_types: Any = None) -> Table:
    """The workbook's sheets and tables as a navigation table (Name, Data, Item, Kind, Hidden): each sheet's cells
    over its stored dimension (the reference: "the dimensions metadata from the file" unless InferSheetDimensions),
    each table's body under its header names; cached values. A Data table is read when first used, and kept by the
    workbook's content (M values never change)."""
    if isinstance(use_headers, Record):
        options = use_headers
        if any(k not in ("UseHeaders", "DelayTypes", "InferSheetDimensions") for k in options.fields):
            raise _error("Excel.Workbook: unknown options.")
        if "InferSheetDimensions" in options and options["InferSheetDimensions"]:
            raise _error("Excel.Workbook: InferSheetDimensions isn't supported yet.")
        use_headers = options["UseHeaders"] if "UseHeaders" in options else None
        delay_types = options["DelayTypes"] if "DelayTypes" in options else None
    if use_headers not in (None, False):
        raise _error("Excel.Workbook: useHeaders converts numbers and dates by culture (Microsoft's reference); it "
                     "isn't supported yet. Use Table.PromoteHeaders.")
    if not delay_types:
        raise _error("Excel.Workbook: column types without delayTypes aren't supported yet (Desktop writes true).")
    if not isinstance(workbook, bytes):
        raise _error(f"Excel.Workbook needs binary, not {kind_of(workbook)}.")
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise _error("Excel.Workbook needs pbix-mcp's sources extra (openpyxl).") from None
    digest = hashlib.sha256(workbook).hexdigest()
    sheets, tables = _workbook_parts(workbook)
    rows: list[list[Any]] = []
    for name, state in sheets:
        rows.append([name, LazyTable(_cached(digest, "sheet", name, partial(_sheet, workbook, name))), name,
                     "Sheet", state != "visible"])
        for t in tables.get(name, []):
            rows.append([t["name"], LazyTable(_cached(digest, "table", t["name"], partial(_table, workbook, name, t))),
                         t["name"], "Table", False])
    return Table(list(_NAV), rows, [ANY] * len(_NAV))


def _cached(digest: str, kind: str, name: str, load: Callable[[], Table]) -> Callable[[], Table]:
    def get() -> Table:
        key = (digest, kind, name)
        with _SHEETS_LOCK:
            hit = _SHEETS.get(key)
            if hit is not None:
                _SHEETS.move_to_end(key)
                return hit
        table = load()
        with _SHEETS_LOCK:
            _SHEETS[key] = table
            while len(_SHEETS) > _SHEETS_KEPT:
                _SHEETS.popitem(last=False)
        return table
    return get


def _workbook_parts(workbook: bytes) -> tuple[list[tuple[str, str]], dict[str, list[dict]]]:
    """The sheets (name, state) in workbook order, and each sheet's tables (name, ref, header and totals rows,
    column names), read from the package's own parts."""
    try:
        z = zipfile.ZipFile(io.BytesIO(workbook))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = _rels(z, "xl/workbook.xml")
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise _error(f"The file isn't an Excel workbook (.xlsx): {e}", "DataFormat.Error") from None
    sheets, tables = [], {}
    for s in wb.iterfind("m:sheets/m:sheet", _NS):
        name, state = s.get("name") or "", s.get("state") or "visible"
        sheets.append((name, state))
        target = rels.get(s.get(f"{{{_NS['r']}}}id") or "")
        if not target:
            continue
        found = []
        for rid, part in _rels(z, target).items():
            if not part.startswith("xl/tables/"):
                continue
            t = ET.fromstring(z.read(part))
            found.append({"name": t.get("displayName") or t.get("name"), "ref": t.get("ref"),
                          "header": int(t.get("headerRowCount", "1")), "totals": int(t.get("totalsRowCount", "0")),
                          "columns": [c.get("name") for c in t.iterfind("m:tableColumns/m:tableColumn", _NS)]})
        tables[name] = found
    return sheets, tables


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """A part's relationships: id -> the target part's path in the package."""
    folder, file = posixpath.split(part)
    path = posixpath.join(folder, "_rels", file + ".rels")
    if path not in z.namelist():
        return {}
    out = {}
    for r in ET.fromstring(z.read(path)).iterfind("rel:Relationship", _NS):
        target = r.get("Target") or ""
        out[r.get("Id") or ""] = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
            posixpath.join(folder, target))
    return out


def _cells(workbook: bytes, sheet: str, bounds: Optional[tuple[int, int, int, int]]) -> list[list[Any]]:
    """A sheet's values over ``bounds`` (min_col, min_row, max_col, max_row; None: its stored dimension), every
    position present (missing cells and rows are null). A formula is read as its cached value; one without a
    cached value isn't settled by the reference and is refused."""
    import openpyxl
    from openpyxl.utils.cell import range_boundaries
    wb = openpyxl.load_workbook(io.BytesIO(workbook), read_only=True, data_only=False, keep_links=False)
    try:
        ws = wb[sheet]
        if bounds is None:
            try:
                bounds = range_boundaries(ws.calculate_dimension(force=False))
            except ValueError:
                raise _error(f"Excel.Workbook: sheet {sheet!r} stores no dimension; reading it is "
                             "InferSheetDimensions' job, not supported yet.") from None
        min_col, min_row, max_col, max_row = bounds
        width = max_col - min_col + 1
        grid: dict[int, list[Any]] = {}
        formulas: list[tuple[int, int]] = []
        for r in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
            for c in r:
                row = getattr(c, "row", None)
                if row is None:
                    continue
                line = grid.setdefault(row, [None] * width)
                if getattr(c, "data_type", None) == "f":
                    formulas.append((row, c.column))
                else:
                    line[c.column - min_col] = _cell(c)
    finally:
        wb.close()
    if formulas:
        wb = openpyxl.load_workbook(io.BytesIO(workbook), read_only=True, data_only=True, keep_links=False)
        try:
            ws = wb[sheet]
            wanted = set(formulas)
            for r in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
                for c in r:
                    if (getattr(c, "row", None), getattr(c, "column", None)) in wanted:
                        if c.value is None:
                            raise _error(f"Excel.Workbook: the formula in {c.coordinate} has no cached value; what "
                                         "Desktop reads then isn't known yet.", "DataFormat.Error")
                        grid[c.row][c.column - min_col] = _cell(c)
        finally:
            wb.close()
    return [grid.get(r, [None] * width) for r in range(min_row, max_row + 1)]


def _sheet(workbook: bytes, sheet: str) -> Table:
    rows = _cells(workbook, sheet, None)
    width = len(rows[0]) if rows else 0
    return Table([f"Column{i + 1}" for i in range(width)], rows, [ANY] * width)


def _table(workbook: bytes, sheet: str, t: dict) -> Table:
    from openpyxl.utils.cell import range_boundaries
    if t["header"] != 1:
        raise _error(f"Excel.Workbook: table {t['name']!r} has no header row; not supported yet.")
    if t["totals"]:
        raise _error(f"Excel.Workbook: table {t['name']!r} has a totals row; whether Desktop reads it isn't known "
                     "yet.")
    min_col, min_row, max_col, max_row = range_boundaries(t["ref"])
    body = _cells(workbook, sheet, (min_col, min_row + 1, max_col, max_row)) if max_row > min_row else []
    return Table(list(t["columns"]), body, [ANY] * len(t["columns"]))


def _cell(c: Any) -> Any:
    """A cell's value: number, text, logical, a date-formatted number as datetime (openpyxl reads the number
    format), empty as null. An error value (#N/A, #DIV/0!, ...) isn't settled by the reference yet."""
    v = getattr(c, "value", None)
    where = getattr(c, "coordinate", "?")
    if getattr(c, "data_type", None) == "e":
        raise _error(f"Excel.Workbook: the cell {where} holds the error {v}; how Desktop reads it isn't known yet.",
                     "DataFormat.Error")
    if isinstance(v, (bool, int, float, str)) or v is None:
        return v
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    if isinstance(v, dt.time):
        return v
    raise _error(f"Excel.Workbook: the cell {where} holds a value of an unknown kind.")


# --- Parquet.Document -------------------------------------------------------------

def _parquet_document(source: Any, options: Any = None) -> Table:
    """A Parquet file's columns. Microsoft's reference has no page for it: only types with one plain M counterpart
    are read (integers within Int64, floating point, text, logical, date, timestamp without a zone); any other is
    refused."""
    if options is not None:
        raise _error("Parquet.Document: options aren't supported yet.")
    if not isinstance(source, bytes):
        raise _error(f"Parquet.Document needs binary, not {kind_of(source)}.")
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        raise _error("Parquet.Document needs pbix-mcp's sources extra (pyarrow).") from None
    try:
        table = pq.read_table(pa.BufferReader(source), use_threads=False)
    except Exception as e:  # noqa: BLE001 - any reader failure is the file's
        raise _error(f"The file isn't a Parquet file pyarrow can read: {e}", "DataFormat.Error") from None
    names, types, columns = [], [], []
    for field, column in zip(table.schema, table.columns):
        t = field.type
        if pa.types.is_integer(t) and not (pa.types.is_unsigned_integer(t) and t.bit_width == 64):
            mtype = MType("number", True, "Int64.Type")
        elif pa.types.is_floating(t):
            mtype = MType("number", True)
        elif pa.types.is_string(t) or pa.types.is_large_string(t):
            mtype = MType("text", True)
        elif pa.types.is_boolean(t):
            mtype = MType("logical", True)
        elif pa.types.is_date(t):
            mtype = MType("date", True)
        elif pa.types.is_timestamp(t) and t.tz is None:
            mtype = MType("datetime", True)
        else:
            raise _error(f"Parquet.Document: column {field.name!r} has type {t}, not supported yet.")
        try:
            values = column.to_pylist()
        except (ValueError, OverflowError, ArithmeticError) as e:
            raise _error(f"Parquet.Document: column {field.name!r} can't be read: {e}", "DataFormat.Error") from None
        names.append(field.name)
        types.append(mtype)
        columns.append(values)
    rows = [list(r) for r in zip(*columns)] if columns else []
    return Table(names, rows, types)


def _fn(name: str, call: Any, lo: int, hi: int) -> Function:
    return Function(name, call, lo, hi)


SOURCES: dict[str, Any] = {
    "File.Contents": file_contents,
    "Csv.Document": _fn("Csv.Document", _csv_document, 1, 5),
    "Excel.Workbook": _fn("Excel.Workbook", _excel_workbook, 1, 3),
    "Parquet.Document": _fn("Parquet.Document", _parquet_document, 1, 2),
    "QuoteStyle.None": QUOTE_NONE,
    "QuoteStyle.Csv": QUOTE_CSV,
    "CsvStyle.QuoteAfterDelimiter": CSV_QUOTE_AFTER_DELIMITER,
    "CsvStyle.QuoteAlways": CSV_QUOTE_ALWAYS,
    "ExtraValues.List": EXTRA_LIST,
    "ExtraValues.Error": EXTRA_ERROR,
    "ExtraValues.Ignore": EXTRA_IGNORE,
    "TextEncoding.Utf8": 65001,
    "TextEncoding.Utf16": 1200,
    "TextEncoding.Unicode": 1200,
    "TextEncoding.BigEndianUnicode": 1201,
    "TextEncoding.Windows": 1252,
    "TextEncoding.Ascii": 20127,
}
