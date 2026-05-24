"""bigint-over-int-preferred (STYLE, opt-in).

Postgres `int` (`int4`) tops out at ~2.1 billion. On any table that could grow
to billions of rows or has hot inserts, `bigint` (`int8`) is the safer choice
from the start; the extra 4 bytes per row is negligible compared to the cost
of an INT → BIGINT type change later.

Squawk emits prefer-bigint-over-int with the same trigger. Opt-in because
many short-lived or naturally-bounded tables genuinely don't need bigint.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "bigint-over-int-preferred"
_INT_TYPE_NAMES = {"int4", "int", "integer"}


@register_rule(
    id=RULE_ID,
    severity=Severity.STYLE,
    applies_to=(ast.CreateStmt, ast.AlterTableStmt),
    doc="Prefer bigint over int for new columns. Opt-in via .safemigrate.toml.",
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    line, column = ctx.line_col()
    columns: list[ast.ColumnDef] = []
    if isinstance(stmt, ast.CreateStmt):
        for elt in stmt.tableElts or ():
            if isinstance(elt, ast.ColumnDef):
                columns.append(elt)
    elif isinstance(stmt, ast.AlterTableStmt):
        for cmd in stmt.cmds or ():
            if isinstance(cmd, ast.AlterTableCmd) and cmd.subtype == AlterTableType.AT_AddColumn:
                if isinstance(cmd.def_, ast.ColumnDef):
                    columns.append(cmd.def_)
    for col in columns:
        if not _is_int(col):
            continue
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.STYLE,
            file=ctx.file,
            line=line,
            column=column,
            message=f"Column {col.colname} uses int; prefer bigint for future-proofing.",
            help=(
                "int (int4) maxes at ~2.1 billion. A type change later requires a "
                "full table rewrite (see column-type-change-rewrites-table). The "
                "extra 4 bytes per row are negligible at any scale where rewrites "
                "matter."
            ),
        )


def _is_int(col: ast.ColumnDef) -> bool:
    if col.typeName is None:
        return False
    names = [getattr(n, "sval", "") for n in (col.typeName.names or ())]
    return bool(names) and names[-1].lower() in _INT_TYPE_NAMES
