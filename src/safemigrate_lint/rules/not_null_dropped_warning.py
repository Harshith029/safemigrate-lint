"""not-null-dropped-warning — squawk ban-drop-not-null equivalent.

Fires on `ALTER TABLE … ALTER COLUMN … DROP NOT NULL`.

The statement itself is cheap: catalog-only, effectively instant, no rewrite and
no meaningful lock. The cost lands elsewhere.

Every reader that has been treating the column as guaranteed-present may now
receive nulls — ORMs with non-optional fields, `NOT NULL` assumptions baked into
application types, downstream queries whose predicates silently stop matching.
None of that fails at migration time; it fails later, in code that was correct
until this ran.

It is also a one-way door in practice. Putting the constraint back means
`SET NOT NULL`, which scans the whole table and fails outright if any null
arrived in the meantime (see `nullable-to-non-nullable-may-fail`).

WARNING rather than CRITICAL: this is spelled out in the diff and deliberate.
A reviewer can see it. What they may not have considered is who was relying on
the guarantee.

Added after measuring recall against squawk over 2,497 real migrations from
cal.com, Mattermost, Supabase and Windmill — `DROP NOT NULL` appeared 45 times
across 29 files with no rule here to catch it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_known_empty
from ._registry import RuleContext, register_rule

RULE_ID = "not-null-dropped-warning"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER COLUMN DROP NOT NULL removes a guarantee readers may depend on. The "
        "statement is catalog-only and instant, but clients that treated the column "
        "as always-present can start receiving nulls, and restoring the constraint "
        "later requires a full table scan that fails if any null has arrived. "
        "Matches squawk's ban-drop-not-null."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    # A table created earlier in this migration and still empty has no existing
    # readers depending on the guarantee.
    if table and table_known_empty(state, table):
        return

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_DropNotNull:
            continue

        col = cmd.name or "<unnamed>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"DROP NOT NULL on {table or 'table'}.{col} lets nulls into a column "
                f"readers may assume is always present."
            ),
            help=(
                f"The ALTER itself is catalog-only and instant — the risk is what "
                f"depends on the guarantee. Application code, ORM models with a "
                f"non-optional field, and queries whose predicates assume a value "
                f"will keep compiling and start behaving differently once nulls "
                f"appear. Restoring the constraint is not symmetric: "
                f"ALTER COLUMN {col} SET NOT NULL scans the whole table and fails "
                f"outright if a single null arrived while it was relaxed. Before "
                f"merging, confirm every reader of {table or 'this table'}.{col} "
                f"handles null. If the column is genuinely optional now, suppress "
                f"with `-- safemigrate:ignore=not-null-dropped-warning reason=\"...\"`."
            ),
            suggested_fix=(
                f"-- If nulls are intended, no change is needed — confirm readers cope.\n"
                f"-- To reverse later (scans the table; fails on any existing null):\n"
                f"ALTER TABLE {table or '<table>'} ALTER COLUMN {col} SET NOT NULL;"
            ),
        )
