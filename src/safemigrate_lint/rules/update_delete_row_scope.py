"""update-delete-row-scope — Bytebase statement.affected-row-limit equivalent.

Fires WARNING on an UPDATE or DELETE with **no WHERE clause**. That statement
touches every row in the table — not as an estimate, but by definition — which
is the case where mass mutation is certain rather than suspected.

It used to fire on every UPDATE and DELETE, on the reasoning that static
analysis can't prove a WHERE clause is bounded. True, but a rule that fires on
100% of a construct carries no information: the reader can't tell the dangerous
one from the other twenty, so they suppress the rule and lose the signal
entirely. Firing only where the answer is certain is worth more than flagging a
category.

A bounded-looking WHERE can still match millions of rows, and this will not
catch that. Row counts are data-dependent and out of reach without a database
connection; the help text says so rather than implying coverage that isn't
there.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "update-delete-row-scope"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.UpdateStmt, ast.DeleteStmt),
    doc=(
        "UPDATE or DELETE with no WHERE clause rewrites or removes every row in the "
        "table. On a large table that means a long transaction, row locks held "
        "throughout, and a WAL burst that lags replicas. Add a WHERE clause, or batch "
        "the mutation in chunks that commit individually. A statement that *has* a "
        "WHERE clause is not flagged — its row count depends on data this analyzer "
        "cannot see. Narrower than Bytebase statement.affected-row-limit, which needs "
        "a live connection to count rows."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, (ast.UpdateStmt, ast.DeleteStmt)):
        return

    # A WHERE clause makes the row count data-dependent, and this analyzer has no
    # database to ask. Guessing there produced a finding on every mutation; the
    # no-WHERE case is the one we can state as fact.
    if stmt.whereClause is not None:
        return

    line, column = ctx.line_col()
    op = "UPDATE" if isinstance(stmt, ast.UpdateStmt) else "DELETE"
    table = table_name(stmt.relation)

    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            f"{op} on {table or 'table'} has no WHERE clause — it touches every "
            f"row in the table."
        ),
        help=(
            f"With no WHERE clause this {op} touches every row, so the cost scales with "
            f"the whole table rather than with what you meant to change. On a large "
            f"table that means one long transaction holding row locks throughout, and a "
            f"WAL burst that lags replicas for as long as it takes to replay. Either "
            f"add a WHERE clause, or batch the mutation into chunks that each commit. "
            f"Note this rule only fires when the statement has no WHERE at all — a "
            f"WHERE that still matches millions of rows is not detectable without a "
            f"database connection, so passing this check is not evidence the row count "
            f"is small. If rewriting every row is the intent (a small lookup table, or "
            f"a table this migration just created), suppress with "
            f"`-- safemigrate:ignore=update-delete-row-scope reason=\"...\"`."
        ),
        suggested_fix=_batched_fix(op, table or "<table>"),
    )


def _batched_fix(op: str, table: str) -> str:
    """One chunk of a batched mutation, to be driven by the caller.

    Deliberately not a DO block. A DO block runs inside a single transaction, so
    looping in one wouldn't shorten the transaction, wouldn't release row locks
    between chunks, and wouldn't stop the WAL burst — it would leave every
    problem this rule warns about in place while looking like a fix.

    Angle-bracketed parts are placeholders for the author to fill in.
    """
    mutation = (
        f"UPDATE {table} SET <assignments>" if op == "UPDATE" else f"DELETE FROM {table}"
    )
    return (
        f"-- Run this one chunk at a time from your migration runner or a script,\n"
        f"-- committing after each, until it reports 0 rows. Batching only helps if\n"
        f"-- each chunk is its own transaction — a DO $$ ... $$ loop is still one\n"
        f"-- transaction and relieves none of the lock or replication pressure.\n"
        f"{mutation}\n"
        f"WHERE ctid IN (\n"
        f"  SELECT ctid FROM {table} WHERE <condition> LIMIT 10000\n"
        f");"
    )


