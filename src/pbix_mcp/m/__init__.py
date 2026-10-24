"""The M (Power Query formula language): lexer, parser and static analysis.

Written from Microsoft's published M language specification
(learn.microsoft.com/powerquery-m/power-query-m-language-specification) and
function reference. ``evaluate`` runs an expression; the library it knows is
``LIBRARY_NAMES`` (Enter-data tables, #table, column types, Table.Combine).
"""
from .analysis import free_names
from .ast import Node, Section, walk
from .evaluate import evaluate, force_table
from .lexer import MSyntaxError, tokenize
from .library import LIBRARY_NAMES, TIERS
from .parser import parse_document, parse_expression
from .sources import UnmappedSource
from .values import CellError, Function, MError, MType, Record, Table

__all__ = ["CellError", "Function", "LIBRARY_NAMES", "MError", "MSyntaxError", "MType", "Node", "Record", "Section",
           "TIERS", "Table", "UnmappedSource", "evaluate", "force_table", "free_names", "parse_document",
           "parse_expression", "tokenize", "walk"]
