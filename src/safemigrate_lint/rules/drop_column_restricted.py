"""drop-column-restricted — ALTER TABLE DROP COLUMN is irreversible data loss.

Fires CRITICAL on every `ALTER TABLE … DROP COLUMN …` statement. Even with
IF EXISTS, the data in the column (if any) is permanently lost once the
migration runs.

A future refinement could downgrade the finding when the column was added in a
recent unmerged migration (so no production data exists yet); for now the
inline-ignore comment is the escape hatch.
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

RULE_ID = "drop-column-restricted"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE DROP COLUMN is irreversible data loss on populated tables. "
        "If the column has rows, those values are unrecoverable post-migration. "
        "Suppress with an inline `-- safemigrate:ignore=drop-column-restricted "
        "reason=\"...\"` after confirming the column has no production data."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    line, col_offset = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_DropColumn:
            continue

        col = cmd.name or "<unnamed>"
        if_exists = " IF EXISTS" if getattr(cmd, "missing_ok", False) else ""
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=col_offset,
            message=(
                f"DROP COLUMN{if_exists} {col} on {table or 'table'} is irreversible "
                f"data loss."
            ),
            help=(
                "DROP COLUMN removes the column and all its data. There is no rollback "
                "path once the migration runs. If this column has production data, ensure "
                "you have a backup or are intentionally accepting the loss. To suppress, "
                "add `-- safemigrate:ignore=drop-column-restricted reason=\"...\"` on the "
                "line immediately preceding the ALTER TABLE statement."
            ),
            # Intentionally no suggested_fix: this rule is about destructive intent;
            # auto-suggesting a "fix" would obscure the danger.
            suggested_fix=None,
        )


