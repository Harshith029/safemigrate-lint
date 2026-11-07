"""Small pglast AST helpers shared across rules and the state machine.

These were previously copy-pasted into ~20 rule modules; centralizing keeps the
name-extraction behavior identical everywhere.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast


def iter_nodes(root: Any) -> Iterator[ast.Node]:
    """Every pglast AST node under `root`, `root` included."""
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, (list, tuple)):
            stack.extend(node)
        elif isinstance(node, ast.Node):
            yield node
            # Iterating a pglast node yields its attribute names.
            stack.extend(getattr(node, attr, None) for attr in node)


def table_name(relation: Any) -> str:
    """Canonical (possibly schema-qualified) table name from a RangeVar node.

    Returns "" when the relation is missing.
    """
    if relation is None:
        return ""
    schemaname = getattr(relation, "schemaname", None) or ""
    relname = getattr(relation, "relname", None) or ""
    return f"{schemaname}.{relname}" if schemaname else relname


def qualified_name(name_parts: Any) -> str:
    """Join a list of pglast String nodes (e.g. a dotted ``schema.table`` name)
    into a dotted string. Returns "" when the list is missing.
    """
    if name_parts is None:
        return ""
    parts: list[str] = []
    for part in name_parts:
        sval = getattr(part, "sval", None)
        if sval:
            parts.append(sval)
    return ".".join(parts)
