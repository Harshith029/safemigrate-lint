"""timestamptz-over-timestamp-preferred — squawk equivalent, opt-in STYLE.

Flags column definitions using `timestamp` (without time zone) where
`timestamptz` is usually wanted.

What the two types actually do, since this rule previously described it
backwards: `timestamp without time zone` stores the wall-clock fields it was
given and returns them unchanged — it ignores any zone on input and performs no
conversion on output. `timestamptz` is the one tied to the session timezone: it
resolves input to a UTC instant and converts back on read.

So the real problem with `timestamp` is not that values shift underneath you.
It's that the value doesn't identify a unique instant: the same wall-clock
reading means different moments in different zones, and repeats across a DST
fall-back. That makes it the wrong type for "when did this happen" — and the
*right* type for civil time that is deliberately zone-free: birthdays, business
hours, a 09:00 local appointment, a scheduling calendar.

That is why this is STYLE rather than a safety finding. Static analysis cannot
tell whether a column holds an instant or a civil time, so firing by default
turned a domain judgement into a warning on correct schemas. It was briefly
promoted to WARNING on the reasoning that the failure mode was silent data
corruption on read, which rested on the incorrect semantics above.

Enable it with `--severity=style`, or promote it per-project:

    [rules.style]
    enabled = ["timestamptz-over-timestamp-preferred"]

Covers both inline `CREATE TABLE` column definitions and `ALTER TABLE
ADD COLUMN` cases.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "timestamptz-over-timestamp-preferred"

_TIMESTAMP_TYPE_NAME = "timestamp"  # canonical pg_catalog name for `timestamp without time zone`


@register_rule(
    id=RULE_ID,
    severity=Severity.STYLE,
    applies_to=(ast.CreateStmt, ast.AlterTableStmt),
    doc=(
        "Columns defined as `timestamp` (without time zone) don't identify a unique "
        "instant: the same wall-clock reading means different moments in different "
        "zones and repeats across a DST fall-back. Use `timestamptz` for anything "
        "recording when something happened — same 8-byte storage. `timestamp` is the "
        "correct type for deliberately zone-free civil time (birthdays, business "
        "hours, local appointment times), so this is opt-in rather than default-on."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    line, column = ctx.line_col()
    columns_to_check: list[tuple[str, ast.ColumnDef]] = []

    if isinstance(stmt, ast.CreateStmt):
        table = table_name(stmt.relation)
        for elt in stmt.tableElts or ():
            if isinstance(elt, ast.ColumnDef):
                columns_to_check.append((table, elt))
    elif isinstance(stmt, ast.AlterTableStmt):
        table = table_name(stmt.relation)
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
            severity=Severity.STYLE,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"Column {table or '<table>'}.{col_name} uses `timestamp` (without "
                f"time zone), which does not identify a unique instant. Use "
                f"`timestamptz` if this records when something happened."
            ),
            help=(
                "`timestamp without time zone` stores the wall-clock fields it was "
                "given and returns them unchanged — it ignores any zone on input and "
                "does not convert on output. The problem isn't that values shift; it's "
                "that the value is ambiguous: the same reading denotes different "
                "moments in different zones, and repeats across a DST fall-back. "
                "`timestamptz` resolves input to a UTC instant and converts back on "
                "read, in the same 8 bytes. If this column deliberately holds civil "
                "time — a birthday, business hours, a 09:00 local appointment — then "
                "`timestamp` is the correct type and this finding does not apply. "
                "To convert: ALTER TABLE <t> ALTER COLUMN <c> TYPE timestamptz USING "
                "<c> AT TIME ZONE '<the zone the values were recorded in>' (this "
                "rewrites the table — use expand-contract on large ones)."
            ),
            suggested_fix=(
                f"-- Declare the column as timestamptz from the start. Inline in a\n"
                f"-- CREATE TABLE:  {col_name} timestamptz NOT NULL DEFAULT now()\n"
                f"ALTER TABLE {table or '<table>'} ADD COLUMN {col_name} timestamptz "
                f"NOT NULL DEFAULT now();"
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


