"""truncate-cascade-banned — squawk-equivalent: TRUNCATE CASCADE silently nukes referenced tables.

Fires CRITICAL on `TRUNCATE TABLE ... CASCADE`. CASCADE silently propagates the
TRUNCATE through any tables that reference the target via FOREIGN KEY, deleting
all their rows too. Almost always a mistake — the developer intended to truncate
ONE table.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import DropBehavior

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "truncate-cascade-banned"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.TruncateStmt,),
    doc=(
        "TRUNCATE ... CASCADE silently empties not just the target table but every "
        "table that has a FOREIGN KEY referencing it. This is rarely the intended "
        "behavior. If you genuinely want the cascade, suppress with "
        "`-- safemigrate:ignore=truncate-cascade-banned reason=\"...\"`; otherwise "
        "drop CASCADE and Postgres will refuse the TRUNCATE with a clear error message "
        "listing the dependent tables."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.TruncateStmt):
        return
    if stmt.behavior != DropBehavior.DROP_CASCADE:
        return

    line, column = ctx.line_col()
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.CRITICAL,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            "TRUNCATE ... CASCADE silently empties all FOREIGN-KEY-referencing tables. "
            "Almost always a mistake."
        ),
        help=(
            "CASCADE on TRUNCATE propagates the empty-table operation through the "
            "FK graph: every table that references the truncated table is ALSO "
            "truncated. There is no rollback once the migration runs (well, you can "
            "ROLLBACK the transaction, but if it commits, all the data is gone). "
            "Drop CASCADE and let Postgres refuse the TRUNCATE — the error message "
            "will name the dependent tables so you can make the decision deliberately. "
            "If you truly want the cascade, suppress this rule explicitly."
        ),
        suggested_fix=None,
    )
