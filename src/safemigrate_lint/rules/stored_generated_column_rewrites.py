"""stored-generated-column-rewrites — Atlas PG309 equivalent.

ALTER TABLE ... ADD COLUMN <c> <type> GENERATED ALWAYS AS (<expr>) STORED
on a populated table triggers a full table rewrite under AccessExclusiveLock.
Postgres must compute the generated expression for every existing row.

Atlas PG309 only fires on ALTER (not inline CREATE TABLE generated columns)
and does not flag VIRTUAL generated columns (Postgres 18+, computed lazily).
Our implementation matches: applies_to=(AlterTableStmt,) excludes inline
generated columns naturally; pglast 7.13 doesn't yet parse VIRTUAL syntax
so we never see those.

This is coverage Atlas paywalls and the free OSS linters don't ship.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType, ConstrType

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "stored-generated-column-rewrites"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE ADD COLUMN ... GENERATED ALWAYS AS (...) STORED rewrites every "
        "existing row to compute the generated value, under AccessExclusiveLock. On "
        "large tables this blocks reads and writes for minutes. The expand-contract "
        "pattern works here too: add a nullable regular column, backfill the computed "
        "values in batches, then optionally convert to GENERATED in a follow-up. "
        "Matches Atlas PG309. VIRTUAL generated columns (Postgres 18+) are exempt — "
        "Atlas excludes them; pglast 7.13 doesn't parse VIRTUAL syntax yet."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    if table and table_created_in_migration(state, table):
        return  # Atlas PG309 also suppresses — same-migration table, empty, no rewrite

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_AddColumn:
            continue
        column_def = cmd.def_
        if not isinstance(column_def, ast.ColumnDef):
            continue
        if not _has_stored_generated_constraint(column_def):
            continue

        col_name = column_def.colname or "<unnamed>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.CRITICAL,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"ADD COLUMN {col_name} GENERATED ALWAYS AS ... STORED on "
                f"{table or 'table'} rewrites every row and locks the table for the "
                f"duration."
            ),
            help=(
                "STORED generated columns store the computed value physically. Postgres "
                "must evaluate the generation expression for every existing row and "
                "write the result, requiring a full table rewrite under "
                "AccessExclusiveLock. On large tables this blocks reads and writes for "
                "minutes. The expand-contract pattern: ADD COLUMN nullable, backfill in "
                "batches with explicit UPDATEs, optionally promote to GENERATED later. "
                "Postgres 18+ VIRTUAL generated columns are computed on read and don't "
                "trigger a rewrite (but require a much newer Postgres version)."
            ),
            suggested_fix=(
                f"-- Three-step expand-contract for a STORED generated column:\n"
                f"-- 1. ALTER TABLE {table} ADD COLUMN {col_name} <type>;\n"
                f"-- 2. Backfill: UPDATE {table} SET {col_name} = <expression> "
                f"WHERE {col_name} IS NULL ORDER BY <pk> LIMIT N;\n"
                f"-- 3. Future-row trigger or app-level write-side compute."
            ),
        )


def _has_stored_generated_constraint(column_def: ast.ColumnDef) -> bool:
    for constraint in column_def.constraints or ():
        if not isinstance(constraint, ast.Constraint):
            continue
        if constraint.contype == ConstrType.CONSTR_GENERATED:
            return True
    return False


