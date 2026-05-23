"""drop-database-restricted — DROP DATABASE is catastrophic and rarely intentional in a migration.

A DROP DATABASE in a migration file is almost always a mistake: migrations run
against an existing database, so the command would either fail (current
connection holds the DB) or destroy a different DB than intended. Fires
CRITICAL on every DropdbStmt.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "drop-database-restricted"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.DropdbStmt,),
    doc=(
        "DROP DATABASE is catastrophic and rarely intentional inside a migration file. "
        "Migrations run against an existing database; the command will either fail "
        "(can't drop the DB you're connected to) or destroy a different database than "
        "you expect. If this is intentional, run it outside the migration tool."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.DropdbStmt):
        return

    dbname = stmt.dbname or "<unknown>"
    if_exists = " IF EXISTS" if getattr(stmt, "missing_ok", False) else ""
    line, col = ctx.line_col()

    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.CRITICAL,
        file=ctx.file,
        line=line,
        column=col,
        message=(
            f"DROP DATABASE{if_exists} {dbname} in a migration file is almost certainly "
            f"a mistake — catastrophic data loss if it runs."
        ),
        help=(
            "Migrations run against an existing database. A DROP DATABASE inside a "
            "migration file will either (a) fail because Postgres won't let you drop "
            "the database you're connected to, or (b) destroy a different database "
            "than intended. Database drops belong outside the migration tool — manual "
            "operator action, not version-controlled migration. If you really do want "
            "this in a migration, suppress with `-- safemigrate:ignore="
            "drop-database-restricted reason=\"...\"`."
        ),
        suggested_fix=None,
    )
