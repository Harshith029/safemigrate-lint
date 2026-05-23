"""rename-table-warning — RENAME TABLE breaks applications referencing the old name.

Same shape as rename-column-warning but for tables. The catalog rename is
fast; the pain is in application code, materialized views, and dependent
objects that hardcode the old table name.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import ObjectType

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "rename-table-warning"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.RenameStmt,),
    doc=(
        "RENAME TABLE is catalog-fast but breaks any code referencing the old table "
        "name. Coordinate with an app deploy that knows the new name, or use a view "
        "as a compatibility shim: CREATE VIEW old_name AS SELECT * FROM new_name;"
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.RenameStmt):
        return
    if stmt.renameType != ObjectType.OBJECT_TABLE:
        return

    old = stmt.relation.relname if stmt.relation else "<unknown>"
    new = stmt.newname or "<unknown>"
    line, col = ctx.line_col()

    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=col,
        message=(
            f"Renaming table {old} to {new} will break any code that still "
            f"references {old!r}."
        ),
        help=(
            "RENAME TABLE is catalog-fast (~milliseconds) but the impact is in app code, "
            "not the database: queries, ORM models, FK references in other tables, "
            "materialized views, and dependent functions that hardcode the old name break "
            "immediately when the migration runs. Coordinate the rename with an app deploy, "
            "or ship a compatibility view that aliases the old name to the new table."
        ),
        suggested_fix=(
            f"-- Compatibility-view pattern (gives app code time to migrate):\n"
            f"ALTER TABLE {old} RENAME TO {new};\n"
            f"CREATE VIEW {old} AS SELECT * FROM {new};\n"
            f"-- Drop the view after all callers have moved to the new name:\n"
            f"-- DROP VIEW {old};"
        ),
    )
