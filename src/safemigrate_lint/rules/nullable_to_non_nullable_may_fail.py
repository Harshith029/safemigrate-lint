"""nullable-to-non-nullable-may-fail — SET NOT NULL fails if existing rows have NULLs.

Fires WARNING on `ALTER TABLE … ALTER COLUMN … SET NOT NULL`. Postgres validates
the constraint against existing rows; the migration fails immediately if any row
has NULL in that column. Suppressed when the table was created in the same
migration (empty table, no NULLs possible).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "nullable-to-non-nullable-may-fail"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER COLUMN ... SET NOT NULL validates the constraint against every existing "
        "row. The migration fails immediately if any row has NULL in that column. "
        "Either backfill the NULLs in a prior migration before adding the constraint, "
        "or use the three-step expand-contract pattern."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    if table and table_created_in_migration(state, table):
        return  # empty table — no rows can be NULL

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_SetNotNull:
            continue

        col_name = cmd.name or "<unnamed>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"SET NOT NULL on {table or 'table'}.{col_name} will fail if any "
                f"existing row has NULL in this column."
            ),
            help=(
                "Postgres scans the entire table to validate SET NOT NULL. If any row "
                "violates the constraint, the migration fails immediately and the lock "
                "is released — but you've still spent table-scan time. On a large table "
                "this scan also takes an AccessExclusiveLock for the duration. Backfill "
                "the NULLs in a prior, batched migration so the SET NOT NULL itself is "
                "fast and safe."
            ),
            suggested_fix=(
                f"-- Backfill first (separate migration, batched):\n"
                f"-- UPDATE {table} SET {col_name} = <default> WHERE {col_name} IS NULL;\n"
                f"-- Then in a follow-up migration:\n"
                f"-- ALTER TABLE {table} ALTER COLUMN {col_name} SET NOT NULL;"
            ),
        )


