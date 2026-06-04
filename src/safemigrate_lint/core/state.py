"""Cross-statement state machine.

Tracks what this migration creates so rules can suppress false positives where
the premise of a warning ("FK validation scan on a populated table") doesn't
apply because the target was created in the same migration (empty).

Tracked so far: tables (incl. CREATE TABLE AS / matviews) and indexes created,
plus transaction-block bookkeeping. Extend with more fields as rules need them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pglast import ast
from pglast.enums import TransactionStmtKind

from .ast_utils import bare, table_name


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
    # True if at the end of walking all statements, the transaction depth was
    # still > 0 — i.e. a BEGIN/START was not matched by a COMMIT/ROLLBACK. Used
    # by uncommitted-transaction-banned.
    has_unmatched_begin: bool = False


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
                name = table_name(stmt.relation)
                if name:
                    state.tables_created.add(name)
            elif isinstance(stmt, ast.CreateTableAsStmt):
                # CREATE TABLE AS / CREATE MATERIALIZED VIEW — name lives on the
                # IntoClause, not `relation`. Tracked alongside tables so rules
                # keyed on `table_created_in_migration` (e.g. refresh-matview-blocks-reads)
                # suppress on objects created earlier in the same migration.
                rel = stmt.into.rel if stmt.into else None
                if rel and rel.relname:
                    state.tables_created.add(table_name(rel))
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
        # Final imbalance check: any unclosed transaction at end-of-file.
        if tx_depth > 0:
            state.has_unmatched_begin = True
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
    bare_target = bare(target)
    bare_created = {bare(t) for t in state.tables_created}
    return bare_target in bare_created
