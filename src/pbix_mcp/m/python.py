"""Python.Execute: Desktop's M side of a Python script step (pivot-studio #102).

``Python.Execute(script)`` (a source) and ``Python.Execute(script, [dataset = table, ...])`` (a transform) are
evaluated as a Desktop refresh evaluates them:

1. each table argument is written as CSV the way Desktop's writer writes it (``to_csv``);
2. the script runs, wrapped in Desktop's prolog and epilog (``wrap_script``), in a folder holding those files;
3. every CSV the run leaves there, except the inputs, is a row of the result: ``Name`` the file's stem, ``Value``
   the file read back with its first row as headers and every cell text (``from_csv``).

This module runs nothing itself. The caller gives ``evaluate`` a ``python`` runner,
``runner(script, inputs) -> outputs``: ``inputs`` maps each argument's name to its CSV bytes, ``outputs`` maps
each output file's stem to its bytes. Where and how the script runs, and whether it may (an opt-in, an approval,
a sandbox), is the runner's business. With no runner, or when the runner declines, the step raises
``ScriptNotRun``. Like ``UnmappedSource``, that isn't an M error: no ``try`` absorbs it, so a refresh reports
the table as not run and keeps its saved data rather than loading a fallback. A script that fails (exits with an
error) is an M error, ``script_failed``.

Every rule here is what a Desktop 2.157 refresh did on pivot-studio's probes (its m-desktop ``py_*`` fixtures:
the CSV each input became, what pandas read from it, and each output as M read it back); what they don't settle
is refused.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
from typing import Any, Callable, Optional

from .values import ANY, MError, MType, Record, Table, force, kind_of

Runner = Callable[[str, "dict[str, bytes]"], "dict[str, bytes]"]
TEXT_TYPE = MType("text")
TABLE_TYPE = MType("table")


class ScriptNotRun(Exception):
    """A Python step that wasn't run: Python isn't enabled for the model, or this script isn't approved. The table
    keeps its saved data. Deliberately not an MError, so no M construct can absorb it. ``key`` identifies the
    script as an approval does (``script_key``)."""

    def __init__(self, script: str, parameters: list[str], reason: str):
        super().__init__(f"The Python script wasn't run: {reason}")
        self.script, self.parameters, self.reason = script, parameters, reason
        self.key = script_key(script, parameters)
        self.message = str(self)


def script_key(script: str, parameters: list[str]) -> str:
    """What one approval covers: the script's text and its parameters' names (Desktop asks again for the same text
    with parameters, never for new parameter values)."""
    h = hashlib.sha256(script.encode("utf-8"))
    for p in parameters:
        h.update(b"\0" + p.encode("utf-8"))
    return h.hexdigest()


def script_failed(stderr: str) -> MError:
    """The error of a script that exited with an error, carrying what it wrote to stderr. The reason Desktop gives
    isn't measured yet."""
    return MError("DataSource.Error", f"The Python script failed: {stderr.strip()}")


# --- the wrapper -----------------------------------------------------------------------------------------------

def wrap_script(script: str, folder: str, inputs: dict[str, str]) -> str:
    """The script as a Desktop refresh runs it, for a runner to execute with ``folder`` as its working folder:

    - pandas and matplotlib imported (Microsoft's documentation requires both; matplotlib on its non-interactive
      backend, so a ``show()`` draws nothing);
    - each input read with a plain ``pandas.read_csv`` into a global named as its argument (``inputs`` maps the
      argument's name to its file's name in ``folder``), so pandas' own inference and missing-value strings apply,
      as the probes show ("", "NA", "null" and "nan" read as NaN; an Int64 column with a null as float64);
    - the script, then, back in ``folder`` (the script may have changed directory), every global that is exactly a
      DataFrame written to ``<name>.csv`` without its index: underscore names and the inputs too, a Series not;
    - a ``from __future__`` import moved to the top, where Python requires it.

    Every .csv the run leaves in ``folder`` is then an output, a file the script wrote there itself included."""
    here = repr(folder)
    lines = ["import os as _pbix_os, pandas, matplotlib", "matplotlib.use('Agg')", "import matplotlib.pyplot",
             f"_pbix_os.chdir({here})", "_pbix_globals = dir"]
    lines += [f"{name} = pandas.read_csv({file!r})" for name, file in inputs.items()]
    body = [line for line in script.splitlines() if not line.startswith("from __future__")]
    future = [line for line in script.splitlines() if line.startswith("from __future__")]
    lines += body
    lines += ["", f"_pbix_os.chdir({here})",
              "for _pbix_name in _pbix_globals():",
              "    if type(globals()[_pbix_name]) is pandas.DataFrame:",
              "        globals()[_pbix_name].to_csv(_pbix_name + '.csv', index=False)"]
    return "\n".join(future + lines) + "\n"


# --- writing a table argument ----------------------------------------------------------------------------------

_ESCAPE = (",", "'", '"', "\n")


def to_csv(table: Table) -> bytes:
    """A table as a Desktop refresh hands it to a script (the probes read the file back): a header row, then a row
    per row, CR LF after each;
    a value holding a comma, an apostrophe, a quote or a line feed is quoted, its quotes doubled; UTF-8 without a
    byte order mark."""
    out = [",".join(_escape(c) for c in table.columns)]
    for row in table.rows:
        out.append(",".join(_escape(_cell_text(force(v), t)) for v, t in zip(row, table.types)))
    return "".join(line + "\r\n" for line in out).encode("utf-8")


def _escape(text: str) -> str:
    if any(c in text for c in _ESCAPE):
        return '"' + text.replace('"', '""') + '"'
    return text


def _cell_text(v: Any, mtype: MType) -> str:
    """A value as the probes show Desktop writes it: null empty; a whole number
    of an Int64.Type column as its digits; another number as .NET's "R" (Desktop measured "1E+21", "1E-07", "0" for
    -0); a Currency.Type column's value, a date and a time as the names of their OLE DB types (a Desktop quirk the
    probes show); a datetime as "o"; a datetimezone and a duration by the invariant culture; a logical as
    True/False."""
    if v is None:
        return ""
    kind = kind_of(v)
    if kind == "logical":
        return "True" if v else "False"
    if kind == "number":
        if mtype.facet == "Currency.Type":
            return "Microsoft.OleDb.Currency"
        if mtype.facet == "Int64.Type" and float(v).is_integer():
            return str(int(v))
        return _dotnet_r(float(v))
    if kind == "text":
        return v
    if kind == "date":
        return "Microsoft.OleDb.Date"
    if kind == "time":
        return "Microsoft.OleDb.Time"
    if kind == "datetime":
        return v.strftime("%Y-%m-%dT%H:%M:%S.") + f"{v.microsecond:06d}0"
    if kind == "datetimezone":
        return v.strftime("%m/%d/%Y %H:%M:%S ") + _offset(v.utcoffset())
    if kind == "duration":
        return _timespan(v)
    raise MError("Expression.Error", f"Python.Execute: a {kind} in a table argument isn't supported yet (how "
                                     "Desktop writes it isn't measured).")


def _dotnet_r(f: float) -> str:
    """.NET Framework's double.ToString("R", invariant): 15 significant digits when they read back to the same
    double, else 17; scientific below 1E-04 and from 1E+15 (the G rule), an exponent of at least two digits."""
    if math.isnan(f):
        return "NaN"
    if math.isinf(f):
        return "Infinity" if f > 0 else "-Infinity"
    if f == 0:
        return "0"
    text = f"{f:.15G}"
    if float(text) != f:
        text = f"{f:.17G}"
    return text


def _offset(off: Optional[dt.timedelta]) -> str:
    minutes = int((off or dt.timedelta()).total_seconds() // 60)
    sign = "-" if minutes < 0 else "+"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _timespan(d: dt.timedelta) -> str:
    """TimeSpan's invariant text: [-][d.]hh:mm:ss[.fffffff]."""
    ticks = (d.days * 86400 + d.seconds) * 10_000_000 + d.microseconds * 10
    sign = "-" if ticks < 0 else ""
    ticks = abs(ticks)
    days, rest = divmod(ticks, 864_000_000_000)
    hours, rest = divmod(rest, 36_000_000_000)
    minutes, rest = divmod(rest, 600_000_000)
    seconds, fraction = divmod(rest, 10_000_000)
    text = f"{sign}{f'{days}.' if days else ''}{hours:02d}:{minutes:02d}:{seconds:02d}"
    return text + (f".{fraction:07d}" if fraction else "")


# --- reading an output -----------------------------------------------------------------------------------------

def from_csv(data: bytes) -> Table:
    """An output file as Desktop reads it back: CSV records (quotes and line breaks inside them as Csv.Document
    reads them), the first record's values as the column names (an empty name stays empty, a repeated one gains
    "_<n>", as Table.PromoteHeaders names them), and every cell text, an empty one "" (NaN and None alike)."""
    from .sources import _csv_records, _decode
    from .tables import _promote_headers
    rows = _csv_records(_decode(data, 65001), ",", True)
    width = len(rows[0]) if rows else 0
    raw = Table([f"Column{i + 1}" for i in range(width)], [(r + [""] * (width - len(r)))[:width] for r in rows],
                [ANY] * width)
    return _promote_headers(raw)


def _ntfs_order(name: str) -> str:
    """The order Desktop lists the output files in: NTFS's, by upper-cased name ("written_by_script" before
    "_o_private")."""
    return name.upper()


# --- the function ----------------------------------------------------------------------------------------------

def python_execute(evaluator: Any) -> Any:
    from .values import Function

    def call(script: Any, arguments: Any = None) -> Table:
        if not isinstance(script, str):
            raise MError("Expression.Error", f"Python.Execute needs the script as text, not {kind_of(script)}.")
        if arguments is not None and not isinstance(arguments, Record):
            raise MError("Expression.Error", "Python.Execute's arguments must be a record of tables.")
        tables: dict[str, Table] = {}
        for name in (arguments.fields if arguments is not None else {}):
            value = force(arguments[name])
            if not isinstance(value, Table):
                raise MError("Expression.Error", f"Python.Execute: the argument '{name}' must be a table, not "
                                                 f"{kind_of(value)}.")
            tables[name] = value
        runner: Optional[Runner] = getattr(evaluator, "python", None)
        if runner is None:
            raise ScriptNotRun(script, list(tables), "Python isn't enabled for this model.")
        outputs = runner(script, {name: to_csv(t) for name, t in tables.items()})
        rows = [[name, from_csv(outputs[name])] for name in sorted(outputs, key=_ntfs_order)]
        return Table(["Name", "Value"], rows, [TEXT_TYPE, TABLE_TYPE])

    return Function("Python.Execute", call, 1, 2)


PYTHON: dict[str, Any] = {"Python.Execute": python_execute}
