"""identifier-too-long — squawk identifier-too-long equivalent.

Postgres stores identifiers in a fixed 64-byte field (`NAMEDATALEN`), leaving 63
usable bytes. A longer name is not rejected — it is silently truncated, with at
most a notice. The object is created under a name you did not write.

That silence is the whole problem, and it is why this is worth a rule rather
than a style note:

* A later migration referring to the name you wrote fails to find the object,
  because the catalog holds the truncated form.
* Two names sharing a 63-byte prefix collapse onto the same identifier. The
  second statement then fails as a duplicate, or worse, silently replaces the
  first for objects created with OR REPLACE.
* Generated names inherit this. Postgres builds constraint and index names by
  concatenating table, column and suffix, so a long table plus a long column
  reaches the limit without any single name looking excessive.

Only names this statement *creates* are checked. A reference — `DROP TRIGGER
<long name>` — truncates to exactly the same 63 bytes the CREATE stored, so it
resolves to the right object and flagging it would be noise. squawk reports
those; verified here that both sides truncate identically.

Detecting it takes a detour. libpg_query applies the truncation itself while
parsing, so by the time a rule sees the AST the name is already 63 bytes and the
evidence is gone — a consequence of using Postgres's own parser rather than a
reimplementation. What survives is the raw SQL, so the check is: a name that
comes back at exactly the limit, immediately followed in the source by more
identifier characters, was truncated. A name that is legitimately 63 bytes has
nothing following it and is left alone.

Byte length, not character length: `NAMEDATALEN` counts bytes, so a non-ASCII
identifier hits the limit sooner than its character count suggests.

WARNING, not CRITICAL. The consequence is a migration that fails or an object
under an unexpected name — recoverable, and it surfaces at deploy time rather
than as an outage.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType

from ..core.ast_utils import qualified_name, table_name
from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "identifier-too-long"

#: Postgres NAMEDATALEN is 64; one byte is the terminator, leaving 63 usable.
MAX_IDENTIFIER_BYTES = 63


#: Characters Postgres accepts inside an unquoted identifier after the first.
_IDENT_TAIL = re.compile(r"[A-Za-z0-9_$]+")
#: Inside "double quotes" an identifier may hold anything but a quote — spaces
#: included, which is how policy names usually read.
_QUOTED_TAIL = re.compile(r'[^"]+')


def _was_truncated(name: str | None, sql: str) -> str | None:
    """Return the full identifier from the source if `name` was truncated.

    libpg_query truncates to NAMEDATALEN while parsing, so a name arriving at
    exactly the limit is the signal; the source says whether anything followed.
    Whether "anything" means identifier characters or arbitrary text depends on
    whether the name was quoted, so check what precedes the match.
    """
    if not name or len(name.encode("utf-8")) != MAX_IDENTIFIER_BYTES:
        return None
    for m in re.finditer(re.escape(name), sql):
        quoted = m.start() > 0 and sql[m.start() - 1] == '"'
        tail = (_QUOTED_TAIL if quoted else _IDENT_TAIL).match(sql, m.end())
        if tail:
            return name + tail.group(0)
    return None


def _names(stmt: Any) -> Iterator[tuple[str, str]]:
    """Yield (kind, identifier) for every name this statement creates."""
    if isinstance(stmt, ast.CreateStmt):
        yield "table", table_name(stmt.relation)
        for elt in stmt.tableElts or ():
            if isinstance(elt, ast.ColumnDef) and elt.colname:
                yield "column", elt.colname
            elif isinstance(elt, ast.Constraint) and elt.conname:
                yield "constraint", elt.conname
    elif isinstance(stmt, ast.IndexStmt):
        if stmt.idxname:
            yield "index", stmt.idxname
    elif isinstance(stmt, ast.CreateTrigStmt):
        if stmt.trigname:
            yield "trigger", stmt.trigname
    elif isinstance(stmt, ast.CreateSeqStmt):
        yield "sequence", table_name(stmt.sequence)
    elif isinstance(stmt, ast.ViewStmt):
        yield "view", table_name(stmt.view)
    elif isinstance(stmt, ast.CreateFunctionStmt):
        yield "function", qualified_name(stmt.funcname)
    elif isinstance(stmt, ast.CreatePolicyStmt):
        yield "policy", stmt.policy_name
    elif isinstance(stmt, ast.AlterTableStmt):
        for cmd in stmt.cmds or ():
            if not isinstance(cmd, ast.AlterTableCmd):
                continue
            if cmd.subtype == AlterTableType.AT_AddColumn and isinstance(cmd.def_, ast.ColumnDef):
                if cmd.def_.colname:
                    yield "column", cmd.def_.colname
            elif cmd.subtype == AlterTableType.AT_AddConstraint and isinstance(
                cmd.def_, ast.Constraint
            ):
                if cmd.def_.conname:
                    yield "constraint", cmd.def_.conname


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(
        ast.CreateStmt,
        ast.IndexStmt,
        ast.CreateTrigStmt,
        ast.CreateSeqStmt,
        ast.ViewStmt,
        ast.CreateFunctionStmt,
        ast.CreatePolicyStmt,
        ast.AlterTableStmt,
    ),
    doc=(
        "Postgres truncates identifiers longer than 63 bytes instead of rejecting "
        "them, so the object is created under a name you didn't write. Later "
        "migrations referencing the full name fail to find it, and two names sharing "
        "a 63-byte prefix collide. Matches squawk's identifier-too-long."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    line, column = ctx.line_col()

    for kind, name in _names(stmt):
        full = _was_truncated(name, ctx.sql)
        if full is None:
            continue
        n = len(full.encode("utf-8"))
        truncated = name
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                f"{kind} name is {n} bytes, over the 63-byte limit — Postgres will "
                f"silently truncate it to {truncated!r}."
            ),
            help=(
                "Postgres stores identifiers in a fixed 64-byte field (NAMEDATALEN), "
                "63 usable. It does not reject a longer name; it truncates and "
                "carries on, so the object exists under a name you did not write. "
                "A later migration that references the full name won't find it, and "
                "any other identifier sharing the same 63-byte prefix collides with "
                "it — failing as a duplicate, or silently replacing it for objects "
                "created with OR REPLACE. The limit counts bytes, not characters, so "
                "non-ASCII names reach it sooner than they look. Shorten the name to "
                "63 bytes or fewer, choosing a prefix that stays unique."
            ),
            suggested_fix=(
                f"-- Postgres will store this as:\n"
                f"--   {truncated}\n"
                f"-- Name it explicitly at 63 bytes or fewer so the catalog matches\n"
                f"-- what the migration says."
            ),
        )
