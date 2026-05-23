"""unique-constraint-data-dependent — ADD UNIQUE may fail if existing rows contain duplicates.

Fires WARNING on `ALTER TABLE … ADD CONSTRAINT … UNIQUE (…)`. Postgres builds
a unique index against existing rows; if any duplicates exist the migration
fails immediately. Suppressed when the table was created in the same migration
(empty table → no duplicates possible).

NOT VALID is not applicable to UNIQUE constraints in Postgres (UNIQUE is
enforced by a backing index, which must validate by construction), so the
suggested fix is "deduplicate first in a prior migration."
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType, ConstrType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "unique-constraint-data-dependent"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE ADD CONSTRAINT ... UNIQUE on an existing column may fail at "
        "migration time if any existing rows have duplicate values. Postgres builds "
        "a unique index, which requires a full scan and takes AccessExclusiveLock "
        "on the table for the duration. Deduplicate in a prior migration; use "
        "CREATE UNIQUE INDEX CONCURRENTLY when possible."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = _table_name(stmt.relation)
    if table and table_created_in_migration(state, table):
        return  # empty table → no duplicates possible

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_AddConstraint:
            continue
        constraint = cmd.def_
        if not isinstance(constraint, ast.Constraint):
            continue
        if constraint.contype != ConstrType.CONSTR_UNIQUE:
            continue

        constraint_name = constraint.conname or "<unnamed>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"ADD CONSTRAINT {constraint_name} UNIQUE on {table or 'table'} will "
                f"fail if existing rows contain duplicate values."
            ),
            help=(
                "Postgres builds a unique index to enforce the constraint; the index "
                "build takes AccessExclusiveLock on the table for the duration of the "
                "full table scan. If duplicates exist the migration fails after the "
                "scan, so you also waste the lock time. Two safer approaches: "
                "(1) deduplicate in a prior batched migration, then add the constraint "
                "non-blockingly via CREATE UNIQUE INDEX CONCURRENTLY followed by "
                "ALTER TABLE ... ADD CONSTRAINT ... USING INDEX; "
                "(2) verify no duplicates exist in a pre-flight query before the migration."
            ),
            suggested_fix=(
                f"-- Two-step pattern (non-blocking on populated tables):\n"
                f"CREATE UNIQUE INDEX CONCURRENTLY {constraint_name}_idx "
                f"ON {table} (<columns>);\n"
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
                f"UNIQUE USING INDEX {constraint_name}_idx;"
            ),
        )


def _table_name(relation: Any) -> str:
    if relation is None:
        return ""
    schemaname = getattr(relation, "schemaname", None) or ""
    relname = getattr(relation, "relname", None) or ""
    return f"{schemaname}.{relname}" if schemaname else relname
