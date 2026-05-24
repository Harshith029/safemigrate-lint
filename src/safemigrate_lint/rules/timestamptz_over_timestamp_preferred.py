"""timestamptz-over-timestamp-preferred — squawk equivalent, promoted to SAFETY.

Fires WARNING on column definitions that use `timestamp` (without time zone)
instead of `timestamptz` (`timestamp with time zone`). Storing local time
without timezone is a recurring source of subtle bugs: DST transitions
silently shift values, cross-region deployments interpret timestamps in the
server's local zone, and JOINs across tables with mixed types compare
apples-to-oranges.

Promoted from STYLE to SAFETY (2026-05-22 correction) because the failure
mode is silent data corruption, not visible errors.

Covers both inline `CREATE TABLE` column definitions and `ALTER TABLE
ADD COLUMN` cases.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "timestamptz-over-timestamp-preferred"

_TIMESTAMP_TYPE_NAME = "timestamp"  # canonical pg_catalog name for `timestamp without time zone`


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.CreateStmt, ast.AlterTableStmt),
    doc=(
        "Columns defined as `timestamp` (without time zone) silently store local time. "
        "DST transitions, cross-region deployments, and JOINs across timestamp/"
        "timestamptz mixes produce subtle bugs. Use `timestamptz` (timestamp with time "
        "zone) — same storage size, always UTC internally, properly converted on "
        "read. Promoted from STYLE to SAFETY 2026-05-22."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    line, column = ctx.line_col()
    columns_to_check: list[tuple[str, ast.ColumnDef]] = []

    if isinstance(stmt, ast.CreateStmt):
        table = _table_name(stmt.relation)
        for elt in stmt.tableElts or ():
            if isinstance(elt, ast.ColumnDef):
                columns_to_check.append((table, elt))
    elif isinstance(stmt, ast.AlterTableStmt):
        table = _table_name(stmt.relation)
        for cmd in stmt.cmds or ():
            if not isinstance(cmd, ast.AlterTableCmd):
                continue
            if cmd.subtype != AlterTableType.AT_AddColumn:
                continue
            if isinstance(cmd.def_, ast.ColumnDef):
                columns_to_check.append((table, cmd.def_))
    else:
        return

    for table, column_def in columns_to_check:
        if not _is_timestamp_without_tz(column_def):
            continue
        col_name = column_def.colname or "<unnamed>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"Column {table or '<table>'}.{col_name} uses `timestamp` (without "
                f"time zone). Use `timestamptz` instead to avoid silent DST and "
                f"cross-region bugs."
            ),
            help=(
                "Postgres `timestamp` (without time zone) stores the literal wall-clock "
                "value with no timezone info. On read it's interpreted in the session's "
                "timezone, which differs across regions, OS configurations, and DST "
                "transitions — values silently shift on the boundary. `timestamptz` "
                "stores as UTC internally and converts on read; same 8-byte storage. "
                "Convert with: ALTER TABLE <t> ALTER COLUMN <c> TYPE timestamptz USING "
                "<c> AT TIME ZONE 'UTC' (note: this rewrites the table — use the "
                "expand-contract pattern on large tables)."
            ),
            suggested_fix=(
                f"-- Define new columns as timestamptz from the start:\n"
                f"{col_name} timestamptz NOT NULL DEFAULT now()"
            ),
        )


def _is_timestamp_without_tz(column_def: ast.ColumnDef) -> bool:
    """Return True if the column's type resolves to Postgres `timestamp without time zone`.
    Returns False for `timestamptz` and any non-timestamp type."""
    tn = column_def.typeName
    if tn is None:
        return False
    names = [getattr(n, "sval", "") for n in (tn.names or ())]
    if not names:
        return False
    last = names[-1]
    # Postgres canonicalizes `timestamp without time zone` → 'timestamp',
    # and `timestamp with time zone` → 'timestamptz'.
    return last == _TIMESTAMP_TYPE_NAME


def _table_name(relation: Any) -> str:
    if relation is None:
        return ""
    schemaname = getattr(relation, "schemaname", None) or ""
    relname = getattr(relation, "relname", None) or ""
    return f"{schemaname}.{relname}" if schemaname else relname
