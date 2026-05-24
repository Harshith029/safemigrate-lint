"""text-over-varchar-preferred (STYLE, opt-in).

`varchar(n)` and `text` are functionally identical in Postgres (no performance
difference); `varchar(n)` adds a length check. If you don't have a hard
business rule requiring length limits, prefer `text` — simpler to refactor,
no surprise truncation errors on edge cases. Squawk emits prefer-text-field.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "text-over-varchar-preferred"


@register_rule(
    id=RULE_ID,
    severity=Severity.STYLE,
    applies_to=(ast.CreateStmt, ast.AlterTableStmt),
    doc="Prefer text over varchar(n) unless a hard length limit is required. Opt-in.",
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    line, column = ctx.line_col()
    columns: list[ast.ColumnDef] = []
    if isinstance(stmt, ast.CreateStmt):
        columns = [e for e in (stmt.tableElts or ()) if isinstance(e, ast.ColumnDef)]
    elif isinstance(stmt, ast.AlterTableStmt):
        for cmd in stmt.cmds or ():
            if isinstance(cmd, ast.AlterTableCmd) and cmd.subtype == AlterTableType.AT_AddColumn:
                if isinstance(cmd.def_, ast.ColumnDef):
                    columns.append(cmd.def_)
    for col in columns:
        if not _is_varchar(col):
            continue
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.STYLE,
            file=ctx.file,
            line=line,
            column=column,
            message=f"Column {col.colname} uses varchar(n); prefer text.",
            help=(
                "varchar(n) and text store data identically in Postgres; the only "
                "difference is varchar's length check, which raises an error on "
                "INSERT of a too-long value. text has no limit and refactors freely. "
                "Use varchar(n) only when a hard business rule (display constraints, "
                "external-system limits) requires the length cap."
            ),
        )


def _is_varchar(col: ast.ColumnDef) -> bool:
    if col.typeName is None:
        return False
    names = [getattr(n, "sval", "") for n in (col.typeName.names or ())]
    return bool(names) and names[-1].lower() in {"varchar", "character varying"}
