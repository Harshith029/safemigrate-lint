"""drop-table-restricted — DROP TABLE is irreversible data loss for the entire relation.

Fires CRITICAL on every `DROP TABLE …` statement. One finding per table dropped
(a single DROP TABLE can take multiple table names).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import ObjectType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "drop-table-restricted"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.DropStmt,),
    doc=(
        "DROP TABLE is irreversible data loss for the entire relation. All rows, "
        "indexes, constraints, and triggers tied to the table are removed. There is "
        "no rollback once the migration runs. Suppress with `-- safemigrate:ignore="
        "drop-table-restricted reason=\"...\"` after confirming the table has no "
        "production data or is intentionally being decommissioned."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.DropStmt):
        return
    if stmt.removeType != ObjectType.OBJECT_TABLE:
        return

    if_exists = " IF EXISTS" if getattr(stmt, "missing_ok", False) else ""
    line, col_offset = ctx.line_col()

    for obj in stmt.objects or ():
        table = _qualified_name(obj)
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.CRITICAL,
            file=ctx.file,
            line=line,
            column=col_offset,
            message=(
                f"DROP TABLE{if_exists} {table or '<unknown>'} is irreversible data "
                f"loss for the entire relation."
            ),
            help=(
                "DROP TABLE removes the table, all its rows, all dependent indexes, "
                "and any rows in tables that reference it via CASCADE. There is no "
                "rollback path once the migration runs. If this table has production "
                "data, ensure it has been archived. To suppress, add `-- safemigrate:"
                "ignore=drop-table-restricted reason=\"...\"` on the line immediately "
                "preceding the DROP TABLE statement."
            ),
            suggested_fix=None,
        )


def _qualified_name(obj: Any) -> str:
    """Join a tuple of pglast String nodes into a dotted qualified name."""
    if obj is None:
        return ""
    parts: list[str] = []
    for part in obj:
        sval = getattr(part, "sval", None)
        if sval:
            parts.append(sval)
    return ".".join(parts)
