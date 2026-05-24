"""prefer-robust-stmts (STYLE, opt-in).

Squawk's prefer-robust-stmts rule — encourage idempotent DDL via IF [NOT] EXISTS
on CREATE/DROP statements so migrations can be safely rerun. Opt-in because
some teams explicitly want failures on duplicate-create as a safety check.

V1 covers the most common cases: CREATE TABLE, DROP TABLE, CREATE INDEX,
DROP INDEX, CREATE SCHEMA. ALTER TABLE ADD/DROP COLUMN IF [NOT] EXISTS exists
but adding to this rule would over-trigger on most migrations.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import ObjectType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "prefer-robust-stmts"


@register_rule(
    id=RULE_ID,
    severity=Severity.STYLE,
    applies_to=(ast.CreateStmt, ast.DropStmt, ast.IndexStmt, ast.CreateSchemaStmt),
    doc="Use IF NOT EXISTS / IF EXISTS for idempotent migrations. Opt-in.",
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    line, column = ctx.line_col()

    label = ""
    needs_if_not_exists = False
    needs_if_exists = False

    if isinstance(stmt, ast.CreateStmt):
        if not stmt.if_not_exists:
            label, needs_if_not_exists = "CREATE TABLE", True
    elif isinstance(stmt, ast.IndexStmt):
        if not stmt.if_not_exists:
            label, needs_if_not_exists = "CREATE INDEX", True
    elif isinstance(stmt, ast.CreateSchemaStmt):
        if not stmt.if_not_exists:
            label, needs_if_not_exists = "CREATE SCHEMA", True
    elif isinstance(stmt, ast.DropStmt):
        if not getattr(stmt, "missing_ok", False):
            if stmt.removeType == ObjectType.OBJECT_TABLE:
                label, needs_if_exists = "DROP TABLE", True
            elif stmt.removeType == ObjectType.OBJECT_INDEX:
                label, needs_if_exists = "DROP INDEX", True

    if not label:
        return

    clause = "IF NOT EXISTS" if needs_if_not_exists else "IF EXISTS"
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.STYLE,
        file=ctx.file,
        line=line,
        column=column,
        message=f"{label} without {clause} — not safely re-runnable.",
        help=(
            "Migrations should be idempotent: running them twice should be a no-op, "
            "not an error. Add IF [NOT] EXISTS so re-running on an already-applied "
            "schema doesn't fail the migration runner."
        ),
    )
