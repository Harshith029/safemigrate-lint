"""constraint-dropped-warning — covers Atlas CD101/CD102/CD103 collectively.

Atlas distinguishes three rules in this space because Atlas does schema diffing:
  CD101 — FOREIGN KEY constraint was dropped
  CD102 — CHECK constraint was removed
  CD103 — PRIMARY KEY constraint was deleted

We cannot distinguish these from SQL alone: `ALTER TABLE foo DROP CONSTRAINT bar`
does not say what kind of constraint `bar` was. We fire one consolidated warning
per DROP CONSTRAINT statement covering all three Atlas cases.

The risk in all three cases is real: dropping a constraint silently allows
previously-rejected data shapes into the table. Applications that depend on the
invariant (FK references valid, CHECK predicate true, PK uniqueness) may break.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "constraint-dropped-warning"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE DROP CONSTRAINT removes a database-enforced invariant (foreign "
        "key, check, primary key, or unique constraint). Applications that depend on "
        "the invariant may break silently when invalid data is allowed in. Atlas "
        "free-tier covers this as three rules (CD101 FK, CD102 CHECK, CD103 PK); "
        "we consolidate because the constraint type is not knowable from SQL text alone."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = _table_name(stmt.relation)
    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_DropConstraint:
            continue

        constraint_name = cmd.name or "<unnamed>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"DROP CONSTRAINT {constraint_name} on {table or 'table'} removes a "
                f"database-enforced invariant — application code that relies on it may break."
            ),
            help=(
                "ALTER TABLE DROP CONSTRAINT removes whatever the constraint enforced: "
                "a FOREIGN KEY (referential integrity), CHECK (predicate validity), "
                "PRIMARY KEY (row uniqueness + non-null), or UNIQUE constraint. "
                "Application code that assumes the invariant holds will silently accept "
                "data that previously would have been rejected. Atlas free-tier breaks "
                "this into CD101 (FK), CD102 (CHECK), and CD103 (PK) because Atlas "
                "performs schema diffing and knows the constraint type. We cannot tell "
                "from `ALTER TABLE ... DROP CONSTRAINT name` alone — same warning regardless. "
                "Suppress with `-- safemigrate:ignore=constraint-dropped-warning reason=\"...\"` "
                "after confirming the drop is intentional."
            ),
            suggested_fix=None,
        )


def _table_name(relation: Any) -> str:
    if relation is None:
        return ""
    schemaname = getattr(relation, "schemaname", None) or ""
    relname = getattr(relation, "relname", None) or ""
    return f"{schemaname}.{relname}" if schemaname else relname
