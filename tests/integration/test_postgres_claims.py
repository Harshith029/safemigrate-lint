"""The linter's claims about PostgreSQL, checked against a real PostgreSQL.

A migration-safety tool is only as good as what it says about the server: the
lock a statement takes, what can run inside a transaction block, whether a
suggested fix actually works. Those claims used to be checked only against the
docs and this project's own reading of them, and some were wrong — CREATE INDEX
was described as ACCESS EXCLUSIVE (it is SHARE), REFRESH ... CONCURRENTLY as
unable to run in a transaction block (it can), and the identity-column fix
failed outright. Here each claim is executed:

* every `lock_impact` the linter reports is compared with the strongest lock
  the server holds on that relation (pg_locks) after running the statement;
* the CONCURRENTLY forms the rules recommend are run inside a transaction block;
* the identity-column fix runs end to end on a populated table.

Skipped unless SAFEMIGRATE_TEST_PG_DSN points at a disposable server; it creates
and drops the schemas `smlint_it` and `smlint_identity`. Needs the `integration`
dependency group (`uv sync --group integration`). CI's `postgres` job runs it
against every supported PostgreSQL major.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from typing import Any

import pglast
import pytest
from pglast import ast
from pglast.enums import AlterTableType

import safemigrate_lint.rules  # noqa: F401  (import registers every rule)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.lock_impact import LOCK_IMPACT
from safemigrate_lint.core.parser import parse_string
from safemigrate_lint.core.reporter import _LOCK_RANK
from safemigrate_lint.core.state import StateBuilder

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("SAFEMIGRATE_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set SAFEMIGRATE_TEST_PG_DSN to a disposable PostgreSQL to run"
)

SCHEMA = "smlint_it"
_SETUP = f"""
DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;
CREATE SCHEMA {SCHEMA};
SET search_path TO {SCHEMA};
CREATE TABLE ref (id int PRIMARY KEY);
INSERT INTO ref VALUES (1);
CREATE TABLE t (id int PRIMARY KEY, c int, d int, e int, ref_id int);
INSERT INTO t SELECT g, g, g, g, 1 FROM generate_series(1, 200) g;
CREATE INDEX t_d_idx ON t (d);
CREATE TABLE nopk (id int, c int);
INSERT INTO nopk SELECT g, g FROM generate_series(1, 200) g;
CREATE TABLE empty_t (id int);
CREATE MATERIALIZED VIEW mv AS SELECT id, c FROM t;
CREATE UNIQUE INDEX mv_id ON mv (id);
CREATE TABLE parent (a int) PARTITION BY RANGE (a);
CREATE TABLE part1 PARTITION OF parent FOR VALUES FROM (0) TO (1000);
CREATE FUNCTION noop_trg() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
"""

# rule id -> (a statement the rule flags, the relation its lock_impact describes).
# Every rule with a lock_impact needs an entry: a new lock claim isn't done
# until a server has confirmed it.
LOCK_CASES: dict[str, tuple[str, str]] = {
    "access-method-change-rewrites": ("ALTER TABLE t SET ACCESS METHOD heap", "t"),
    "add-non-nullable-without-default": (
        "ALTER TABLE empty_t ADD COLUMN x int NOT NULL",
        "empty_t",
    ),
    # Not `c`: mv reads it, and Postgres won't retype a column a view uses.
    "column-type-change-rewrites-table": ("ALTER TABLE t ALTER COLUMN d TYPE bigint", "t"),
    "concurrent-index-create-required": ("CREATE INDEX t_c_idx ON t (c)", "t"),
    "concurrent-index-drop-required": ("DROP INDEX t_d_idx", "t"),
    "concurrent-partition-detach-required": ("ALTER TABLE parent DETACH PARTITION part1", "parent"),
    "concurrent-reindex-required": ("REINDEX INDEX t_d_idx", "t_d_idx"),
    "constraint-dropped-warning": ("ALTER TABLE t DROP CONSTRAINT t_pkey", "t"),
    "drop-column-restricted": ("ALTER TABLE t DROP COLUMN e", "t"),
    "drop-table-restricted": ("DROP TABLE nopk", "nopk"),
    "identity-column-add-rewrites": (
        "ALTER TABLE t ADD COLUMN x int GENERATED ALWAYS AS IDENTITY",
        "t",
    ),
    "nullable-to-non-nullable-may-fail": ("ALTER TABLE t ALTER COLUMN c SET NOT NULL", "t"),
    "pk-constraint-exclusive-lock": ("ALTER TABLE nopk ADD PRIMARY KEY (id)", "nopk"),
    "refresh-matview-blocks-reads": ("REFRESH MATERIALIZED VIEW mv", "mv"),
    "rename-column-warning": ("ALTER TABLE t RENAME COLUMN e TO e2", "t"),
    "rename-table-warning": ("ALTER TABLE t RENAME TO t_renamed", "t"),
    "stored-generated-column-rewrites": (
        "ALTER TABLE t ADD COLUMN x int GENERATED ALWAYS AS (c * 2) STORED",
        "t",
    ),
    "table-logging-mode-rewrites": ("ALTER TABLE t SET UNLOGGED", "t"),
    "trigger-add-blocks-writes": (
        "CREATE TRIGGER trg BEFORE INSERT ON t FOR EACH ROW EXECUTE FUNCTION noop_trg()",
        "t",
    ),
    "truncate-cascade-banned": ("TRUNCATE t CASCADE", "t"),
    "unique-constraint-data-dependent": ("ALTER TABLE t ADD CONSTRAINT t_c_key UNIQUE (c)", "t"),
    "unique-constraint-exclusive-lock": ("ALTER TABLE t ADD CONSTRAINT t_c_key UNIQUE (c)", "t"),
    "update-delete-row-scope": ("UPDATE t SET c = c + 1", "t"),
    "volatile-default-rewrites-table": (
        "ALTER TABLE t ADD COLUMN x uuid DEFAULT gen_random_uuid()",
        "t",
    ),
}
# Syntax newer than the oldest supported server, by server_version_num.
_MIN_SERVER = {"access-method-change-rewrites": 150000}


def _end_open_transaction(conn: Any) -> None:
    """A failed step can leave an explicit BEGIN open; clean-up needs it closed."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.execute("ROLLBACK")


@pytest.fixture(scope="module")
def pg() -> Iterator[Any]:
    conn = psycopg.connect(DSN, autocommit=True)
    try:
        conn.execute(_SETUP)
        yield conn
    finally:
        _end_open_transaction(conn)
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.close()


def _findings(sql: str) -> list[Any]:
    result = parse_string(sql if sql.rstrip().endswith(";") else sql + ";")
    assert result.finding is None, result.finding
    return analyze(result, StateBuilder.build(result.statements or []))


def _reported_lock(sql: str, rule_id: str) -> str:
    (finding,) = [f for f in _findings(sql) if f.rule_id == rule_id]
    assert finding.lock_impact is not None, f"{rule_id} reported no lock impact"
    return finding.lock_impact.lock


def _mode(pg_locks_mode: str) -> str:
    """pg_locks' AccessExclusiveLock -> the report's ACCESS EXCLUSIVE."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", pg_locks_mode.removesuffix("Lock")).upper()


def _measured_lock(conn: Any, sql: str, relation: str) -> str:
    """Strongest lock this session holds on `relation` right after `sql` runs."""
    conn.execute("BEGIN")
    try:
        (oid,) = conn.execute("SELECT %s::regclass::oid", (relation,)).fetchone()
        conn.execute(sql)
        modes = [
            _mode(mode)
            for (mode,) in conn.execute(
                "SELECT mode FROM pg_locks WHERE pid = pg_backend_pid() "
                "AND locktype = 'relation' AND relation = %s",
                (oid,),
            )
        ]
    finally:
        conn.execute("ROLLBACK")
    assert modes, f"no lock held on {relation} after: {sql}"
    return max(modes, key=lambda m: _LOCK_RANK[m])


def test_every_lock_claim_has_a_server_check() -> None:
    assert set(LOCK_CASES) == set(LOCK_IMPACT)


@pytest.mark.parametrize("rule_id", sorted(LOCK_CASES))
def test_reported_lock_is_the_lock_postgres_takes(pg: Any, rule_id: str) -> None:
    if pg.info.server_version < _MIN_SERVER.get(rule_id, 0):
        pytest.skip(f"{rule_id}'s statement needs a newer server")
    sql, relation = LOCK_CASES[rule_id]
    assert _reported_lock(sql, rule_id) == _measured_lock(pg, sql, relation)


@pytest.mark.parametrize(
    ("sql", "relations"),
    [
        (
            "ALTER TABLE t ADD CONSTRAINT t_ref_fk FOREIGN KEY (ref_id) REFERENCES ref (id)",
            ("t", "ref"),
        ),
        ("ALTER TABLE t ADD CONSTRAINT t_c_pos CHECK (c > 0)", ("t",)),
    ],
    ids=["foreign-key-both-tables", "check"],
)
def test_constraint_lock_follows_the_constraint_type(
    pg: Any, sql: str, relations: tuple[str, ...]
) -> None:
    reported = _reported_lock(sql, "constraint-not-valid-required")
    for relation in relations:
        assert _measured_lock(pg, sql, relation) == reported, relation


def test_adding_not_null_without_default_fails_on_a_populated_table(pg: Any) -> None:
    """The premise of add-non-nullable-without-default: the migration errors out."""
    with pytest.raises(psycopg.errors.NotNullViolation):
        _in_transaction(pg, "ALTER TABLE t ADD COLUMN x int NOT NULL")


# --- what may run inside a transaction block ----------------------------------------


def _in_transaction(conn: Any, sql: str) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute(sql)
    finally:
        conn.execute("ROLLBACK")


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE INDEX CONCURRENTLY t_c2_idx ON t (c)",
        "DROP INDEX CONCURRENTLY t_d_idx",
        "REINDEX INDEX CONCURRENTLY t_d_idx",
        "ALTER TABLE parent DETACH PARTITION part1 CONCURRENTLY",
    ],
)
def test_concurrently_forms_cannot_run_in_a_transaction_block(pg: Any, sql: str) -> None:
    with pytest.raises(psycopg.errors.ActiveSqlTransaction):
        _in_transaction(pg, sql)


def test_refresh_concurrently_can_run_in_a_transaction_block(pg: Any) -> None:
    _in_transaction(pg, "REFRESH MATERIALIZED VIEW CONCURRENTLY mv")


# --- the identity-column fix, end to end -----------------------------------------------

IDENTITY_SCHEMA = "smlint_identity"


def _fix_statements(fix: str) -> list[tuple[str, Any]]:
    out = []
    for raw in pglast.parse_sql(fix):
        start = raw.stmt_location or 0
        end = start + raw.stmt_len if raw.stmt_len else len(fix)
        out.append((fix[start:end], raw.stmt))
    return out


def _alters(node: Any, subtype: AlterTableType) -> bool:
    return isinstance(node, ast.AlterTableStmt) and any(
        getattr(cmd, "subtype", None) == subtype for cmd in node.cmds or ()
    )


def _sets_default(node: Any) -> bool:
    """SET DEFAULT — DROP DEFAULT is the same command with no expression."""
    return isinstance(node, ast.AlterTableStmt) and any(
        getattr(cmd, "subtype", None) == AlterTableType.AT_ColumnDefault
        and getattr(cmd, "def_", None) is not None
        for cmd in node.cmds or ()
    )


@pytest.mark.parametrize(
    "identity", ["GENERATED ALWAYS AS IDENTITY", "GENERATED BY DEFAULT AS IDENTITY"]
)
def test_identity_fix_runs_without_a_rewrite_or_duplicate_ids(pg: Any, identity: str) -> None:
    users = f"{IDENTITY_SCHEMA}.users"
    pg.execute(f"DROP SCHEMA IF EXISTS {IDENTITY_SCHEMA} CASCADE")
    pg.execute(f"CREATE SCHEMA {IDENTITY_SCHEMA}")
    try:
        pg.execute(f"CREATE TABLE {users} (pk bigint PRIMARY KEY, name text)")
        pg.execute(f"INSERT INTO {users} SELECT g, 'u' || g FROM generate_series(1, 25000) g")
        (filenode,) = pg.execute("SELECT pg_relation_filenode(%s)", (users,)).fetchone()

        (finding,) = [
            f
            for f in _findings(f"ALTER TABLE {users} ADD COLUMN id int {identity}")
            if f.rule_id == "identity-column-add-rewrites"
        ]
        assert finding.suggested_fix is not None
        notices: list[str] = []

        def on_notice(diag: Any) -> None:
            notices.append(diag.message_primary or "")

        pg.add_notice_handler(on_notice)
        try:
            for sql, node in _fix_statements(finding.suggested_fix):
                if isinstance(node, ast.UpdateStmt):
                    batches = 0
                    while pg.execute(sql).rowcount:  # one committed batch per round
                        batches += 1
                    # 25,000 existing rows, 10,000 at a time; the app row below
                    # was already numbered by the default.
                    assert batches == 3
                elif _alters(node, AlterTableType.AT_SetNotNull):
                    pg.execute("SET client_min_messages = debug1")
                    pg.execute(sql)
                    pg.execute("RESET client_min_messages")
                else:
                    pg.execute(sql)
                if _sets_default(node):
                    # An application insert arriving while the migration is underway.
                    pg.execute(f"INSERT INTO {users} (pk, name) VALUES (90001, 'during')")
        finally:
            pg.remove_notice_handler(on_notice)
            _end_open_transaction(pg)

        pg.execute(f"INSERT INTO {users} (pk, name) VALUES (90002, 'after')")
        rows, distinct, nulls, newest_is_highest = pg.execute(
            f"SELECT count(*), count(DISTINCT id), count(*) FILTER (WHERE id IS NULL), "
            f"(SELECT id FROM {users} WHERE pk = 90002) > "
            f"(SELECT max(id) FROM {users} WHERE pk <> 90002) FROM {users}"
        ).fetchone()
        assert (rows, distinct, nulls) == (25002, 25002, 0)
        assert newest_is_highest, "the identity reused an id the backfill handed out"

        (after,) = pg.execute("SELECT pg_relation_filenode(%s)", (users,)).fetchone()
        assert after == filenode, "a step rewrote the table"
        assert any("sufficient to prove" in n for n in notices), "SET NOT NULL rescanned the table"

        attidentity, attnotnull, atthasdef = pg.execute(
            "SELECT attidentity, attnotnull, atthasdef FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attname = 'id'",
            (users,),
        ).fetchone()
        assert attidentity == ("a" if "ALWAYS" in identity else "d")
        assert attnotnull and not atthasdef
        (leftover,) = pg.execute(
            "SELECT to_regclass(%s)", (f"{IDENTITY_SCHEMA}.users_id_backfill_seq",)
        ).fetchone()
        assert leftover is None, "the backfill sequence was left behind"
    finally:
        _end_open_transaction(pg)
        pg.execute(f"DROP SCHEMA IF EXISTS {IDENTITY_SCHEMA} CASCADE")
