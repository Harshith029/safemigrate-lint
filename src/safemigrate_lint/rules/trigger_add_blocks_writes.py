"""trigger-add-blocks-writes — Atlas PG308 equivalent.

CREATE TRIGGER on an existing table acquires `ShareRowExclusiveLock` on the
table for the duration of the catalog update. The duration is typically
milliseconds (CREATE TRIGGER does not scan rows — it just writes a catalog
row), so this is WARNING severity, not CRITICAL.

Atlas frames this as "notable operational concern rather than catastrophic"
per their own docs (verified 2026-05-22 via WebFetch of atlasgo.io/lint/analyzers).
The risk is cascading queue effects under heavy write contention: while the
brief catalog lock is held, new writes queue; if a long-running transaction
already holds RowExclusiveLock, CREATE TRIGGER waits behind it AND queues new
writes behind itself.

Suppressed on tables created in the same migration (no production load to
contend with).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "trigger-add-blocks-writes"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.CreateTrigStmt,),
    doc=(
        "CREATE TRIGGER on an existing table takes ShareRowExclusiveLock for the "
        "catalog update — milliseconds normally, but blocks all writes (INSERT, "
        "UPDATE, DELETE) for the duration. On a hot table this can queue writes "
        "behind any existing long-running transaction. Atlas PG308 (downgraded "
        "from CRITICAL to WARNING in safemigrate-lint per Atlas's own framing of "
        "this as 'notable operational concern rather than catastrophic')."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.CreateTrigStmt):
        return

    table = table_name(stmt.relation)
    if table and table_created_in_migration(state, table):
        return  # same-migration table — no production load to contend with

    line, column = ctx.line_col()
    trig_name = stmt.trigname or "<unnamed>"

    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            f"CREATE TRIGGER {trig_name} on {table or 'table'} blocks writes on the "
            f"table for the duration of the catalog update."
        ),
        help=(
            "CREATE TRIGGER takes ShareRowExclusiveLock on the target table for the "
            "catalog update. The operation itself is fast (no row scan — just a "
            "pg_trigger catalog write), but during the lock all INSERT/UPDATE/DELETE "
            "queries block. On a hot table this can cause cascading queue effects if "
            "any long-running transaction is already holding RowExclusiveLock: CREATE "
            "TRIGGER waits behind it, and meanwhile new writes queue behind CREATE "
            "TRIGGER. Run during off-peak, or on a brand-new table created in the same "
            "migration (which we suppress automatically)."
        ),
        suggested_fix=None,
    )


