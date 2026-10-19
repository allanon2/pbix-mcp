"""The M (Power Query formula language): lexer, parser and static analysis.

Written from Microsoft's published M language specification
(learn.microsoft.com/powerquery-m/power-query-m-language-specification).
Evaluation is not here yet.
"""
from .analysis import free_names
from .ast import Node, Section, walk
from .lexer import MSyntaxError, tokenize
from .parser import parse_document, parse_expression

__all__ = ["MSyntaxError", "Node", "Section", "free_names", "parse_document", "parse_expression", "tokenize", "walk"]
