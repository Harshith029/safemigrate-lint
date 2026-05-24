"""char-column-banned (STYLE, opt-in).

`CHAR(n)` (`bpchar`) right-pads with spaces, which surprises comparisons and
joins. Use `text` or `varchar(n)` without trailing-space semantics. Squawk
emits ban-char-field for the same case.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "char-column-banned"


@register_rule(
    id=RULE_ID,
    severity=Severity.STYLE,
    applies_to=(ast.CreateStmt, ast.AlterTableStmt),
    doc="Avoid CHAR(n) — it pads with spaces. Use text or varchar(n). Opt-in.",
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
        if not _is_bpchar(col):
            continue
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.STYLE,
            file=ctx.file,
            line=line,
            column=column,
            message=f"Column {col.colname} uses CHAR(n) — pads with spaces; prefer text or varchar.",
            help=(
                "CHAR(n) (bpchar in Postgres) right-pads stored values with spaces to "
                "reach length n. Comparisons and joins get surprising results (a CHAR(5) "
                "containing 'foo  ' compares equal to 'foo' under = but not under like)."
            ),
        )


def _is_bpchar(col: ast.ColumnDef) -> bool:
    if col.typeName is None:
        return False
    names = [getattr(n, "sval", "") for n in (col.typeName.names or ())]
    return bool(names) and names[-1].lower() in {"bpchar", "char", "character"}
