"""Small pglast AST helpers shared across rules and the state machine.

These were previously copy-pasted into ~20 rule modules; centralizing keeps the
name-extraction behavior identical everywhere.
"""

from __future__ import annotations

from typing import Any


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


def bare(name: str) -> str:
    """Drop the schema prefix from a possibly-qualified name (``a.b`` -> ``b``)."""
    return name.rsplit(".", 1)[-1] if "." in name else name
