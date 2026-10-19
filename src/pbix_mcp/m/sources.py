"""Tier 3 of the library: local files -- File.Contents, Csv.Document,
Excel.Workbook and Parquet.Document.

A file is read only through the ``files`` callable the caller gives
``evaluate`` (a refresh's path mapping): with none, or for a path it doesn't
map, File.Contents raises ``UnmappedSource``, which a refresh reports as such
rather than as a failure.

Rules follow Microsoft's function reference. Where it is silent the reader
refuses (an error naming what isn't known yet), so a refresh never stores a
guess; each such rule is settled by a Desktop refresh of a purpose-built source
(ADR-0013's D3). Excel.Workbook (openpyxl) and Parquet.Document (pyarrow) need
the ``pbix-mcp[sources]`` extra.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from typing import Any, Callable, Optional

from .values import ANY, Function, MError, MType, Record, Table, force, kind_of

# Enumerations, from Microsoft's reference pages.
QUOTE_NONE, QUOTE_CSV = 0, 1
CSV_QUOTE_AFTER_DELIMITER, CSV_QUOTE_ALWAYS = 0, 1
EXTRA_LIST, EXTRA_ERROR, EXTRA_IGNORE = 0, 1, 2
ENCODINGS = {65001: "utf-8", 1252: "cp1252", 1200: "utf-16-le", 1201: "utf-16-be", 20127: "ascii"}


class UnmappedSource(MError):
    """A file the refresh's mapping doesn't cover (ADR-0013): the table keeps its saved data."""

    def __init__(self, path: str):
        super().__init__("DataSource.NotFound", f"No local file is mapped for '{path}'.")
        self.path = path


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
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"', doublequote=True,
                        strict=False)
    try:
        rows = [list(r) if r else [""] for r in reader]
    except csv.Error as e:
        raise _error(f"Csv.Document: {e}", "DataFormat.Error") from None
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
    if isinstance(columns, MType) and columns.kind == "table" and columns.fields is not None:
        return [n for n, _ in columns.fields]
    raise _error("Csv.Document: Columns must be a count, a list of names or a table type.")


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


def _excel_workbook(workbook: Any, use_headers: Any = None, delay_types: Any = None) -> Table:
    """The workbook's sheets and tables as a navigation table (Name, Data, Item, Kind, Hidden), each Data the cells'
    cached values (a formula's last computed value) over the sheet's stored dimension or the table's range."""
    if isinstance(use_headers, Record):
        options = use_headers
        if any(k not in ("UseHeaders", "DelayTypes", "InferSheetDimensions") for k in options.fields):
            raise _error("Excel.Workbook: unknown options.")
        if ("InferSheetDimensions" in options and options["InferSheetDimensions"]):
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
        import openpyxl
    except ImportError:
        raise _error("Excel.Workbook needs pbix-mcp's sources extra (openpyxl).") from None
    try:
        # Read-only mode gives each sheet's stored dimension (the reference: Excel.Workbook reads "the dimensions
        # metadata from the file" unless InferSheetDimensions); the full mode gives its tables.
        ro = openpyxl.load_workbook(io.BytesIO(workbook), read_only=True, data_only=True, keep_links=False)
        wb = openpyxl.load_workbook(io.BytesIO(workbook), data_only=True, keep_links=False)
    except Exception as e:  # noqa: BLE001 - any reader failure is the file's
        raise _error(f"The file isn't an Excel workbook openpyxl can read: {e}", "DataFormat.Error") from None
    rows = []
    try:
        for ws in wb.worksheets:
            hidden = ws.sheet_state != "visible"
            rows.append([ws.title, _sheet(ro[ws.title]), ws.title, "Sheet", hidden])
            for table in ws.tables.values():
                name = table.displayName
                rows.append([name, _excel_table(ws, table), name, "Table", False])
    finally:
        ro.close()
    return Table(list(_NAV), rows, [ANY] * len(_NAV))


def _sheet(ws: Any) -> Table:
    """A sheet over its stored dimension, columns Column1..N. A sheet without one isn't settled yet."""
    from openpyxl.utils.cell import range_boundaries
    try:
        ref = ws.calculate_dimension(force=False)
    except ValueError:
        raise _error(f"Excel.Workbook: sheet {ws.title!r} stores no dimension; reading it is InferSheetDimensions' "
                     "job, not supported yet.") from None
    min_col, min_row, max_col, max_row = range_boundaries(ref)
    names = [f"Column{i + 1}" for i in range(max_col - min_col + 1)]
    rows = []
    for r in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
        cells = [_cell(c) for c in r]
        rows.append(cells + [None] * (len(names) - len(cells)))
    return Table(names, rows, [ANY] * len(names))


def _excel_table(ws: Any, table: Any) -> Table:
    from openpyxl.utils.cell import range_boundaries
    min_col, min_row, max_col, max_row = range_boundaries(table.ref)
    header_rows = table.headerRowCount if table.headerRowCount is not None else 1
    totals = table.totalsRowCount or 0
    if header_rows != 1:
        raise _error(f"Excel.Workbook: table {table.displayName!r} has no header row; not supported yet.")
    names = [str(c.name) for c in table.tableColumns]
    body = ws.iter_rows(min_row=min_row + 1, max_row=max_row - totals, min_col=min_col, max_col=max_col)
    return Table(names, [[_cell(c) for c in r] for r in body], [ANY] * len(names))


def _cell(c: Any) -> Any:
    """A cell's value: number, text, logical, a date-formatted number as datetime, empty as null. An error value
    (#N/A, #DIV/0!, ...) isn't settled by the reference yet."""
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
    are read (integers, floating point, text, logical, date, timestamp without a zone); any other refuses."""
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
        table = pq.read_table(pa.BufferReader(source))
    except Exception as e:  # noqa: BLE001 - any reader failure is the file's
        raise _error(f"The file isn't a Parquet file pyarrow can read: {e}", "DataFormat.Error") from None
    names, types, columns = [], [], []
    for field, column in zip(table.schema, table.columns):
        t = field.type
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
        else:
            raise _error(f"Parquet.Document: column {field.name!r} has type {t}, not supported yet.")
        names.append(field.name)
        types.append(mtype)
        columns.append(column.to_pylist())
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
