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

import datetime as dt
import hashlib
import io
import posixpath
import re
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from decimal import Decimal
from functools import partial
from typing import Any, Callable, Optional

from .values import ANY, CellError, Function, LazyTable, MError, MType, Record, Table, force, kind_of

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
    if quote not in (QUOTE_NONE, QUOTE_CSV) or style not in (CSV_QUOTE_AFTER_DELIMITER, CSV_QUOTE_ALWAYS):
        raise _error("Csv.Document: unknown QuoteStyle or CsvStyle.")
    if style == CSV_QUOTE_ALWAYS:
        raise _error("Csv.Document: CsvStyle.QuoteAlways isn't supported yet.")
    if delimiter is None:
        delimiter = ","
    if not (isinstance(delimiter, str) and len(delimiter) == 1):
        raise _error("Csv.Document: only a single-character delimiter is supported yet.")
    if extra is not None and not (type(extra) is int and extra in (EXTRA_LIST, EXTRA_ERROR, EXTRA_IGNORE)):
        raise _error("Csv.Document: unknown ExtraValues.")
    text = _decode(source, 65001 if encoding is None else encoding)
    rows = _csv_records(text, delimiter, quote == QUOTE_CSV)
    names = _csv_columns(columns, rows)
    width = len(names)
    # A Desktop refresh pads a short row with "" and drops a long row's extra values, whatever ExtraValues says
    # (#104's D3 probes: Error and List read like Ignore, with Columns given or not).
    out = [(r + [""] * (width - len(r)))[:width] for r in rows]
    return Table(names, out, [ANY] * width)


def _csv_records(text: str, delimiter: str, quoted_breaks: bool) -> list[list[str]]:
    """Records as a Desktop refresh reads them (#104's D3 probes). A quote opens a quoted value only at the start
    of a value (' "x" ' is read as written); inside it "" is a quote; text after the closing quote joins the value
    ('"x"y' is xy). With QuoteStyle.Csv a line break inside quotes is part of the value, and a quote never closed
    runs to the end of the text, line breaks and all; with QuoteStyle.None every line break ends the record,
    closing the quote. CR LF, CR and LF each end a record; a blank line is a record of one empty value; a line
    break at the very end doesn't start another."""
    records: list[list[str]] = []
    fields: list[str] = []
    value: list[str] = []
    i, n = 0, len(text)
    started = False                          # anything read since the last record ended
    while i < n:
        c = text[i]
        if c == '"' and not value:
            # A quoted value.
            i += 1
            started = True
            while i < n:
                c = text[i]
                if c == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        value.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                if c in "\r\n" and not quoted_breaks:
                    break
                value.append(c)
                i += 1
            continue
        if c == delimiter:
            fields.append("".join(value))
            value = []
            started = True
            i += 1
            continue
        if c in "\r\n":
            fields.append("".join(value))
            records.append(fields)
            fields, value, started = [], [], False
            i += 2 if c == "\r" and i + 1 < n and text[i + 1] == "\n" else 1
            continue
        value.append(c)
        started = True
        i += 1
    if started or value or fields:
        fields.append("".join(value))
        records.append(fields)
    return records


def _csv_columns(columns: Any, rows: list[list[str]]) -> list[str]:
    """Columns: a count, names, or (none given) the first record's width, as a Desktop refresh counts them."""
    if columns is None:
        return [f"Column{i + 1}" for i in range(len(rows[0]) if rows else 0)]
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
    # A byte order mark wins over the encoding given: a Desktop refresh reads a UTF-16 file given 65001, and a
    # UTF-8 one given 1252, by their marks (#104's D3 probes).
    for bom, by in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if source.startswith(bom):
            source, name = source[len(bom):], by
            break
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
    """The workbook's sheets, then its tables, as a navigation table (Name, Data, Item, Kind, Hidden), in the
    order a Desktop refresh lists them (#104's D3 probes). A sheet's cells span its stored dimension's columns
    (the reference: "the dimensions metadata from the file") and its rows down to the last row storing a cell,
    or, with InferSheetDimensions, the cells holding values; a table's rows are its body, a totals row included.
    Each cell is the value stored in the file, a formula's cached one: a date-formatted cell is its number, as a
    Desktop refresh reads it; an error value (#DIV/0!) is a failed cell. With useHeaders the first row becomes the
    column names, its values unconverted. A Data table is read when first used, and kept by the workbook's
    content (M values never change)."""
    infer = False
    if isinstance(use_headers, Record):
        options = use_headers
        if any(k not in ("UseHeaders", "DelayTypes", "InferSheetDimensions") for k in options.fields):
            raise _error("Excel.Workbook: unknown options.")
        infer = bool(options["InferSheetDimensions"]) if "InferSheetDimensions" in options else False
        use_headers = options["UseHeaders"] if "UseHeaders" in options else None
        delay_types = options["DelayTypes"] if "DelayTypes" in options else None
    if use_headers not in (None, False, True):
        raise _error("Excel.Workbook: useHeaders must be a logical.")
    if not delay_types:
        raise _error("Excel.Workbook: column types without delayTypes aren't supported yet (Desktop writes true).")
    if not isinstance(workbook, bytes):
        raise _error(f"Excel.Workbook needs binary, not {kind_of(workbook)}.")
    digest = hashlib.sha256(workbook).hexdigest()
    sheets, tables = _workbook_parts(workbook)
    headers = bool(use_headers)
    rows: list[list[Any]] = []
    for name, state, part in sheets:
        load = partial(_sheet, workbook, part, infer, headers)
        rows.append([name, LazyTable(_cached(digest, f"sheet:{infer}:{headers}", name, load)), name, "Sheet",
                     state != "visible"])
    for name, _, part in sheets:
        for t in tables.get(name, []):
            load = partial(_table, workbook, part, t)
            rows.append([t["name"], LazyTable(_cached(digest, "table", t["name"], load)), t["name"], "Table", False])
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


def _workbook_parts(workbook: bytes) -> tuple[list[tuple[str, str, str]], dict[str, list[dict]]]:
    """The sheets (name, state, part) in workbook order, and each sheet's tables (name, ref, header and totals
    rows, column names) in the sheet's relationship order, read from the package's own parts."""
    try:
        z = zipfile.ZipFile(io.BytesIO(workbook))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = _rels(z, "xl/workbook.xml")
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise _error(f"The file isn't an Excel workbook (.xlsx): {e}", "DataFormat.Error") from None
    sheets, tables = [], {}
    for s in wb.iterfind("m:sheets/m:sheet", _NS):
        name, state = s.get("name") or "", s.get("state") or "visible"
        target = rels.get(s.get(f"{{{_NS['r']}}}id") or "")
        if not target:
            continue
        sheets.append((name, state, target))
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


_REF = re.compile(r"^\$?([A-Z]{1,3})\$?(\d+)$")


def _cell_ref(ref: str) -> tuple[int, int]:
    """(column, row), 1-based, of an A1 reference."""
    m = _REF.match(ref.upper())
    if not m:
        raise _error(f"Excel.Workbook: the cell reference {ref!r} isn't valid.", "DataFormat.Error")
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return col, int(m.group(2))


def _range(ref: str) -> tuple[int, int, int, int]:
    """(min col, min row, max col, max row) of an A1 range or single cell."""
    first, _, last = ref.partition(":")
    c1, r1 = _cell_ref(first)
    c2, r2 = _cell_ref(last or first)
    return min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2)


def _text_of(node: Optional[ET.Element]) -> Optional[str]:
    """A string item's text: its own text or its runs' text (phonetic runs left out); None when it holds none."""
    if node is None:
        return None
    parts = []
    for child in node:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "t":
            parts.append(child.text or "")
        elif tag == "r":
            t = child.find("m:t", _NS)
            parts.append(t.text or "" if t is not None else "")
    return "".join(parts) if parts else None


def _shared_strings(z: zipfile.ZipFile) -> list[Optional[str]]:
    rels = _rels(z, "xl/workbook.xml")
    path = next((p for p in rels.values() if p.endswith("sharedStrings.xml")), "xl/sharedStrings.xml")
    if path not in z.namelist():
        return []
    return [_text_of(si) or "" for si in ET.fromstring(z.read(path)).iterfind("m:si", _NS)]


def _number(text: str) -> int | float:
    if any(ch in text for ch in ".eE"):
        return float(text)
    return int(text)


def _cell_value(c: ET.Element, shared: list[Optional[str]]) -> Any:
    """A cell's stored value, as a Desktop refresh reads it (#104's D3 probes): its number (date-formatted ones
    too), text, logical, a formula's cached value (none: null), or, for an error value, a failed cell."""
    t = c.get("t") or "n"
    v = c.find("m:v", _NS)
    raw = v.text if v is not None else None
    where = c.get("r") or "?"
    if t == "inlineStr":
        return _text_of(c.find("m:is", _NS))
    if raw is None:
        if v is not None and c.find("m:f", _NS) is not None:
            return ""          # a formula whose cached value is empty: Desktop reads "" (#104)
        if v is not None:
            raise _error(f"Excel.Workbook: the cell {where} stores an empty value; not supported yet.")
        return None
    if t == "n":
        try:
            return _number(raw)
        except ValueError:
            raise _error(f"Excel.Workbook: the cell {where} holds {raw!r}, not a number.", "DataFormat.Error") from None
    if t == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            raise _error(f"Excel.Workbook: the cell {where} names a shared string that isn't there.",
                         "DataFormat.Error") from None
    if t == "str":
        return raw
    if t == "b":
        return raw.strip() not in ("0", "false")
    if t == "e":
        return CellError(MError("DataFormat.Error", f"Invalid cell value '{raw}'."))
    raise _error(f"Excel.Workbook: the cell {where} has type {t!r}, not supported yet.")


def _grid(workbook: bytes, part: str, bounds: Optional[tuple[int, int, int, int]], infer: bool) -> list[list[Any]]:
    """The sheet's values: over ``bounds`` (a table's range) as stored; otherwise over the stored dimension's
    columns, down to the last row that stores a cell (a formatted empty one too), or (``infer``) over the cells
    holding values."""
    z = zipfile.ZipFile(io.BytesIO(workbook))
    try:
        root = ET.fromstring(z.read(part))
    except (KeyError, ET.ParseError) as e:
        raise _error(f"Excel.Workbook: the sheet part {part!r} can't be read: {e}", "DataFormat.Error") from None
    shared = _shared_strings(z)
    cells: dict[tuple[int, int], Any] = {}
    stored: list[tuple[int, int]] = []      # every cell the file stores, a formatted empty one too
    for c in root.iterfind("m:sheetData/m:row/m:c", _NS):
        ref = c.get("r")
        if ref is None:
            raise _error("Excel.Workbook: a cell without a reference isn't supported yet.")
        at = _cell_ref(ref)
        stored.append(at)
        value = _cell_value(c, shared)
        if value is not None:
            cells[at] = value
    if bounds is None:
        dim = root.find("m:dimension", _NS)
        if infer or dim is None or not dim.get("ref"):
            if not infer:
                raise _error("Excel.Workbook: the sheet stores no dimension; reading it is InferSheetDimensions' "
                             "job.")
            if not cells:
                return []
            cols = [k[0] for k in cells]
            rows = [k[1] for k in cells]
            bounds = (min(cols), min(rows), max(cols), max(rows))
        else:
            min_col, min_row, max_col, max_row = _range(dim.get("ref") or "")
            last = max((r for (col, r) in stored if min_col <= col <= max_col and min_row <= r <= max_row),
                       default=min_row - 1)
            bounds = (min_col, min_row, max_col, last)
    min_col, min_row, max_col, max_row = bounds
    return [[cells.get((col, r)) for col in range(min_col, max_col + 1)] for r in range(min_row, max_row + 1)]


def _sheet(workbook: bytes, part: str, infer: bool, headers: bool) -> Table:
    rows = _grid(workbook, part, None, infer)
    width = len(rows[0]) if rows else 0
    table = Table([f"Column{i + 1}" for i in range(width)], rows, [ANY] * width)
    if headers:
        from .tables import TABLES
        promoted: Table = TABLES["Table.PromoteHeaders"].invoke([table])
        return promoted
    return table


def _table(workbook: bytes, part: str, t: dict) -> Table:
    if t["header"] != 1:
        raise _error(f"Excel.Workbook: table {t['name']!r} has no header row; not supported yet.")
    min_col, min_row, max_col, max_row = _range(t["ref"])
    body = _grid(workbook, part, (min_col, min_row + 1, max_col, max_row), False) if max_row > min_row else []
    return Table(list(t["columns"]), body, [ANY] * len(t["columns"]))


# --- Parquet.Document -------------------------------------------------------------

def _parquet_document(source: Any, options: Any = None) -> Table:
    """A Parquet file's columns. Microsoft's reference has no page for it; each type reads as a Desktop refresh
    reads it (#104's D3 probes): integers (unsigned 64-bit too) and floating point as numbers (a float32 widened),
    text, logical, date, a timestamp without a zone as datetime and with one as a datetimezone at UTC (+00:00,
    whatever the zone), time, binary, and a decimal as its number (one a double can't hold exactly is refused
    here)."""
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
        convert: Callable[[Any], Any] = lambda v: v  # noqa: E731
        if pa.types.is_integer(t):
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
        elif pa.types.is_timestamp(t):
            mtype = MType("datetimezone", True)
            convert = lambda v: v.astimezone(dt.timezone.utc)  # noqa: E731
        elif pa.types.is_time(t):
            mtype = MType("time", True)
        elif pa.types.is_binary(t) or pa.types.is_large_binary(t):
            mtype = MType("binary", True)
        elif pa.types.is_decimal(t):
            mtype = MType("number", True)
            convert = partial(_decimal_number, field.name)
        else:
            raise _error(f"Parquet.Document: column {field.name!r} has type {t}, not supported yet.")
        try:
            values = [None if v is None else convert(v) for v in column.to_pylist()]
        except (ValueError, OverflowError, ArithmeticError) as e:
            raise _error(f"Parquet.Document: column {field.name!r} can't be read: {e}", "DataFormat.Error") from None
        names.append(field.name)
        types.append(mtype)
        columns.append(values)
    rows = [list(r) for r in zip(*columns)] if columns else []
    return Table(names, rows, types)


def _decimal_number(column: str, v: Any) -> int | float:
    f = float(v)
    if Decimal(repr(f)) != v.normalize() and Decimal(f) != v:
        raise _error(f"Parquet.Document: column {column!r} holds {v}, more digits than a double keeps; not supported "
                     "yet.")
    return int(v) if v == v.to_integral_value() and abs(v) < 2 ** 63 else f


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
