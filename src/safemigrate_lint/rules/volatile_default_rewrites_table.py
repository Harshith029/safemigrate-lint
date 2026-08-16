"""volatile-default-rewrites-table — Atlas PG302 equivalent.

Postgres 11+ added a fast-path optimization for ADD COLUMN with a constant
(non-volatile) DEFAULT: the default is stored in the catalog and applied to
existing rows lazily on read. This makes the operation O(1) regardless of
table size.

VOLATILE defaults (random(), gen_random_uuid(), nextval(), etc.) do NOT
qualify for the fast path. Postgres must evaluate the function for every
existing row and write the result, requiring a full table rewrite under
AccessExclusiveLock.

The current-time family — now(), current_timestamp, transaction_timestamp(),
localtimestamp — is *not* in that set. Those are STABLE, not VOLATILE: they
return one value for the whole transaction, so the fast path applies and the
ALTER is metadata-only. The Postgres ALTER TABLE docs use `DEFAULT now()` as
the worked example of a non-rewriting default. Flagging it was a false
positive on one of the most common migrations there is, so the current-time
family is deliberately excluded. clock_timestamp() and timeofday() stay:
those advance within a transaction and really are VOLATILE.

We detect volatile defaults by matching the function name against a known
list of common volatile functions. False negatives possible on custom
VOLATILE user-defined functions (detecting those would need a pg_proc lookup,
i.e. a live DB connection, which is out of scope for static analysis).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType, ConstrType

from ..core.ast_utils import table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_known_empty
from ._registry import RuleContext, register_rule

RULE_ID = "volatile-default-rewrites-table"

# Postgres built-in functions marked VOLATILE in pg_proc that are commonly
# used as column DEFAULTs. Lowercased for case-insensitive matching.
# Source: Postgres documentation — Function Volatility Categories.
#
# Deliberately absent: now, current_timestamp, transaction_timestamp,
# localtimestamp, statement_timestamp. Those are STABLE — fixed for the whole
# transaction — so ADD COLUMN ... DEFAULT now() takes the PG11 fast path and
# does not rewrite. See STABLE_CURRENT_TIME_FUNCTIONS below.
KNOWN_VOLATILE_FUNCTIONS: frozenset[str] = frozenset({
    "random",
    "gen_random_uuid",
    "clock_timestamp",
    "timeofday",
    "nextval",
    "uuid_generate_v1",
    "uuid_generate_v1mc",
    "uuid_generate_v3",
    "uuid_generate_v4",
    "uuid_generate_v5",
    "txid_current",
    "pg_backend_pid",
})

# STABLE current-time functions: one value per transaction, so ADD COLUMN with
# one of these as DEFAULT takes the PG11 metadata-only fast path. Listed
# explicitly (rather than merely omitted) so a future edit can't quietly move
# one into the volatile set — `test_stable_time_functions_are_not_volatile`
# fails if the two sets ever overlap.
STABLE_CURRENT_TIME_FUNCTIONS: frozenset[str] = frozenset({
    "now",
    "current_timestamp",
    "current_date",
    "current_time",
    "localtimestamp",
    "localtime",
    "transaction_timestamp",
    "statement_timestamp",
})


@register_rule(
    id=RULE_ID,
    severity=Severity.CRITICAL,
    applies_to=(ast.AlterTableStmt,),
    doc=(
        "ALTER TABLE ADD COLUMN with a volatile DEFAULT (now(), random(), "
        "gen_random_uuid(), nextval(), etc.) triggers a full table rewrite under "
        "AccessExclusiveLock. Postgres 11+'s fast-path optimization only applies to "
        "constant defaults. Backfill in a separate, batched migration; add the "
        "column with no DEFAULT (or a constant placeholder) first. Matches Atlas PG302."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.AlterTableStmt):
        return

    table = table_name(stmt.relation)
    if table and table_known_empty(state, table):
        return  # same-migration table — empty, no rewrite cost

    line, column = ctx.line_col()

    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype != AlterTableType.AT_AddColumn:
            continue
        column_def = cmd.def_
        if not isinstance(column_def, ast.ColumnDef):
            continue

        volatile_name = _extract_volatile_funcname(column_def)
        if not volatile_name:
            continue

        col_name = column_def.colname or "<unnamed>"
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.CRITICAL,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"ADD COLUMN {col_name} on {table or 'table'} with volatile DEFAULT "
                f"{volatile_name}() triggers a full table rewrite under "
                f"AccessExclusiveLock."
            ),
            help=(
                "Postgres 11+ skips the table rewrite for ADD COLUMN when the DEFAULT "
                "is a constant — the default is stored in pg_attribute and applied "
                "lazily on read. This fast-path does NOT apply when the DEFAULT is a "
                f"volatile function ({volatile_name}() is volatile per pg_proc), "
                "because each row needs a unique value computed at write time. "
                "The rewrite scans every row and holds AccessExclusiveLock for the "
                "duration — minutes on a large table, blocking reads and writes. "
                "The safe pattern is: (1) ADD COLUMN with no DEFAULT (instant catalog "
                "update), (2) backfill in batched UPDATE migrations, (3) optionally "
                "ALTER COLUMN SET DEFAULT for future inserts."
            ),
            suggested_fix=(
                f"-- Three-step expand-contract:\n"
                f"-- 1. ALTER TABLE {table} ADD COLUMN {col_name} <type>;  -- no DEFAULT\n"
                f"-- 2. Backfill in batched UPDATE migrations:\n"
                f"--    UPDATE {table} SET {col_name} = {volatile_name}() "
                f"WHERE {col_name} IS NULL ORDER BY <pk> LIMIT N;\n"
                f"-- 3. Optionally set DEFAULT for new rows:\n"
                f"--    ALTER TABLE {table} ALTER COLUMN {col_name} "
                f"SET DEFAULT {volatile_name}();"
            ),
        )


def _extract_volatile_funcname(column_def: ast.ColumnDef) -> str:
    """If the column has a DEFAULT whose expression is a call to a known-volatile
    function, return the function's bare name. Otherwise return empty string."""
    for constraint in column_def.constraints or ():
        if not isinstance(constraint, ast.Constraint):
            continue
        if constraint.contype != ConstrType.CONSTR_DEFAULT:
            continue
        expr = constraint.raw_expr
        if not isinstance(expr, ast.FuncCall):
            continue
        funcname = _funcname(expr)
        if funcname and funcname.lower() in KNOWN_VOLATILE_FUNCTIONS:
            return funcname
    return ""


def _funcname(call: ast.FuncCall) -> str:
    parts: list[str] = []
    for p in call.funcname or ():
        sval = getattr(p, "sval", None)
        if sval:
            parts.append(sval)
    # For Postgres built-ins the name may be ('pg_catalog', 'now') or just ('now',).
    # We compare against the rightmost component.
    return parts[-1] if parts else ""


