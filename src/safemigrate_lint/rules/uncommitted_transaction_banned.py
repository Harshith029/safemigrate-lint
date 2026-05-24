"""uncommitted-transaction-banned — squawk-equivalent: BEGIN without matching COMMIT.

Fires WARNING on every TransactionStmt(BEGIN/START) in a file whose
transaction-depth never returns to zero by end-of-file. An unmatched BEGIN
either (a) leaves the migration tool's auto-wrapping transaction in a
confused state, or (b) when run via psql/raw libpq, opens a transaction
that the migration framework can't commit, leaving the connection in an
aborted state until the framework forces a rollback.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import TransactionStmtKind

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "uncommitted-transaction-banned"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.TransactionStmt,),
    doc=(
        "A BEGIN or START TRANSACTION in a migration file should be matched by a "
        "COMMIT or ROLLBACK before end-of-file. Migration tools that auto-wrap each "
        "file in a transaction interact badly with manual BEGIN; raw libpq runners "
        "leave the connection in an unclosed-transaction state. If your migration "
        "tool wraps for you, remove the explicit BEGIN/COMMIT. If you need explicit "
        "control, make sure BEGIN and COMMIT/ROLLBACK are balanced in the file."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.TransactionStmt):
        return
    if stmt.kind not in (
        TransactionStmtKind.TRANS_STMT_BEGIN,
        TransactionStmtKind.TRANS_STMT_START,
    ):
        return
    if not state.has_unmatched_begin:
        return

    line, column = ctx.line_col()
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            "BEGIN/START TRANSACTION in this file has no matching COMMIT/ROLLBACK "
            "before end-of-file. The transaction is unclosed."
        ),
        help=(
            "An unclosed transaction in a migration file is ambiguous: if your "
            "migration tool wraps each file in its own transaction (Atlas, Sqitch, "
            "many ORM-driven migrators), the explicit BEGIN is a no-op NOTICE and "
            "the COMMIT you forgot would have closed the OUTER (tool-managed) "
            "transaction unexpectedly. If your tool runs via raw libpq, the BEGIN "
            "opens a transaction the tool can't close — the connection stays in "
            "an in-progress state until the framework forces a ROLLBACK. Either "
            "remove the BEGIN (rely on the wrapping transaction) or add the "
            "matching COMMIT."
        ),
        suggested_fix=None,
    )
