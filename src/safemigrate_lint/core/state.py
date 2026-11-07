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

Suppression rests on two claims, and each is only made when it is established:

*Identity* — that a name in this statement denotes the relation created
earlier. An unqualified name resolves through search_path: under the default
path it means `public`, unless this migration also created that bare name in
another schema, in which case it is ambiguous and matches nothing. After
`SET search_path` (or `set_config('search_path', ...)`) it means the path's
first schema; when that can't be known — an empty or computed path — it
matches nothing. Indexes live in their table's schema and are tracked the same
way. DROP, RENAME and SET SCHEMA move or end an identity.

*Emptiness* — that nothing has written rows into it since. INSERT, COPY FROM
and MERGE count, including inside a WITH clause, as do CREATE TABLE AS, a
populating REFRESH, and attaching a populated partition or inheritance child.
A write to a name that might denote a created table counts against it.
Procedural code is opaque: a DO block that can write, CALL, or EXECUTE counts
as writing to every table created before it. Writes nothing here can see — a
function called from a SELECT, a trigger, a rule — are not modeled, and
`CREATE TABLE IF NOT EXISTS` is taken at its word.

Two facts are inherently terminal and stay whole-file, computed in a prepass and
documented as such: which BEGIN, if any, leaves the file inside an unclosed
transaction, and which BEGINs are nested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pglast import ast
from pglast.enums import AlterTableType, ObjectType, TransactionStmtKind, VariableSetKind

from .ast_utils import iter_nodes, qualified_name, table_name

DEFAULT_SCHEMA = "public"


@dataclass(frozen=True, slots=True)
class RelationId:
    """A relation's identity.

    `schema=None` means the name was written unqualified while the default
    search_path was in effect — i.e. `public`, unless ambiguous.
    """

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
    # The created relations this migration has (or may have) written rows into,
    # so "created here" stops implying "empty".
    relations_with_data: set[RelationId] = field(default_factory=set)
    # Bare names this migration created under a schema other than public. An
    # unqualified reference to one of these can't be resolved without knowing
    # the real search_path, so it matches nothing rather than guessing.
    ambiguous_bare_names: set[str] = field(default_factory=set)
    # Indexes created here, identified like relations (an index lives in its
    # table's schema) rather than by bare name.
    indexes_created: set[RelationId] = field(default_factory=set)
    # True while an explicit BEGIN/START is open at this point in the file.
    in_explicit_transaction: bool = False
    # The schema an unqualified name resolves to first. None once this file
    # sets a search_path that can't be resolved against (empty or computed).
    search_path_head: str | None = DEFAULT_SCHEMA

    # --- whole-file: computed in a prepass, terminal by nature ---
    # Offsets of BEGINs issued while a transaction was already open. Postgres
    # warns and ignores these; they do not open a nested transaction.
    nested_begin_statement_offsets: set[int] = field(default_factory=set)
    # Offset of the BEGIN/START that opened the transaction still open at
    # end-of-file, or None if the file ends outside a transaction.
    unmatched_begin_offset: int | None = None

    @property
    def has_unmatched_begin(self) -> bool:
        """True if the file ends inside a transaction it opened."""
        return self.unmatched_begin_offset is not None


_RELATION_OBJECTS = frozenset(
    {
        ObjectType.OBJECT_TABLE,
        ObjectType.OBJECT_VIEW,
        ObjectType.OBJECT_MATVIEW,
        ObjectType.OBJECT_FOREIGN_TABLE,
    }
)
_OPENS = frozenset({TransactionStmtKind.TRANS_STMT_BEGIN, TransactionStmtKind.TRANS_STMT_START})
_CLOSES = frozenset(
    {
        TransactionStmtKind.TRANS_STMT_COMMIT,
        TransactionStmtKind.TRANS_STMT_ROLLBACK,
        TransactionStmtKind.TRANS_STMT_PREPARE,  # dissociates it from the session
    }
)
# Statements whose own execution can write rows, possibly in a nested
# data-modifying WITH clause. Definitions (CREATE FUNCTION ... BEGIN ATOMIC,
# CREATE RULE) also contain DML nodes, but defining them runs nothing.
_EXECUTES_DML = (
    ast.InsertStmt,
    ast.UpdateStmt,
    ast.DeleteStmt,
    ast.MergeStmt,
    ast.SelectStmt,
    ast.CopyStmt,
    ast.ExplainStmt,
)
# Words that let a DO block write rows, directly or through code we can't see.
_DO_BLOCK_WRITE = re.compile(r"\b(insert|merge|copy|execute|call|perform)\b", re.IGNORECASE)


def _ends_transaction(stmt: ast.TransactionStmt) -> bool:
    # COMMIT AND CHAIN / ROLLBACK AND CHAIN start a new transaction at once.
    return stmt.kind in _CLOSES and not getattr(stmt, "chain", False)


class StateBuilder:
    """Builds a MigrationState incrementally, in source order."""

    @staticmethod
    def build(statements: list[Any]) -> MigrationState:
        """Start a walk: whole-file transaction facts only, no relations yet.

        Relation and index facts are added by `advance()` as the engine walks, so
        that a rule never sees a statement that hasn't run yet.
        """
        state = MigrationState()
        open_at: int | None = None
        for raw_stmt in statements:
            stmt = getattr(raw_stmt, "stmt", raw_stmt)
            if not isinstance(stmt, ast.TransactionStmt):
                continue
            offset = (getattr(raw_stmt, "stmt_location", None) or 0) + 1
            if stmt.kind in _OPENS:
                if open_at is not None:
                    # Postgres emits "there is already a transaction in progress"
                    # and leaves the transaction alone — the BEGIN is a no-op, so
                    # it must not count as opening anything. Treating it as
                    # nesting made the next COMMIT look like it left a
                    # transaction open.
                    state.nested_begin_statement_offsets.add(offset)
                else:
                    open_at = offset
            elif _ends_transaction(stmt):
                open_at = None
        state.unmatched_begin_offset = open_at
        return state

    @staticmethod
    def advance(state: MigrationState, stmt: Any) -> None:
        """Fold one statement's effects in, after its rules have run."""
        if isinstance(stmt, ast.TransactionStmt):
            if stmt.kind in _OPENS:
                state.in_explicit_transaction = True
            elif _ends_transaction(stmt):
                state.in_explicit_transaction = False
            return

        if isinstance(stmt, ast.VariableSetStmt):
            changed, head = _search_path_from_set(stmt)
            if changed:
                state.search_path_head = head
            return

        if isinstance(stmt, (ast.CreateStmt, ast.CreateTableAsStmt)):
            _fold_create(state, stmt)
            return

        if isinstance(stmt, ast.IndexStmt):
            table = _resolve(state, table_name(stmt.relation))
            if stmt.idxname and table is not None:
                _record(state, state.indexes_created, RelationId(table.schema, stmt.idxname))
            return

        if isinstance(stmt, ast.DropStmt):
            _fold_drop(state, stmt)
            return

        if isinstance(stmt, ast.RenameStmt):
            _fold_rename(state, stmt)
            return

        if isinstance(stmt, ast.AlterObjectSchemaStmt):
            _fold_set_schema(state, stmt)
            return

        if isinstance(stmt, ast.AlterTableStmt):
            _fold_attach(state, stmt)
            return

        if isinstance(stmt, ast.RefreshMatViewStmt):
            if not stmt.skipData:
                _taint(state, table_name(stmt.relation))
            return

        if isinstance(stmt, _EXECUTES_DML):
            for node in iter_nodes(stmt):
                if isinstance(node, (ast.InsertStmt, ast.MergeStmt)) or (
                    isinstance(node, ast.CopyStmt) and node.is_from
                ):
                    _taint(state, table_name(node.relation))
                elif isinstance(node, ast.FuncCall):
                    changed, head = _search_path_from_set_config(node)
                    if changed:
                        state.search_path_head = head
            return

        if _is_opaque(stmt):
            # Can't see what it wrote, so nothing created so far is provably empty.
            state.relations_with_data |= state.relations_created


# --- identity -----------------------------------------------------------------


def _resolve(state: MigrationState, dotted: str) -> RelationId | None:
    """The identity `dotted` denotes at this point in the file, or None if the
    search_path makes it unknowable."""
    rid = RelationId.parse(dotted)
    if not rid.name:
        return None
    if rid.schema is not None:
        return rid
    head = state.search_path_head
    if head is None:
        return None
    return rid if head == DEFAULT_SCHEMA else RelationId(head, rid.name)


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
    return (created.schema or DEFAULT_SCHEMA) == (target.schema or DEFAULT_SCHEMA)


def _may_denote(entry: RelationId, written: RelationId) -> bool:
    """Could `written`, as it appears in the SQL, refer to `entry`?

    The permissive counterpart of `_same_relation`, used for writes: claiming a
    table is empty must be proven, so a write only has to be possible to count.
    An unqualified name could resolve to any schema on some search_path.
    """
    if entry.name != written.name:
        return False
    if written.schema is None or entry.schema == written.schema:
        return True
    return entry.schema is None and written.schema == DEFAULT_SCHEMA


def _record(state: MigrationState, entries: set[RelationId], rid: RelationId) -> None:
    entries.add(rid)
    if rid.schema not in (None, DEFAULT_SCHEMA):
        state.ambiguous_bare_names.add(rid.name)


def _matching(
    entries: set[RelationId], target: RelationId, ambiguous: set[str]
) -> list[RelationId]:
    return [e for e in entries if _same_relation(e, target, ambiguous)]


# --- folding statements in ----------------------------------------------------


def _fold_create(state: MigrationState, stmt: Any) -> None:
    rel = stmt.relation if isinstance(stmt, ast.CreateStmt) else getattr(stmt.into, "rel", None)
    if rel is None:
        return
    if getattr(rel, "relpersistence", None) == "t":
        # Temporary relations live in pg_temp whatever the search_path says.
        rid: RelationId | None = RelationId("pg_temp", rel.relname or "")
    else:
        rid = _resolve(state, table_name(rel))
    if rid is None or not rid.name:
        return  # can't tell which relation this is, so it vouches for nothing
    _record(state, state.relations_created, rid)
    if isinstance(stmt, ast.CreateTableAsStmt) and not _is_with_no_data(stmt):
        # CREATE TABLE AS / CREATE MATERIALIZED VIEW populate on creation.
        state.relations_with_data.add(rid)


def _fold_drop(state: MigrationState, stmt: ast.DropStmt) -> None:
    sets: tuple[set[RelationId], ...]
    if stmt.removeType in _RELATION_OBJECTS:
        sets = (state.relations_created, state.relations_with_data)
    elif stmt.removeType == ObjectType.OBJECT_INDEX:
        sets = (state.indexes_created,)
    else:
        return
    for obj in stmt.objects or ():
        rid = _resolve(state, qualified_name(obj))
        if rid is None:
            continue
        for entries in sets:
            entries.difference_update(_matching(entries, rid, state.ambiguous_bare_names))


def _fold_rename(state: MigrationState, stmt: ast.RenameStmt) -> None:
    sets: tuple[set[RelationId], ...]
    if stmt.renameType in _RELATION_OBJECTS:
        sets = (state.relations_created, state.relations_with_data)
    elif stmt.renameType == ObjectType.OBJECT_INDEX:
        sets = (state.indexes_created,)
    else:
        return  # columns, constraints, ... keep the relation's identity
    new_name = stmt.newname or ""
    old = _resolve(state, table_name(stmt.relation))
    for entries in sets:
        if old is None:
            # Unknown which relation now answers to the new name: forget it.
            entries.difference_update({e for e in entries if e.name == new_name})
            continue
        entries.difference_update(
            _matching(entries, RelationId(old.schema, new_name), state.ambiguous_bare_names)
        )
        for entry in _matching(entries, old, state.ambiguous_bare_names):
            entries.discard(entry)
            _record(state, entries, RelationId(entry.schema, new_name))


def _fold_set_schema(state: MigrationState, stmt: ast.AlterObjectSchemaStmt) -> None:
    if stmt.objectType not in _RELATION_OBJECTS or not stmt.newschema:
        return
    old = _resolve(state, table_name(stmt.relation))
    if old is None:
        return
    for entries in (state.relations_created, state.relations_with_data):
        for entry in _matching(entries, old, state.ambiguous_bare_names):
            entries.discard(entry)
            _record(state, entries, RelationId(stmt.newschema, entry.name))


def _fold_attach(state: MigrationState, stmt: ast.AlterTableStmt) -> None:
    """A parent stops being empty when it gains a child that has rows."""
    for cmd in stmt.cmds or ():
        if not isinstance(cmd, ast.AlterTableCmd):
            continue
        if cmd.subtype == AlterTableType.AT_AttachPartition and isinstance(
            cmd.def_, ast.PartitionCmd
        ):
            parent, child = table_name(stmt.relation), table_name(cmd.def_.name)
        elif cmd.subtype == AlterTableType.AT_AddInherit and isinstance(cmd.def_, ast.RangeVar):
            parent, child = table_name(cmd.def_), table_name(stmt.relation)
        else:
            continue
        if not table_known_empty(state, child):
            _taint(state, parent)


def _taint(state: MigrationState, written: str) -> None:
    """Record that rows may have been written to the relation named `written`."""
    if not written:
        return
    target = RelationId.parse(written)
    state.relations_with_data.update(e for e in state.relations_created if _may_denote(e, target))


def _is_with_no_data(stmt: Any) -> bool:
    into = getattr(stmt, "into", None)
    return bool(getattr(into, "skipData", False)) if into is not None else False


def _is_opaque(stmt: Any) -> bool:
    """Procedural code whose writes can't be seen statically."""
    if isinstance(stmt, (ast.CallStmt, ast.ExecuteStmt)):
        return True
    if isinstance(stmt, ast.DoStmt):
        body = " ".join(
            getattr(arg.arg, "sval", "") or ""
            for arg in stmt.args or ()
            if isinstance(arg, ast.DefElem) and arg.defname == "as"
        )
        return bool(_DO_BLOCK_WRITE.search(body))
    return False


# --- search_path --------------------------------------------------------------


def _search_path_from_set(stmt: ast.VariableSetStmt) -> tuple[bool, str | None]:
    """(changes search_path?, new head) for SET / RESET."""
    if stmt.kind == VariableSetKind.VAR_RESET_ALL:
        return True, DEFAULT_SCHEMA
    if (stmt.name or "").lower() != "search_path":
        return False, None
    if stmt.kind in (VariableSetKind.VAR_SET_DEFAULT, VariableSetKind.VAR_RESET):
        return True, DEFAULT_SCHEMA
    if stmt.kind != VariableSetKind.VAR_SET_VALUE:
        return True, None
    entries = []
    for arg in stmt.args or ():
        sval = getattr(getattr(arg, "val", None), "sval", None)
        if sval is None:
            return True, None
        # Each SET argument is one schema name, already unquoted by the parser;
        # `SET search_path = 'a, b'` names a single schema called "a, b".
        entries.append(sval)
    return True, _path_head(entries)


def _search_path_from_set_config(call: ast.FuncCall) -> tuple[bool, str | None]:
    """(changes search_path?, new head) for set_config('search_path', ...)."""
    names = [getattr(n, "sval", "") for n in call.funcname or ()]
    args = list(call.args or ())
    if not names or names[-1] != "set_config" or len(args) < 2:
        return False, None
    setting = getattr(getattr(args[0], "val", None), "sval", None)
    if setting is None or setting.lower() != "search_path":
        return False, None
    value = getattr(getattr(args[1], "val", None), "sval", None)
    if value is None:
        return True, None  # computed at run time
    return True, _path_head(_split_path(value))


def _split_path(value: str) -> list[str]:
    """Split a search_path string the way Postgres does: comma-separated,
    double quotes preserve case and commas, unquoted names fold to lower case."""
    entries = []
    for m in re.finditer(r'\s*(?:"((?:[^"]|"")*)"|([^,]*?))\s*(?:,|$)', value):
        quoted, bare = m.group(1), m.group(2)
        if quoted is not None:
            entries.append(quoted.replace('""', '"'))
        elif bare:
            entries.append(bare.lower())
        if m.end() >= len(value):
            break
    return entries


def _path_head(entries: list[str]) -> str | None:
    """The schema unqualified names resolve to first, or None if unknowable.

    `$user` is skipped, as elsewhere in this module (no schema named after the
    role is assumed). An empty path leaves nothing to resolve to.
    """
    for entry in entries:
        if not entry or entry == "$user" or entry == "pg_catalog" or entry.startswith("pg_temp"):
            continue
        return entry
    return None


# --- queries rules use ---------------------------------------------------------


def table_created_in_migration(state: MigrationState, target: str) -> bool:
    """Did an earlier statement in this migration create this relation?

    Use this only when the question really is "does it exist yet" — e.g. a
    matview has no outside readers to block. When the premise is that the table
    is *empty*, use `table_known_empty`.
    """
    tid = _resolve(state, target)
    if tid is None:
        return False
    return bool(_matching(state.relations_created, tid, state.ambiguous_bare_names))


def table_known_empty(state: MigrationState, target: str) -> bool:
    """Was this relation created earlier in this migration and left empty?

    This is the premise behind most suppression: a rewrite, constraint scan, or
    index build is free on a table with no rows. Creating a table doesn't prove
    it's empty — the migration may have inserted into it, or created it via
    CREATE TABLE AS — so those cases return False and the hazard is reported.
    """
    tid = _resolve(state, target)
    if tid is None:
        return False
    created = _matching(state.relations_created, tid, state.ambiguous_bare_names)
    return bool(created) and not any(c in state.relations_with_data for c in created)


def index_created_in_migration(state: MigrationState, target: str) -> bool:
    """Did an earlier statement in this migration create this index?"""
    tid = _resolve(state, target)
    if tid is None:
        return False
    return bool(_matching(state.indexes_created, tid, state.ambiguous_bare_names))
