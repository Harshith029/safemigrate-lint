"""constraint-not-valid-required — ADD CONSTRAINT without NOT VALID requires a full table scan.

Fires WARNING on `ALTER TABLE … ADD CONSTRAINT … CHECK (…)` or
`ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY …` when NOT VALID is missing.
Postgres validates the constraint against existing rows; on a large pre-existing
table this scan can take minutes. The lock differs by constraint type: a FOREIGN
KEY takes SHARE ROW EXCLUSIVE on both the referencing and referenced table, a
CHECK takes ACCESS EXCLUSIVE. The finding reports whichever applies.

Suppressed when an earlier statement in this migration created the table and
nothing has written to it since — an empty table costs nothing to validate.
NOT VALID is the Postgres mechanism for adding the constraint
immediately (catalog-only) and validating later with `VALIDATE CONSTRAINT`,
which uses a weaker ShareUpdateExclusiveLock.

UNIQUE constraints are intentionally excluded: Postgres doesn't allow NOT VALID
on UNIQUE (a unique constraint is enforced by a backing index). See
`unique-constraint-data-dependent` for that case.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType, ConstrType

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.lock_impact import ADD_CHECK_LOCK, ADD_FOREIGN_KEY_LOCK
from ..core.state import MigrationState, table_known_empty
from ._registry import RuleContext, register_rule

RULE_ID = "constraint-not-valid-required"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE ADD CONSTRAINT (CHECK or FOREIGN KEY) without NOT VALID requires "
        "a full table scan to validate against existing rows — holding SHARE ROW "
        "EXCLUSIVE for a FOREIGN KEY (on both tables) or ACCESS EXCLUSIVE for a "
        "CHECK. Use NOT VALID to add the constraint "
        "instantly (catalog-only), then run VALIDATE CONSTRAINT in a separate "
        "migration with the weaker ShareUpdateExclusiveLock."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    if table and table_known_empty(state, table):
        return  # empty table — no validation cost

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_AddConstraint:
            continue
        constraint = cmd.def_
        if not isinstance(constraint, ast.Constraint):
            continue
        if constraint.contype not in (ConstrType.CONSTR_CHECK, ConstrType.CONSTR_FOREIGN):
            continue
        if constraint.skip_validation:
            continue  # NOT VALID is present — rule satisfied

        is_check = constraint.contype == ConstrType.CONSTR_CHECK
        kind_label = "CHECK" if is_check else "FOREIGN KEY"
        constraint_name = constraint.conname or "<unnamed>"
        # The two constraint types take different locks, so the impact is set
        # per finding rather than once for the whole rule.
        lock = ADD_CHECK_LOCK if is_check else ADD_FOREIGN_KEY_LOCK
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"ADD CONSTRAINT {constraint_name} {kind_label} on {table or 'table'} "
                f"without NOT VALID requires a full table scan."
            ),
            help=(
                f"Postgres validates the {kind_label} against every existing row, holding "
                f"{lock.lock} for the entire scan — that blocks {lock.blocks}. On a large "
                f"pre-existing table the scan runs for minutes. The two-step pattern is "
                f"non-blocking: add the constraint with NOT VALID (catalog-only, "
                f"~millisecond lock), then VALIDATE CONSTRAINT in a separate migration "
                f"which uses ShareUpdateExclusiveLock (doesn't block reads or writes)."
            ),
            suggested_fix=(
                f"-- Two-step pattern (non-blocking):\n"
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
                f"{kind_label} (...) NOT VALID;\n"
                f"-- Then in a separate migration:\n"
                f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint_name};"
            ),
            lock_impact=lock,
        )


