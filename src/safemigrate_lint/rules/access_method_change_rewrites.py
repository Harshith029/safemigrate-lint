"""access-method-change-rewrites — Atlas PG311 equivalent.

ALTER TABLE ... SET ACCESS METHOD <method> rewrites the entire table into the
new storage form (e.g. heap → columnar). Holds AccessExclusiveLock for the
duration; on a large table this is minutes of blocked reads and writes.

Atlas PG311 fires unconditionally; we add same-migration suppression because
on an empty table the rewrite is trivial.
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

RULE_ID = "access-method-change-rewrites"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE SET ACCESS METHOD rewrites the entire table into the new "
        "storage form, under AccessExclusiveLock. On large tables this blocks reads "
        "and writes for minutes. Pre-create the new-method table, copy data in "
        "batches, and atomically swap names if the table is too large for an "
        "in-place rewrite. Matches Atlas PG311."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    if table and table_created_in_migration(state, table):
        return

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_SetAccessMethod:
            continue

        method = cmd.name or "<unspecified>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.CRITICAL,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"ALTER TABLE {table or 'table'} SET ACCESS METHOD {method} rewrites "
                f"the entire table and locks reads + writes for the duration."
            ),
            help=(
                "SET ACCESS METHOD rewrites the table into the new storage form (e.g. "
                "heap to columnar). Postgres holds AccessExclusiveLock for the full "
                "rewrite — minutes on a large table. The non-blocking alternative is "
                "the create-copy-swap pattern: CREATE TABLE new_foo USING <method>, "
                "INSERT INTO new_foo SELECT * FROM foo (in batches if needed), then "
                "swap the tables atomically with RENAME. Coordinate with app code that "
                "reads/writes the table during the swap."
            ),
            suggested_fix=None,
        )


