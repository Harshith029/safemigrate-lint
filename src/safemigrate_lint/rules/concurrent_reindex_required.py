"""concurrent-reindex-required — REINDEX without CONCURRENTLY locks the table.

Fires WARNING on `REINDEX TABLE foo` / `REINDEX INDEX idx` that omits the
CONCURRENTLY clause. Plain REINDEX takes AccessExclusiveLock for the duration
of the rebuild — minutes on a large table, blocking all reads and writes.
REINDEX CONCURRENTLY (Postgres 12+) builds a parallel index and swaps it,
with no write block.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "concurrent-reindex-required"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.ReindexStmt,),
    doc=(
        "REINDEX without CONCURRENTLY takes AccessExclusiveLock on the table for the "
        "rebuild duration — minutes on a large table, blocking reads and writes. "
        "Postgres 12+ supports REINDEX CONCURRENTLY which builds in the background "
        "without blocking writes. Use it on production tables."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.ReindexStmt):
        return
    if _is_concurrent(stmt):
        return

    line, column = ctx.line_col()
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            "REINDEX without CONCURRENTLY blocks reads and writes on the target for "
            "the duration of the rebuild."
        ),
        help=(
            "Plain REINDEX rebuilds the index in place under AccessExclusiveLock on "
            "the underlying table — minutes on a large table, blocking all queries. "
            "REINDEX CONCURRENTLY (added in Postgres 12) builds a new index "
            "in parallel, then atomically swaps it in. The CONCURRENTLY path is "
            "slower but doesn't block writes. Use it on any table that gets "
            "production traffic."
        ),
        suggested_fix=(
            "-- Postgres 12+:\n"
            "REINDEX TABLE CONCURRENTLY <table>;\n"
            "-- or:\n"
            "REINDEX INDEX CONCURRENTLY <index_name>;"
        ),
    )


def _is_concurrent(stmt: ast.ReindexStmt) -> bool:
    for p in stmt.params or ():
        if isinstance(p, ast.DefElem) and (p.defname or "").lower() == "concurrently":
            return True
    return False
