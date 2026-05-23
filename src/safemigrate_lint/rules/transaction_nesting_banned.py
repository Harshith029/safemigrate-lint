"""transaction-nesting-banned — BEGIN inside an open transaction silently behaves as a savepoint.

Postgres does not support nested transactions in the SQL standard sense. A
`BEGIN` issued while a transaction is already open emits a NOTICE and is
treated as a no-op (the outer transaction remains active). This is subtle:
the second-level COMMIT does NOT close the outer transaction, and the
subsequent statements may run with semantics the migration author did not
expect.

Fires WARNING on every BEGIN/START that the StateBuilder identified as nested
(depth > 0 when it appeared). The outer BEGIN is not flagged.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import TransactionStmtKind

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "transaction-nesting-banned"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.TransactionStmt,),
    doc=(
        "Postgres does not support nested transactions. A BEGIN issued while a "
        "transaction is already open emits a NOTICE and is treated as a no-op. The "
        "outer transaction stays active; the inner COMMIT does not close it. This "
        "leads to subtle bugs where statements after the inner COMMIT run with "
        "different transactional semantics than the author intended. Use "
        "SAVEPOINT/RELEASE if you really want nested-like behavior."
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
    if ctx.statement_offset not in state.nested_begin_statement_offsets:
        return

    line, column = ctx.line_col()
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            "Nested BEGIN inside an already-open transaction — Postgres treats this "
            "as a no-op NOTICE, not a real nested transaction."
        ),
        help=(
            "Postgres has no true nested transactions. A second BEGIN issued while a "
            "transaction is open emits 'WARNING: there is already a transaction in "
            "progress' and is silently dropped — the outer transaction stays active. "
            "The subsequent COMMIT in the file refers to the OUTER transaction, not "
            "the (nonexistent) inner one. If you want nested-like semantics, use "
            "SAVEPOINT name; ... RELEASE SAVEPOINT name; or ROLLBACK TO SAVEPOINT name;"
        ),
        suggested_fix=(
            "-- Use SAVEPOINT for nested-style behavior:\n"
            "SAVEPOINT sp_name;\n"
            "-- ... statements ...\n"
            "RELEASE SAVEPOINT sp_name;  -- or: ROLLBACK TO SAVEPOINT sp_name;"
        ),
    )
