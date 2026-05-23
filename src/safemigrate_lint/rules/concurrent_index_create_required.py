"""require-concurrent-index-creation — first rule, used to validate the pipeline.

Fires on CREATE INDEX statements that don't use CONCURRENTLY, except when the
index targets a table created in the same migration (empty table, no lock pain).

The suppression on same-migration tables is the cross-statement-context wedge
from D1 Gap 2; this is the rule that validates the state machine works.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "concurrent-index-create-required"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.IndexStmt,),
    doc=(
        "CREATE INDEX without CONCURRENTLY acquires AccessExclusiveLock on the table for "
        "the duration of the build, blocking writes. On large populated tables this can be "
        "minutes. Use CREATE INDEX CONCURRENTLY (which cannot run inside a transaction)."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.IndexStmt):
        return
    if stmt.concurrent:
        return

    target = _target_table(stmt)
    if target and table_created_in_migration(state, target):
        # Suppress: index target is a brand-new (empty) table — no lock pain.
        return

    line, col = ctx.line_col()
    idx_name = stmt.idxname or "<unnamed>"
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=col,
        message=(
            f"CREATE INDEX {idx_name} without CONCURRENTLY blocks writes on "
            f"{target or 'the target table'} for the duration of the build."
        ),
        help=(
            "Non-concurrent CREATE INDEX takes AccessExclusiveLock for the full build duration. "
            "On a populated table this blocks all writes (UPDATE/INSERT/DELETE) until the index "
            "is built — minutes on a large table. CREATE INDEX CONCURRENTLY uses a slower but "
            "non-blocking build path. It cannot run inside a transaction, so it must be its own "
            "migration statement."
        ),
        suggested_fix=(
            f"CREATE INDEX CONCURRENTLY {idx_name if idx_name != '<unnamed>' else 'idx_name'} "
            f"ON {target or 'table'} (...);"
        ),
    )


def _target_table(stmt: ast.IndexStmt) -> str:
    rel = stmt.relation
    if rel is None:
        return ""
    schemaname = getattr(rel, "schemaname", None) or ""
    relname = getattr(rel, "relname", None) or ""
    return f"{schemaname}.{relname}" if schemaname else relname
