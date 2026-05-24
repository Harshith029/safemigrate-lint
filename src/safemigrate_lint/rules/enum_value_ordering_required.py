"""enum-value-ordering-required — ALTER TYPE ADD VALUE without BEFORE/AFTER appends.

Fires WARNING on `ALTER TYPE foo ADD VALUE 'new_val'` when neither BEFORE nor
AFTER is specified. Postgres appends the value at the end of the enum's sort
order, which may not match the author's mental model. If any code (queries,
ORM, application) sorts by the enum value's ordinal position, the new value
sorts unexpectedly. Specify BEFORE/AFTER to make the intent explicit.

Squawk has require-enum-value-ordering with the same trigger.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "enum-value-ordering-required"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.AlterEnumStmt,),
    doc=(
        "ALTER TYPE ADD VALUE without explicit BEFORE/AFTER appends at the end of the "
        "enum's sort order. If application code relies on the enum's value order "
        "(common with status enums sorted by progress), the new value sorts after "
        "everything, possibly violating that semantic. Always specify BEFORE or AFTER."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterEnumStmt):
        return
    # Only flag ADD VALUE (not RENAME VALUE or DROP VALUE).
    if not stmt.newVal:
        return
    if stmt.newValNeighbor:
        return  # BEFORE or AFTER was specified — author was explicit

    line, column = ctx.line_col()
    new_val = stmt.newVal or "<unknown>"
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            f"ALTER TYPE ADD VALUE '{new_val}' without BEFORE/AFTER appends at the end "
            f"of the enum's sort order — may surprise consumers that sort by enum order."
        ),
        help=(
            "Postgres enum values have an implicit sort order matching their definition "
            "order. ALTER TYPE ADD VALUE without BEFORE/AFTER appends the new value at "
            "the end. If your application sorts by enum ordinal (common for status "
            "enums where order matches workflow progress), the new value will sort "
            "after everything else — which may not be what you want. Specify BEFORE "
            "or AFTER an existing value to make the intent explicit."
        ),
        suggested_fix=(
            f"-- Specify the position explicitly:\n"
            f"ALTER TYPE <enum_name> ADD VALUE '{new_val}' AFTER '<existing_value>';\n"
            f"-- or:\n"
            f"ALTER TYPE <enum_name> ADD VALUE '{new_val}' BEFORE '<existing_value>';"
        ),
    )
