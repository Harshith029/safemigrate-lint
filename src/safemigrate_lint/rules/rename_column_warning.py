"""rename-column-warning — RENAME COLUMN breaks applications referencing the old name.

Fires WARNING on every `ALTER TABLE … RENAME COLUMN old TO new`. The rename
itself is fast (catalog-only, brief lock) but downstream callers — application
code, materialized views, dependent functions — that reference the old name by
string will break immediately.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import ObjectType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "rename-column-warning"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.RenameStmt,),
    doc=(
        "RENAME COLUMN is catalog-fast but breaks any code referencing the old column "
        "name by string (application queries, materialized views, ORM mappings, "
        "dependent functions). Coordinate the rename with an app deploy that knows the "
        "new name, or use the dual-column expand-contract pattern (add new column, "
        "backfill, dual-write, switch reads, drop old)."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.RenameStmt):
        return
    if stmt.renameType != ObjectType.OBJECT_COLUMN:
        return

    table = stmt.relation.relname if stmt.relation else "<unknown>"
    old = stmt.subname or "<unknown>"
    new = stmt.newname or "<unknown>"
    line, col = ctx.line_col()

    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=col,
        message=(
            f"Renaming column {table}.{old} to {table}.{new} will break any code "
            f"that still references {old!r}."
        ),
        help=(
            "RENAME COLUMN is catalog-fast (~milliseconds) but the impact is in app code, "
            "not the database: queries, ORM models, materialized views, and dependent "
            "functions that hardcode the old name break immediately when the migration "
            "runs. Coordinate with an app deploy that knows the new name, or use the "
            "expand-contract pattern (add new column, backfill, dual-write, switch reads, "
            "drop old) to ship the schema change ahead of the application change."
        ),
        suggested_fix=(
            f"-- Expand-contract pattern:\n"
            f"-- 1. ALTER TABLE {table} ADD COLUMN {new} <type>;\n"
            f"-- 2. Backfill: UPDATE {table} SET {new} = {old} WHERE {new} IS NULL;\n"
            f"-- 3. Deploy app code that writes both columns.\n"
            f"-- 4. Deploy app code that reads from {new}.\n"
            f"-- 5. ALTER TABLE {table} DROP COLUMN {old};"
        ),
    )
