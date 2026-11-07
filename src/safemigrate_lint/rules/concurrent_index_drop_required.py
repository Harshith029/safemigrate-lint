"""concurrent-index-drop-required — DROP INDEX without CONCURRENTLY blocks reads and writes.

Fires WARNING on every `DROP INDEX …` that omits `CONCURRENTLY`. Non-concurrent
DROP INDEX acquires AccessExclusiveLock on the underlying table, blocking both
reads and writes for the duration. On a hot table, even a short lock can cause
cascading queue effects.

Suppressed when the index was created earlier in the same migration (transient
indexes in a single migration won't hit production load). "The same index"
means the same schema-qualified identity — an index lives in its table's
schema — so a same-named index created in another schema vouches for nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import ObjectType

from ..core.ast_utils import qualified_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState, index_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "concurrent-index-drop-required"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.DropStmt,),
    doc=(
        "DROP INDEX without CONCURRENTLY acquires AccessExclusiveLock on the table, "
        "blocking both reads and writes for the duration of the operation. Use DROP "
        "INDEX CONCURRENTLY (cannot run inside a transaction) on production tables."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.DropStmt):
        return
    if stmt.removeType != ObjectType.OBJECT_INDEX:
        return
    if stmt.concurrent:
        return

    line, column = ctx.line_col()

    for obj in stmt.objects or ():
        index_name = qualified_name(obj)

        # Suppress if this exact index was created earlier in this migration —
        # transient within-migration indexes don't see production load during
        # the lock. Matching on the bare name let `audit.idx` vouch for
        # `public.idx`.
        if index_name and index_created_in_migration(state, index_name):
            continue

        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"DROP INDEX {index_name or '<unknown>'} without CONCURRENTLY blocks "
                f"reads and writes on the underlying table."
            ),
            help=(
                "Non-concurrent DROP INDEX acquires AccessExclusiveLock on the table "
                "the index belongs to, blocking ALL queries (reads and writes) for the "
                "duration. Even brief locks on a hot table can cause queries to queue "
                "up and exhaust the connection pool. DROP INDEX CONCURRENTLY uses a "
                "non-blocking path; it cannot run inside a transaction, so it must be "
                "its own migration statement."
            ),
            suggested_fix=(f"DROP INDEX CONCURRENTLY {index_name or 'index_name'};"),
        )
