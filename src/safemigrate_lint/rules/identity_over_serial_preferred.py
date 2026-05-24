"""identity-over-serial-preferred (STYLE, opt-in).

`SERIAL` is Postgres legacy syntax that quietly creates a sequence with weird
ownership semantics (orphaned on table rename, not detected by pg_dump in some
edge cases). `GENERATED ALWAYS AS IDENTITY` is the SQL-standard equivalent
with cleaner ownership. Squawk emits prefer-identity for the same case.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "identity-over-serial-preferred"
_SERIAL_TYPE_NAMES = {"serial", "serial4", "serial8", "bigserial", "smallserial", "serial2"}


@register_rule(
    id=RULE_ID,
    severity=Severity.STYLE,
    applies_to=(ast.CreateStmt, ast.AlterTableStmt),
    doc="Prefer GENERATED ALWAYS AS IDENTITY over SERIAL. Opt-in.",
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
        if not _is_serial(col):
            continue
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.STYLE,
            file=ctx.file,
            line=line,
            column=column,
            message=f"Column {col.colname} uses SERIAL; prefer GENERATED ALWAYS AS IDENTITY.",
            help=(
                "SERIAL is Postgres-specific legacy syntax that creates an implicit "
                "sequence with quirky ownership. GENERATED ALWAYS AS IDENTITY is SQL-"
                "standard, has clean ownership semantics, and is harder to accidentally "
                "override (the ALWAYS form rejects INSERT-with-explicit-value)."
            ),
        )


def _is_serial(col: ast.ColumnDef) -> bool:
    if col.typeName is None:
        return False
    names = [getattr(n, "sval", "") for n in (col.typeName.names or ())]
    return bool(names) and names[-1].lower() in _SERIAL_TYPE_NAMES
