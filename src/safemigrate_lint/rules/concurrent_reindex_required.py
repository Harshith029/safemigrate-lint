"""concurrent-reindex-required — REINDEX without CONCURRENTLY locks the table.

Fires WARNING on `REINDEX TABLE foo` / `REINDEX INDEX idx` that omits the
CONCURRENTLY clause. Plain REINDEX holds ACCESS EXCLUSIVE on each index it
rebuilds and SHARE on the table, for the whole rebuild. SHARE blocks writes;
and because the planner locks every index of a table to plan a query on it,
reads stall too — measured on a real server, a read that didn't use the index
being rebuilt still waited. REINDEX CONCURRENTLY (Postgres 12+) builds a
replacement index and swaps it in, blocking neither.
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
        "REINDEX without CONCURRENTLY holds ACCESS EXCLUSIVE on the index being rebuilt "
        "and SHARE on its table for the whole rebuild — minutes on a large table. Writes "
        "wait, and since planning any query on the table locks all of its indexes, reads "
        "stall too. Postgres 12+ supports REINDEX CONCURRENTLY, which blocks neither. "
        "Use it on production tables."
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
            "Plain REINDEX rebuilds the index in place, holding ACCESS EXCLUSIVE on the "
            "index and SHARE on its table until it finishes — minutes on a large table. "
            "SHARE blocks writes, and reads stall too, because planning a query on the "
            "table needs a lock on every one of its indexes. REINDEX CONCURRENTLY "
            "(added in Postgres 12) builds a new index alongside, then swaps it in. "
            "It is slower but blocks neither reads nor writes; like other CONCURRENTLY "
            "forms it cannot run inside a transaction block. Use it on any table that "
            "gets production traffic."
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
