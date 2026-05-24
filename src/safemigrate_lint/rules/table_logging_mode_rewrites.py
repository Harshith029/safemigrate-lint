"""table-logging-mode-rewrites — Atlas PG307 equivalent.

ALTER TABLE ... SET LOGGED and ALTER TABLE ... SET UNLOGGED both require a
complete table rewrite under AccessExclusiveLock. Postgres has to either copy
all rows into the WAL stream (SET LOGGED) or rewrite into the unlogged storage
form (SET UNLOGGED). On large tables this blocks reads and writes for minutes.

Atlas PG307 spec: full table rewrite, ACCESS EXCLUSIVE lock. Suppressed on
same-migration new tables (no rows = no rewrite cost).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "table-logging-mode-rewrites"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE SET LOGGED / SET UNLOGGED rewrites the entire table under "
        "AccessExclusiveLock — minutes of blocked reads and writes on large tables. "
        "Either create the table with the desired logging mode from the start, or "
        "accept the rewrite cost and run during a maintenance window. Matches Atlas PG307."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = _table_name(stmt.relation)
    if table and table_created_in_migration(state, table):
        return  # same-migration table — no rewrite cost

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype not in (AlterTableType.AT_SetLogged, AlterTableType.AT_SetUnLogged):
            continue

        mode = "LOGGED" if cmd.subtype == AlterTableType.AT_SetLogged else "UNLOGGED"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.CRITICAL,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"ALTER TABLE {table or 'table'} SET {mode} rewrites the entire table "
                f"and locks reads + writes for the duration."
            ),
            help=(
                f"SET {mode} rewrites the table into the new storage form. Postgres "
                f"holds AccessExclusiveLock for the full rewrite — minutes on a large "
                f"table, blocking reads and writes. The safer pattern is to set the "
                f"desired logging mode at table-creation time: CREATE "
                f"{'TABLE' if mode == 'LOGGED' else 'UNLOGGED TABLE'} foo (...). If "
                f"toggling on an existing table is unavoidable, run it during a "
                f"maintenance window."
            ),
            suggested_fix=None,
        )


def _table_name(relation: Any) -> str:
    if relation is None:
        return ""
    schemaname = getattr(relation, "schemaname", None) or ""
    relname = getattr(relation, "relname", None) or ""
    return f"{schemaname}.{relname}" if schemaname else relname
