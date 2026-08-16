"""Cross-statement state machine.

Tracks what a migration has done *so far* so rules can suppress false positives
where the premise of a warning doesn't apply — an FK validation scan is free on
a table this migration just created empty.

The engine advances this state as it walks statements in source order, and runs
a statement's rules *before* folding that statement in. A rule therefore only
ever sees facts established strictly before the statement it is judging.

That ordering is load-bearing. An earlier design computed every fact about the
file up front, which let rules read "created anywhere in this file" as "created
earlier, in this schema, and still empty" — three separate claims, none of them
proven. It silently suppressed real hazards: a blocking `DROP INDEX` went quiet
because the same name was re-created *later*, and a new `audit.users` hid a
dangerous ALTER on the existing `public.users`.

Two facts are inherently terminal and stay whole-file, computed in a prepass and
documented as such: whether the file ends inside an unclosed transaction, and
which BEGINs are nested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pglast import ast
from pglast.enums import TransactionStmtKind

from .ast_utils import table_name


@dataclass(frozen=True, slots=True)
class RelationId:
    """A relation's identity. `schema=None` means the name was written unqualified."""

    schema: str | None
    name: str

    @classmethod
    def parse(cls, dotted: str) -> RelationId:
        if not dotted:
            return cls(None, "")
        schema, _, name = dotted.rpartition(".")
        return cls(schema or None, name)


@dataclass(slots=True)
class MigrationState:
    """Facts established by the statements walked so far, plus whole-file totals."""

    # --- ordered: reflects only statements before the one being judged ---
    relations_created: set[RelationId] = field(default_factory=set)
    # Relations this migration has written rows into, so "created here" stops
    # implying "empty". CREATE TABLE AS and a populated matview count as data.
    relations_with_data: set[RelationId] = field(default_factory=set)
    # Bare names this migration created under an explicit non-public schema. An
    # unqualified reference to one of these can't be resolved without knowing
    # search_path, so it matches nothing rather than guessing.
    ambiguous_bare_names: set[str] = field(default_factory=set)
    indexes_created: set[str] = field(default_factory=set)
    # True while an explicit BEGIN/START is open at this point in the file.
    in_explicit_transaction: bool = False

    # --- whole-file: computed in a prepass, terminal by nature ---
    # Offsets of BEGINs issued while a transaction was already open. Postgres
    # warns and ignores these; they do not open a nested transaction.
    nested_begin_statement_offsets: set[int] = field(default_factory=set)
    # True if the file ends with a BEGIN/START unmatched by COMMIT/ROLLBACK.
    has_unmatched_begin: bool = False


def _relation_of(stmt: Any) -> RelationId | None:
    """The relation a statement creates, if it creates one."""
    if isinstance(stmt, ast.CreateStmt):
        name = table_name(stmt.relation)
        return RelationId.parse(name) if name else None
    if isinstance(stmt, ast.CreateTableAsStmt):
        # CREATE TABLE AS / CREATE MATERIALIZED VIEW — the name is on the
        # IntoClause, not `relation`.
        rel = stmt.into.rel if stmt.into else None
        name = table_name(rel) if rel is not None else ""
        return RelationId.parse(name) if name else None
    return None


class StateBuilder:
    """Builds a MigrationState incrementally, in source order."""

    @staticmethod
    def build(statements: list[Any]) -> MigrationState:
        """Start a walk: whole-file transaction facts only, no relations yet.

        Relation and index facts are added by `advance()` as the engine walks, so
        that a rule never sees a statement that hasn't run yet.
        """
        state = MigrationState()
        depth = 0
        for raw_stmt in statements:
            stmt = getattr(raw_stmt, "stmt", raw_stmt)
            if not isinstance(stmt, ast.TransactionStmt):
                continue
            offset = (getattr(raw_stmt, "stmt_location", None) or 0) + 1
            if stmt.kind in (
                TransactionStmtKind.TRANS_STMT_BEGIN,
                TransactionStmtKind.TRANS_STMT_START,
            ):
                if depth > 0:
                    # Postgres emits "there is already a transaction in progress"
                    # and leaves the transaction alone — the BEGIN is a no-op, so
                    # depth must not grow. Treating it as nesting made the next
                    # COMMIT look like it left a transaction open.
                    state.nested_begin_statement_offsets.add(offset)
                else:
                    depth = 1
            elif stmt.kind in (
                TransactionStmtKind.TRANS_STMT_COMMIT,
                TransactionStmtKind.TRANS_STMT_ROLLBACK,
            ):
                depth = 0
        state.has_unmatched_begin = depth > 0
        return state

    @staticmethod
    def advance(state: MigrationState, stmt: Any) -> None:
        """Fold one statement's effects in, after its rules have run."""
        created = _relation_of(stmt)
        if created is not None:
            state.relations_created.add(created)
            if created.schema not in (None, "public"):
                state.ambiguous_bare_names.add(created.name)
            if isinstance(stmt, ast.CreateTableAsStmt) and not _is_with_no_data(stmt):
                # CREATE TABLE AS / CREATE MATERIALIZED VIEW populate on creation.
                state.relations_with_data.add(created)
            return

        if isinstance(stmt, ast.IndexStmt):
            if stmt.idxname:
                state.indexes_created.add(stmt.idxname)
            return

        if isinstance(stmt, ast.TransactionStmt):
            if stmt.kind in (
                TransactionStmtKind.TRANS_STMT_BEGIN,
                TransactionStmtKind.TRANS_STMT_START,
            ):
                state.in_explicit_transaction = True
            elif stmt.kind in (
                TransactionStmtKind.TRANS_STMT_COMMIT,
                TransactionStmtKind.TRANS_STMT_ROLLBACK,
            ):
                state.in_explicit_transaction = False
            return

        target = _written_relation(stmt)
        if target is not None:
            state.relations_with_data.add(target)


def _is_with_no_data(stmt: Any) -> bool:
    into = getattr(stmt, "into", None)
    return bool(getattr(into, "skipData", False)) if into is not None else False


def _written_relation(stmt: Any) -> RelationId | None:
    """The relation a statement writes rows into, if any."""
    if isinstance(stmt, (ast.InsertStmt, ast.CopyStmt)):
        name = table_name(getattr(stmt, "relation", None))
        return RelationId.parse(name) if name else None
    return None


def _same_relation(created: RelationId, target: RelationId, ambiguous: set[str]) -> bool:
    """Do these two names denote the same relation?

    Exact identities match. An unqualified name resolves against the default
    search_path — so it matches `public.x` — but only while no other schema in
    this migration has claimed that bare name. Two different explicit schemas
    never match, which is what keeps a new `audit.users` from vouching for an
    existing `public.users`.
    """
    if created.name != target.name:
        return False
    if created.schema == target.schema:
        return True
    if created.schema is not None and target.schema is not None:
        return False  # two different explicit schemas
    if created.name in ambiguous:
        return False  # unqualified and genuinely ambiguous — don't guess
    return (created.schema or "public") == (target.schema or "public")


def table_created_in_migration(state: MigrationState, target: str) -> bool:
    """Did an earlier statement in this migration create this relation?

    Use this only when the question really is "does it exist yet" — e.g. a
    matview has no outside readers to block. When the premise is that the table
    is *empty*, use `table_known_empty`.
    """
    tid = RelationId.parse(target)
    return any(
        _same_relation(c, tid, state.ambiguous_bare_names) for c in state.relations_created
    )


def table_known_empty(state: MigrationState, target: str) -> bool:
    """Was this relation created earlier in this migration and left empty?

    This is the premise behind most suppression: a rewrite, constraint scan, or
    index build is free on a table with no rows. Creating a table doesn't prove
    it's empty — the migration may have inserted into it, or created it via
    CREATE TABLE AS — so those cases return False and the hazard is reported.
    """
    tid = RelationId.parse(target)
    ambiguous = state.ambiguous_bare_names
    if not any(_same_relation(c, tid, ambiguous) for c in state.relations_created):
        return False
    return not any(w for w in state.relations_with_data if _same_relation(w, tid, ambiguous))
