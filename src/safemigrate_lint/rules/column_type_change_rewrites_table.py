"""column-type-change-rewrites-table — ALTER COLUMN TYPE rewrites the whole table.

Fires CRITICAL on `ALTER TABLE … ALTER COLUMN … TYPE …`. Postgres takes
AccessExclusiveLock and rewrites every row to convert to the new type (unless
the new type is binary-coercible — which is hard to determine statically).
Suppressed when the table was created in the same migration (empty → no rewrite
cost).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_known_empty
from ._registry import RuleContext, register_rule

RULE_ID = "column-type-change-rewrites-table"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER COLUMN ... TYPE rewrites every row of the table (unless the new type "
        "is binary-coercible with the old, which is hard to determine statically) and "
        "takes AccessExclusiveLock for the duration. On a large table this means "
        "minutes of write-blocking. Use the expand-contract column-replacement pattern: "
        "add a new column with the target type, backfill, switch reads, drop old."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    if table and table_known_empty(state, table):
        return  # empty table — no rewrite cost

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_AlterColumnType:
            continue

        col_name = cmd.name or "<unnamed>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.CRITICAL,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"ALTER COLUMN {col_name} TYPE on {table or 'table'} rewrites every "
                f"row and locks the table for the duration."
            ),
            help=(
                "Most ALTER COLUMN TYPE operations require a full table rewrite. Postgres "
                "takes AccessExclusiveLock (blocks reads AND writes) for the entire "
                "rewrite duration — minutes on a large table. Exception: type changes "
                "that are binary-coercible (e.g. varchar(N) to varchar(M) with M>=N, "
                "varchar to text) skip the rewrite, but this is hard to determine "
                "statically. The safe pattern is expand-contract: ADD COLUMN new_col "
                "with the target type, backfill in batches, switch reads, then DROP old."
            ),
            suggested_fix=(
                f"-- Expand-contract type change:\n"
                f"-- 1. ALTER TABLE {table} ADD COLUMN {col_name}_new <new_type>;\n"
                f"-- 2. Backfill in batches: UPDATE {table} SET {col_name}_new = "
                f"{col_name}::<new_type> WHERE {col_name}_new IS NULL LIMIT N;\n"
                f"-- 3. Deploy app code to write both columns.\n"
                f"-- 4. Switch reads to {col_name}_new.\n"
                f"-- 5. ALTER TABLE {table} DROP COLUMN {col_name};\n"
                f"-- 6. ALTER TABLE {table} RENAME COLUMN {col_name}_new TO {col_name};"
            ),
        )


