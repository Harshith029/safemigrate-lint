"""timeout-settings-required (STYLE, opt-in).

Squawk fires this rule on every DDL statement that isn't preceded by SET
statement_timeout / SET lock_timeout. The hygienic pattern is to set these
at the start of every migration so a runaway lock or statement doesn't hang
indefinitely.

V1 implementation: fire ONCE per file if the file contains DDL/DML but has
no SET statement_timeout or SET lock_timeout earlier in the same file.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "timeout-settings-required"


@register_rule(
    id=RULE_ID,
    severity=Severity.STYLE,
    applies_to=(
        ast.AlterTableStmt,
        ast.CreateStmt,
        ast.DropStmt,
        ast.IndexStmt,
        ast.UpdateStmt,
        ast.DeleteStmt,
        ast.CreateTrigStmt,
        ast.AlterEnumStmt,
        ast.ReindexStmt,
    ),
    doc=(
        "Recommend SET statement_timeout and SET lock_timeout at the top of every "
        "migration so a runaway operation doesn't hang indefinitely. Opt-in."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    # Fire on the first DDL/DML stmt in the file; check the SQL preceding it for
    # SET statement_timeout / SET lock_timeout. If neither is set, warn.
    preceding_sql = ctx.sql[: max(0, ctx.statement_offset - 1)].lower()
    if "set statement_timeout" in preceding_sql or "set lock_timeout" in preceding_sql:
        return

    # Only fire on the FIRST applicable stmt to avoid one warning per stmt.
    # We approximate this by checking: is there any earlier statement of the same
    # "needs timeout" class in the preceding SQL? If yes, this isn't the first; skip.
    # Heuristic: look for any of these keywords earlier; if not present, this is first.
    keywords_pattern = ("alter table", "create table", "drop table", "create index",
                        "drop index", "update ", "delete from", "create trigger",
                        "alter type", "reindex ")
    if any(kw in preceding_sql for kw in keywords_pattern):
        return  # not the first

    line, column = ctx.line_col()
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.STYLE,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            "Migration has no SET statement_timeout or SET lock_timeout. A runaway "
            "operation can hang indefinitely."
        ),
        help=(
            "Set statement_timeout and lock_timeout at the top of every migration "
            "file. statement_timeout aborts queries that run longer than the limit; "
            "lock_timeout aborts the wait for a lock rather than queueing forever. "
            "Both default to 0 (no timeout) which is dangerous for migrations on "
            "live tables."
        ),
        suggested_fix=(
            "-- At the top of the migration file:\n"
            "SET statement_timeout = '60s';\n"
            "SET lock_timeout = '10s';"
        ),
    )
