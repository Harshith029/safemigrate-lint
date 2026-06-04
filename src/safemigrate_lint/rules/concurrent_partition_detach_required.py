"""concurrent-partition-detach-required — DETACH PARTITION without CONCURRENTLY blocks queries.

Fires WARNING on `ALTER TABLE foo DETACH PARTITION bar` that omits the
CONCURRENTLY clause (Postgres 14+). Plain DETACH PARTITION takes
AccessExclusiveLock on the parent table — blocks all queries against any
partition for the duration. DETACH PARTITION CONCURRENTLY uses a weaker
lock and returns immediately, with a later FINALIZE step.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "concurrent-partition-detach-required"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE DETACH PARTITION without CONCURRENTLY (Postgres 14+) takes "
        "AccessExclusiveLock on the parent table for the duration of the detach, "
        "blocking queries against every partition (not just the one being detached). "
        "DETACH PARTITION CONCURRENTLY uses a weaker lock and returns immediately; "
        "complete with DETACH PARTITION ... FINALIZE later."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_DetachPartition:
            continue
        # Concurrent flag is on the PartitionCmd inside cmd.def_
        partition_cmd = cmd.def_
        if partition_cmd is not None and getattr(partition_cmd, "concurrent", False):
            continue  # CONCURRENTLY was specified

        partition_name = ""
        if partition_cmd is not None:
            partition_name = getattr(getattr(partition_cmd, "name", None), "relname", "") or ""
        parent = table_name(stmt.relation)

        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"DETACH PARTITION {partition_name or '<unknown>'} from "
                f"{parent or 'parent'} without CONCURRENTLY blocks queries on all "
                f"partitions for the duration."
            ),
            help=(
                "Plain DETACH PARTITION takes AccessExclusiveLock on the parent "
                "partitioned table — that means EVERY query against ANY partition "
                "blocks until the detach completes. Postgres 14+ supports DETACH "
                "PARTITION ... CONCURRENTLY which uses ShareUpdateExclusiveLock and "
                "returns immediately. The detach completes in a follow-up FINALIZE "
                "step. Use CONCURRENTLY on production partitioned tables."
            ),
            suggested_fix=(
                f"-- Postgres 14+:\n"
                f"ALTER TABLE {parent or 'parent'} DETACH PARTITION "
                f"{partition_name or 'partition'} CONCURRENTLY;\n"
                f"-- Later, in a separate transaction:\n"
                f"ALTER TABLE {parent or 'parent'} DETACH PARTITION "
                f"{partition_name or 'partition'} FINALIZE;"
            ),
        )


