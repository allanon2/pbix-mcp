"""The M (Power Query formula language): Power Query's reader (``check``), static analysis and evaluation.

Written from Microsoft's published M language specification
(learn.microsoft.com/powerquery-m/power-query-m-language-specification) and
function reference. ``evaluate`` runs an expression; the library it knows is
``LIBRARY_NAMES`` (Enter-data tables, #table, column types, Table.Combine).
"""
from .analysis import free_names
from .ast import Node, Section, walk
from .check import MSyntaxError, compile_document, parse_document, parse_expression, referenced_document
from .evaluate import evaluate, force_table
from .library import LIBRARY_NAMES, TIERS
from .python import ScriptNotRun, script_key
from .sources import UnmappedSource
from .values import CellError, Function, MError, MType, Record, Table

__all__ = ["CellError", "Function", "LIBRARY_NAMES", "MError", "MSyntaxError", "MType", "Node", "Record", "Section",
           "ScriptNotRun", "TIERS", "Table", "UnmappedSource", "compile_document", "evaluate", "force_table", "free_names", "parse_document",
           "parse_expression", "referenced_document", "script_key", "walk"]
