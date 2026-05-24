"""update-delete-row-scope — Bytebase statement.affected-row-limit equivalent.

Fires WARNING on every UPDATE and DELETE statement. Static analysis cannot
prove the WHERE clause is bounded, and unbounded UPDATE/DELETE statements are
one of the most common root causes of replication lag and connection-pool
exhaustion incidents. The rule defensively prompts the author to verify the
row scope.

Per D1 Gap 4: squawk has no rule for any UPDATE/DELETE; Atlas does not either;
only Bytebase covers this. We adopt the rule because the corpus and incident
literature both show this is a real production-hazard pattern that no other
free tool catches.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "update-delete-row-scope"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.UpdateStmt, ast.DeleteStmt),
    doc=(
        "UPDATE and DELETE statements affect an unbounded number of rows from the "
        "analyzer's perspective. Static analysis cannot determine if the WHERE clause "
        "is bounded — that depends on runtime data. Unbounded mass UPDATE/DELETE is a "
        "leading cause of replication lag and connection-pool exhaustion. Verify the "
        "expected row count; for large updates, batch with ctid IN (... LIMIT N) or "
        "use a separate batched-migration job. Matches Bytebase statement.affected-"
        "row-limit."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, (ast.UpdateStmt, ast.DeleteStmt)):
        return

    line, column = ctx.line_col()
    op = "UPDATE" if isinstance(stmt, ast.UpdateStmt) else "DELETE"
    table = _table_name(stmt.relation)

    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            f"{op} on {table or 'table'} — verify the WHERE clause is bounded; "
            f"unbounded mass mutations cause replication lag and pool exhaustion."
        ),
        help=(
            f"Static analysis cannot prove a WHERE clause bounds the row count. If the "
            f"{op} can match more than ~10K rows, batch the operation: identify the "
            f"target rows by primary key in chunks (ORDER BY pk LIMIT N), then loop. "
            f"Mass {op}s on big tables block replication for minutes, can exhaust the "
            f"connection pool by piling app writes behind row locks, and produce huge "
            f"WAL volume which lags replicas. If you have verified the WHERE clause "
            f"bounds the rows tightly (e.g. WHERE id = constant), suppress with "
            f"`-- safemigrate:ignore=update-delete-row-scope reason=\"bounded by PK\"`."
        ),
        suggested_fix=(
            f"-- Batched pattern for large {op}s:\n"
            f"DO $$\n"
            f"DECLARE batch_rows INT;\n"
            f"BEGIN\n"
            f"  LOOP\n"
            f"    WITH chunk AS (\n"
            f"      SELECT ctid FROM {table or 'table'} WHERE <condition> LIMIT 10000\n"
            f"    )\n"
            f"    {op} FROM {table or 'table'} WHERE ctid IN (SELECT ctid FROM chunk);\n"
            f"    GET DIAGNOSTICS batch_rows = ROW_COUNT;\n"
            f"    EXIT WHEN batch_rows = 0;\n"
            f"  END LOOP;\n"
            f"END $$;"
        ),
    )


def _table_name(relation: Any) -> str:
    if relation is None:
        return ""
    schemaname = getattr(relation, "schemaname", None) or ""
    relname = getattr(relation, "relname", None) or ""
    return f"{schemaname}.{relname}" if schemaname else relname
