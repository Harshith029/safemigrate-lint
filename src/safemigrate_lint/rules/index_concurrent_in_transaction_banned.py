"""index-concurrent-in-transaction-banned — CREATE INDEX CONCURRENTLY cannot run inside a transaction.

Fires CRITICAL when a CREATE INDEX CONCURRENTLY appears in a file that also has
an explicit BEGIN/START TRANSACTION. Postgres rejects CREATE INDEX CONCURRENTLY
inside any explicit transaction with an error: the migration fails at execution
time. Most migration tools wrap each file in a transaction by default, so the
fix is either to move the CONCURRENTLY index to its own migration file or to
opt out of the wrapping transaction (e.g. Atlas `atlas:txmode none` directive).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "index-concurrent-in-transaction-banned"


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.IndexStmt,),
    doc=(
        "CREATE INDEX CONCURRENTLY cannot run inside an explicit transaction; "
        "Postgres errors out at execution time. Move the CONCURRENTLY index to its "
        "own migration file (most tools wrap each file in a transaction by default), "
        "or opt out of the transaction wrapping for this file (e.g. Atlas's "
        "`atlas:txmode none` directive)."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.IndexStmt):
        return
    if not stmt.concurrent:
        return
    # Only a transaction that is *open at this point* can contain the index
    # build. A BEGIN later in the file is irrelevant — flagging on that was a
    # false positive on a perfectly valid migration.
    if not state.in_explicit_transaction:
        return

    line, column = ctx.line_col()
    idx_name = stmt.idxname or "<unnamed>"
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.CRITICAL,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            f"CREATE INDEX CONCURRENTLY {idx_name} cannot run inside an explicit "
            f"transaction — the migration will fail at execution."
        ),
        help=(
            "Postgres rejects CREATE INDEX CONCURRENTLY inside any explicit "
            "transaction (BEGIN/START TRANSACTION) with: 'CREATE INDEX CONCURRENTLY "
            "cannot run inside a transaction block'. The migration fails immediately. "
            "Two fixes: (1) move the CONCURRENTLY index to its own migration file, so "
            "the tool's per-file transaction wrapping doesn't apply (the file just "
            "contains the one statement); (2) opt out of the wrapping for this file "
            "if your tool supports it (Atlas: `atlas:txmode none` directive at the top)."
        ),
        suggested_fix=(
            "-- Move CREATE INDEX CONCURRENTLY to its own migration file with no\n"
            "-- BEGIN/COMMIT block:\n"
            f"CREATE INDEX CONCURRENTLY {idx_name} ON <table> (<columns>);"
        ),
    )
