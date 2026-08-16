"""unique-constraint-exclusive-lock — Atlas PG105 equivalent.

ALTER TABLE ... ADD CONSTRAINT ... UNIQUE on a populated table takes
AccessExclusiveLock for the full duration of the unique-index build. Same
trigger as `unique-constraint-data-dependent` (which warns about the
duplicate-failure risk); this rule warns about the lock duration. Both
fire — they describe different concerns about the same operation. Matches
Atlas's behavior, which also has both MF101 (data-dependent) and PG105
(exclusive lock) for the same trigger.
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

RULE_ID = "unique-constraint-exclusive-lock"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE ADD CONSTRAINT ... UNIQUE on an existing table takes "
        "AccessExclusiveLock for the duration of the backing unique-index build. "
        "On large tables this blocks reads and writes for minutes. Use CREATE UNIQUE "
        "INDEX CONCURRENTLY first, then ALTER TABLE ADD CONSTRAINT ... USING INDEX. "
        "Matches Atlas PG105. Fires alongside unique-constraint-data-dependent "
        "(same trigger, different concern: data failure vs lock duration)."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    if table and table_known_empty(state, table):
        return  # same-migration table — no lock pain on empty

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
            severity=Severity.CRITICAL,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"ADD CONSTRAINT {constraint_name} UNIQUE on {table or 'table'} takes "
                f"AccessExclusiveLock for the unique-index build duration."
            ),
            help=(
                "Postgres builds a unique index to enforce the constraint, and the build "
                "takes AccessExclusiveLock on the table for the full duration. On large "
                "tables this blocks all reads and writes for minutes. Use the two-step "
                "CONCURRENTLY pattern: build the index without locking, then attach it "
                "to the constraint definition with a brief catalog-only lock."
            ),
            suggested_fix=(
                f"-- Two-step non-blocking UNIQUE addition:\n"
                f"CREATE UNIQUE INDEX CONCURRENTLY {constraint_name}_idx "
                f"ON {table} (<columns>);\n"
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
                f"UNIQUE USING INDEX {constraint_name}_idx;"
            ),
        )


