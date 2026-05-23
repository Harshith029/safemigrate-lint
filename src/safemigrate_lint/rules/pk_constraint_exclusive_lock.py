"""pk-constraint-exclusive-lock — Atlas PG104 equivalent.

ALTER TABLE ... ADD PRIMARY KEY on a populated table takes AccessExclusiveLock
for the full duration of the unique index build that backs the PK constraint.
On a large table this blocks reads AND writes for minutes.

Atlas PG104 spec: only fires on ALTER (not inline CREATE TABLE PRIMARY KEY);
suppressed on tables created in the same migration (empty, no lock cost).
Our implementation matches via the existing state machine.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType, ConstrType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "pk-constraint-exclusive-lock"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE ADD PRIMARY KEY on an existing table takes AccessExclusiveLock for "
        "the duration of the backing unique-index build. On large tables this blocks "
        "reads and writes for minutes. Use CREATE UNIQUE INDEX CONCURRENTLY first, then "
        "ALTER TABLE ADD PRIMARY KEY USING INDEX (catalog-only, brief lock). Matches "
        "Atlas PG104."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = _table_name(stmt.relation)
    if table and table_created_in_migration(state, table):
        return  # Atlas PG104 also suppresses here — same-migration table, empty, no lock cost

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_AddConstraint:
            continue
        constraint = cmd.def_
        if not isinstance(constraint, ast.Constraint):
            continue
        if constraint.contype != ConstrType.CONSTR_PRIMARY:
            continue

        constraint_name = constraint.conname or "<unnamed>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.CRITICAL,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"ADD PRIMARY KEY {constraint_name} on {table or 'table'} takes "
                f"AccessExclusiveLock for the unique-index build duration."
            ),
            help=(
                "Postgres builds a unique index to enforce the PRIMARY KEY constraint. "
                "ALTER TABLE ADD PRIMARY KEY takes AccessExclusiveLock on the table for "
                "the entire build — minutes on a large table, blocking reads and writes. "
                "The non-blocking pattern: CREATE UNIQUE INDEX CONCURRENTLY first, then "
                "ALTER TABLE ADD PRIMARY KEY USING INDEX (which is catalog-only and uses "
                "a brief AccessExclusiveLock just for the constraint metadata update)."
            ),
            suggested_fix=(
                f"-- Two-step non-blocking PK addition:\n"
                f"CREATE UNIQUE INDEX CONCURRENTLY {constraint_name}_idx "
                f"ON {table} (<columns>);\n"
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
                f"PRIMARY KEY USING INDEX {constraint_name}_idx;"
            ),
        )


def _table_name(relation: Any) -> str:
    if relation is None:
        return ""
    schemaname = getattr(relation, "schemaname", None) or ""
    relname = getattr(relation, "relname", None) or ""
    return f"{schemaname}.{relname}" if schemaname else relname
