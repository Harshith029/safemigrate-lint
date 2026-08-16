"""add-non-nullable-without-default — ADD COLUMN NOT NULL without DEFAULT fails on populated tables.

Fires CRITICAL when `ALTER TABLE … ADD COLUMN … NOT NULL` is missing a DEFAULT
clause. On a table with existing rows, Postgres has no value to put in the new
NOT NULL column, so the migration fails immediately at the ALTER TABLE.

Suppressed when the table was created in the same migration (no existing rows —
the NOT NULL constraint is satisfied trivially on zero rows).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType, ConstrType

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_known_empty
from ._registry import RuleContext, register_rule

RULE_ID = "add-non-nullable-without-default"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE ADD COLUMN with NOT NULL but no DEFAULT fails immediately on a "
        "table with existing rows — Postgres has no value to write into the new column. "
        "Either add a DEFAULT, or use the three-step pattern: ADD COLUMN nullable, "
        "backfill, ALTER COLUMN SET NOT NULL."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    # Suppress if the table was created in this migration — no existing rows to violate.
    if table and table_known_empty(state, table):
        return

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_AddColumn:
            continue
        column_def = cmd.def_
        if not isinstance(column_def, ast.ColumnDef):
            continue

        has_not_null = False
        has_default = False
        for constraint in column_def.constraints or ():
            if not isinstance(constraint, ast.Constraint):
                continue
            if constraint.contype == ConstrType.CONSTR_NOTNULL:
                has_not_null = True
            elif constraint.contype == ConstrType.CONSTR_DEFAULT:
                has_default = True

        if has_not_null and not has_default:
            col_name = column_def.colname or "<unnamed>"
            yield Finding(
                rule_id=RULE_ID,
                severity=Severity.CRITICAL,
                file=ctx.file,
                line=line,
                column=column,
                message=(
                    f"ADD COLUMN {col_name} NOT NULL on {table or 'table'} has no DEFAULT "
                    f"— will fail at migration time on any populated table."
                ),
                help=(
                    "Postgres requires a value for every row in a NOT NULL column. "
                    "Without DEFAULT, the ALTER TABLE fails at execution time on any "
                    "table that has existing rows. Either add a DEFAULT (immutable "
                    "constant — avoid volatile expressions like now() which trigger a "
                    "table rewrite), or use the three-step expand-contract pattern: "
                    "ADD COLUMN nullable, backfill in batches, then ALTER COLUMN SET "
                    "NOT NULL."
                ),
                suggested_fix=(
                    f"-- Three-step pattern:\n"
                    f"-- 1. ALTER TABLE {table} ADD COLUMN {col_name} <type>;  -- nullable\n"
                    f"-- 2. Backfill in batches (separate migration).\n"
                    f"-- 3. ALTER TABLE {table} ALTER COLUMN {col_name} SET NOT NULL;\n"
                    f"-- OR add an immutable DEFAULT:\n"
                    f"-- ALTER TABLE {table} ADD COLUMN {col_name} <type> NOT NULL "
                    f"DEFAULT <const>;"
                ),
            )


