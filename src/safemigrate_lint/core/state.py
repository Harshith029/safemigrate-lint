"""Cross-statement state machine.

Tracks what this migration creates so rules can suppress false positives where
the premise of a warning ("FK validation scan on a populated table") doesn't
apply because the target was created in the same migration (empty).

Scope for v1: tables_created. Columns and indexes can be added as we wire
the rules that need them. See D1 Gap 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pglast import ast
from pglast.enums import TransactionStmtKind


@dataclass(slots=True)
class MigrationState:
    """Accumulated state from walking the migration's statements in order."""

    tables_created: set[str] = field(default_factory=set)
    # Indexes created in this migration — bare names only (Postgres index names
    # are unique within a schema and most rules compare bare).
    indexes_created: set[str] = field(default_factory=set)
    # True if any TransactionStmt with kind in {BEGIN, START} appears in this file.
    # Used by index-concurrent-in-transaction-banned (CREATE INDEX CONCURRENTLY
    # can't run inside an explicit transaction).
    has_explicit_transaction_begin: bool = False
    # Statement offsets (1-indexed byte offsets) where a BEGIN/START appears
    # while a prior transaction is still open. Used by transaction-nesting-banned
    # to fire only on the truly-nested BEGIN, not the outer one.
    nested_begin_statement_offsets: set[int] = field(default_factory=set)


def _table_name(relation: Any) -> str:
    """Extract canonical (possibly schema-qualified) table name from a RangeVar."""
    if relation is None:
        return ""
    schemaname = getattr(relation, "schemaname", None) or ""
    relname = getattr(relation, "relname", None) or ""
    if schemaname:
        return f"{schemaname}.{relname}"
    return relname


def _bare(name: str) -> str:
    """Drop the schema prefix from a qualified name."""
    return name.rsplit(".", 1)[-1] if "." in name else name


class StateBuilder:
    """Walks parsed statements in source order, populates a MigrationState."""

    @staticmethod
    def build(statements: list[Any]) -> MigrationState:
        state = MigrationState()
        tx_depth = 0
        for raw_stmt in statements:
            stmt = getattr(raw_stmt, "stmt", raw_stmt)
            raw_offset = getattr(raw_stmt, "stmt_location", None) or 0
            offset = raw_offset + 1

            if isinstance(stmt, ast.CreateStmt):
                name = _table_name(stmt.relation)
                if name:
                    state.tables_created.add(name)
            elif isinstance(stmt, ast.IndexStmt):
                if stmt.idxname:
                    state.indexes_created.add(stmt.idxname)
            elif isinstance(stmt, ast.TransactionStmt):
                if stmt.kind in (
                    TransactionStmtKind.TRANS_STMT_BEGIN,
                    TransactionStmtKind.TRANS_STMT_START,
                ):
                    state.has_explicit_transaction_begin = True
                    if tx_depth > 0:
                        state.nested_begin_statement_offsets.add(offset)
                    tx_depth += 1
                elif stmt.kind in (
                    TransactionStmtKind.TRANS_STMT_COMMIT,
                    TransactionStmtKind.TRANS_STMT_ROLLBACK,
                ):
                    tx_depth = max(0, tx_depth - 1)
        return state


def table_created_in_migration(state: MigrationState, target: str) -> bool:
    """Was a table with this name created in this migration?

    Matches both schema-qualified (`public.foo`) and bare (`foo`) forms in
    both directions — so a CREATE of `public.foo` will satisfy a query for
    bare `foo` and vice versa. Pragmatic for v1; tighten when we track
    search_path explicitly.
    """
    if target in state.tables_created:
        return True
    bare_target = _bare(target)
    bare_created = {_bare(t) for t in state.tables_created}
    return bare_target in bare_created
