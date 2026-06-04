"""refresh-matview-blocks-reads — REFRESH MATERIALIZED VIEW without CONCURRENTLY
locks the view against all reads for the duration of the refresh.

A plain REFRESH takes AccessExclusiveLock on the matview, so every query against
it blocks until the rebuild finishes — which on a large aggregate can be many
seconds to minutes. REFRESH ... CONCURRENTLY rebuilds without blocking readers
(it requires a UNIQUE index on the matview and cannot run inside a transaction
block).

Suppressed when the matview was created earlier in the same migration: it has no
readers yet, so blocking is moot. Matview creation parses as CreateTableAsStmt,
which `StateBuilder` records into `tables_created`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "refresh-matview-blocks-reads"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.RefreshMatViewStmt,),
    doc=(
        "REFRESH MATERIALIZED VIEW without CONCURRENTLY takes AccessExclusiveLock, "
        "blocking all reads of the view until the refresh completes. "
        "REFRESH ... CONCURRENTLY updates it without blocking readers (requires a "
        "UNIQUE index on the matview and cannot run inside a transaction block). "
        "Suppressed when the matview was created earlier in the same migration."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.RefreshMatViewStmt):
        return
    if stmt.concurrent:
        return  # already non-blocking

    name = stmt.relation.relname if stmt.relation else ""
    if name and table_created_in_migration(state, name):
        return  # created in this migration — no readers to block yet

    display = name or "<matview>"
    line, column = ctx.line_col()
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            f"REFRESH MATERIALIZED VIEW {display} without CONCURRENTLY blocks all "
            f"reads of the view until the refresh finishes."
        ),
        help=(
            "A plain REFRESH takes AccessExclusiveLock on the matview, so every query "
            "against it waits for the entire rebuild. REFRESH ... CONCURRENTLY rebuilds "
            "it without blocking readers. It requires a UNIQUE index on the matview and "
            "cannot run inside a transaction block. If a brief read stall is acceptable "
            "(e.g. an off-hours batch refresh), suppress with "
            "`-- safemigrate:ignore=refresh-matview-blocks-reads reason=\"...\"`."
        ),
        suggested_fix=f"REFRESH MATERIALIZED VIEW CONCURRENTLY {display};",
    )
