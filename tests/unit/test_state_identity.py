"""Suppression must rest on an identity and an emptiness this file establishes.

`test_state_semantics.py` covers the basic premises — created earlier, same
schema, still empty. These cases are the ways a migration can break one of them
without the old state model noticing: writes it didn't track (MERGE, a WITH
clause, procedural code), names it resolved wrongly (search_path, index names,
temp tables), and identities that moved (DROP, RENAME, SET SCHEMA). Each one
silently suppressed a real finding before this change.

The direction of every assertion is the same: when the file doesn't prove the
premise, the finding is reported.
"""

from __future__ import annotations

import pytest

import safemigrate_lint.rules  # noqa: F401  (import registers every rule)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.parser import parse_string
from safemigrate_lint.core.state import StateBuilder

FK = "constraint-not-valid-required"
DROP_INDEX = "concurrent-index-drop-required"
_ADD_FK = "ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES users(id);"


def _ids(sql: str) -> list[str]:
    result = parse_string(sql, file="m.sql")
    assert result.finding is None, result.finding
    return [f.rule_id for f in analyze(result, StateBuilder.build(result.statements or []))]


def _fk_reported_after(*statements: str) -> bool:
    return FK in _ids("\n".join([*statements, _ADD_FK]))


# --- emptiness: writes the old model missed --------------------------------------


def test_baseline_a_new_table_is_still_suppressed() -> None:
    assert not _fk_reported_after("CREATE TABLE orders (id int);")


@pytest.mark.parametrize(
    "write",
    [
        "MERGE INTO orders o USING src s ON o.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id);",
        "WITH moved AS (INSERT INTO orders SELECT id FROM src RETURNING id) SELECT count(*) FROM moved;",
        "WITH x AS (INSERT INTO orders VALUES (1) RETURNING id) UPDATE src SET id = 1;",
        "COPY orders FROM STDIN;",
        "INSERT INTO public.orders VALUES (1);",
        "DO $$ BEGIN INSERT INTO orders VALUES (1); END $$;",
        "DO $$ BEGIN EXECUTE 'INSERT INTO orders VALUES (1)'; END $$;",
        "DO $$ BEGIN PERFORM backfill_orders(); END $$;",
        "CALL backfill_orders();",
        "EXECUTE backfill_plan;",
    ],
    ids=[
        "merge",
        "insert-in-with",
        "insert-in-with-of-update",
        "copy-from",
        "qualified-insert",
        "do-insert",
        "do-execute",
        "do-perform",
        "call",
        "execute-prepared",
    ],
)
def test_a_write_makes_the_table_not_known_empty(write: str) -> None:
    assert _fk_reported_after("CREATE TABLE orders (id int);", write)


@pytest.mark.parametrize(
    "harmless",
    [
        "COPY orders TO STDOUT;",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'm') THEN "
        "CREATE TYPE m AS ENUM ('a'); END IF; END $$;",
        "CREATE FUNCTION f() RETURNS void LANGUAGE sql BEGIN ATOMIC INSERT INTO orders VALUES (1); END;",
        "CREATE RULE r AS ON INSERT TO src DO ALSO INSERT INTO orders VALUES (1);",
    ],
    ids=["copy-to", "do-conditional-ddl", "function-definition", "rule-definition"],
)
def test_statements_that_write_nothing_now_keep_suppression(harmless: str) -> None:
    """Defining code runs nothing; only executing it can write."""
    assert not _fk_reported_after("CREATE TABLE orders (id int);", harmless)


def test_a_write_through_an_ambiguous_name_counts_against_every_candidate() -> None:
    """With audit.orders created, bare `orders` might resolve to it on some
    search_path. Proving emptiness needs certainty; tainting doesn't."""
    sql = [
        "CREATE TABLE audit.orders (id int);",
        "INSERT INTO orders VALUES (1);",
        "ALTER TABLE audit.orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES users(id);",
    ]
    assert FK in _ids("\n".join(sql))


def test_refresh_populates_a_matview_created_with_no_data() -> None:
    sql = [
        "CREATE MATERIALIZED VIEW mv AS SELECT * FROM big WITH NO DATA;",
        "REFRESH MATERIALIZED VIEW mv;",
        "CREATE INDEX mv_idx ON mv (id);",
    ]
    assert "concurrent-index-create-required" in _ids("\n".join(sql))


def test_attaching_a_populated_partition_populates_the_parent() -> None:
    sql = [
        "CREATE TABLE orders (id int) PARTITION BY RANGE (id);",
        "ALTER TABLE orders ATTACH PARTITION legacy_orders FOR VALUES FROM (0) TO (100);",
    ]
    assert _fk_reported_after(*sql)


def test_attaching_an_empty_new_partition_keeps_the_parent_empty() -> None:
    sql = [
        "CREATE TABLE orders (id int) PARTITION BY RANGE (id);",
        "CREATE TABLE orders_0 (id int);",
        "ALTER TABLE orders ATTACH PARTITION orders_0 FOR VALUES FROM (0) TO (100);",
    ]
    assert not _fk_reported_after(*sql)


def test_inheriting_a_populated_child_populates_the_parent() -> None:
    sql = ["CREATE TABLE orders (id int);", "ALTER TABLE legacy_orders INHERIT orders;"]
    assert _fk_reported_after(*sql)


# --- identity: search_path ---------------------------------------------------------


def test_set_search_path_moves_unqualified_creates_to_its_first_schema() -> None:
    sql = [
        "SET search_path TO audit, public;",
        "CREATE TABLE orders (id int);",  # audit.orders
        "ALTER TABLE public.orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES users(id);",
    ]
    assert FK in _ids("\n".join(sql))


def test_unqualified_names_resolve_to_the_same_first_schema() -> None:
    sql = ["SET search_path TO audit, public;", "CREATE TABLE orders (id int);"]
    assert not _fk_reported_after(*sql)
    qualified = "ALTER TABLE audit.orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES u(id);"
    assert FK not in _ids("\n".join([*sql, qualified]))


def test_a_table_created_before_the_path_changed_is_not_found_by_bare_name() -> None:
    """After the change, bare `orders` resolves to audit.orders — not the new public one."""
    sql = ["CREATE TABLE orders (id int);", "SET search_path TO audit, public;"]
    assert _fk_reported_after(*sql)


def test_reset_restores_the_default_path() -> None:
    sql = ["SET search_path TO audit;", "RESET search_path;", "CREATE TABLE orders (id int);"]
    assert not _fk_reported_after(*sql)


@pytest.mark.parametrize(
    "set_path",
    [
        "SET search_path = '';",
        "SELECT pg_catalog.set_config('search_path', '', false);",
        "SELECT set_config('search_path', current_setting('app.schema'), false);",
        'SET search_path TO "$user";',
    ],
    ids=["empty", "pg_dump-style-empty", "computed", "user-only"],
)
def test_an_unknowable_path_makes_bare_names_match_nothing(set_path: str) -> None:
    sql = ["CREATE TABLE public.orders (id int);", set_path]
    assert _fk_reported_after(*sql)


def test_qualified_names_still_work_under_pg_dumps_empty_path() -> None:
    sql = [
        "SELECT pg_catalog.set_config('search_path', '', false);",
        "CREATE TABLE public.orders (id int);",
        "ALTER TABLE public.orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES public.users(id);",
    ]
    assert FK not in _ids("\n".join(sql))


def test_set_config_value_is_parsed_like_postgres() -> None:
    sql = [
        "SELECT set_config('search_path', '\"$user\", Audit', false);",  # folds to audit
        "CREATE TABLE orders (id int);",
        "ALTER TABLE audit.orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES u(id);",
    ]
    assert FK not in _ids("\n".join(sql))


def test_a_temp_table_never_vouches_for_a_permanent_one() -> None:
    sql = [
        "CREATE TEMP TABLE orders (id int);",
        "ALTER TABLE public.orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES users(id);",
    ]
    assert FK in _ids("\n".join(sql))


# --- identity: indexes ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("create", "drop", "suppressed"),
    [
        ("CREATE INDEX idx ON t (c);", "DROP INDEX idx;", True),
        ("CREATE INDEX idx ON public.t (c);", "DROP INDEX idx;", True),
        ("CREATE INDEX idx ON audit.t (c);", "DROP INDEX audit.idx;", True),
        ("CREATE INDEX idx ON audit.t (c);", "DROP INDEX idx;", False),
        ("CREATE INDEX idx ON audit.t (c);", "DROP INDEX public.idx;", False),
        ("CREATE INDEX idx ON t (c);", "DROP INDEX audit.idx;", False),
    ],
)
def test_index_identity_is_schema_qualified(create: str, drop: str, suppressed: bool) -> None:
    """An index lives in its table's schema; the bare name proves nothing."""
    assert (DROP_INDEX not in _ids(f"{create}\n{drop}")) is suppressed


def test_index_dropped_and_recreated_elsewhere_is_not_confused() -> None:
    sql = "CREATE INDEX idx ON t (c);\nDROP INDEX idx;\nDROP INDEX idx;"
    assert _ids(sql).count(DROP_INDEX) == 1  # the second DROP targets something else


def test_renamed_index_is_followed() -> None:
    sql = "CREATE INDEX idx ON t (c);\nALTER INDEX idx RENAME TO idx2;\nDROP INDEX idx2;"
    assert DROP_INDEX not in _ids(sql)


# --- identity: DROP, RENAME, SET SCHEMA ------------------------------------------------


def test_drop_then_recreate_is_empty_again() -> None:
    sql = [
        "CREATE TABLE orders (id int);",
        "INSERT INTO orders VALUES (1);",
        "DROP TABLE orders;",
        "CREATE TABLE orders (id int);",
    ]
    assert not _fk_reported_after(*sql)


def test_renaming_an_existing_table_onto_a_created_name_is_not_trusted() -> None:
    """The new table moved aside; `orders` now names the old, populated one."""
    sql = [
        "CREATE TABLE orders (id int);",
        "ALTER TABLE orders RENAME TO orders_new;",
        "ALTER TABLE legacy_orders RENAME TO orders;",
    ]
    assert _fk_reported_after(*sql)


def test_a_renamed_new_table_keeps_its_suppression_under_the_new_name() -> None:
    sql = ["CREATE TABLE orders_tmp (id int);", "ALTER TABLE orders_tmp RENAME TO orders;"]
    assert not _fk_reported_after(*sql)


def test_set_schema_moves_the_identity() -> None:
    moved = ["CREATE TABLE orders (id int);", "ALTER TABLE orders SET SCHEMA audit;"]
    assert _fk_reported_after(*moved)  # public.orders is no longer the new table
    sql = [*moved, "ALTER TABLE audit.orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES u(id);"]
    assert FK not in _ids("\n".join(sql))


def test_refresh_compares_matviews_schema_qualified() -> None:
    sql = "CREATE MATERIALIZED VIEW mv AS SELECT 1;\nREFRESH MATERIALIZED VIEW audit.mv;"
    assert "refresh-matview-blocks-reads" in _ids(sql)


# --- transactions (SAF-07) --------------------------------------------------------------

UNCOMMITTED = "uncommitted-transaction-banned"


def _flagged_lines(sql: str, rule_id: str) -> list[int]:
    result = parse_string(sql, file="m.sql")
    return [
        f.line
        for f in analyze(result, StateBuilder.build(result.statements or []))
        if f.rule_id == rule_id
    ]


def test_only_the_begin_left_open_is_reported() -> None:
    sql = "BEGIN;\nSELECT 1;\nCOMMIT;\nBEGIN;\nSELECT 2;\n"
    assert _flagged_lines(sql, UNCOMMITTED) == [4]


def test_a_balanced_file_reports_nothing() -> None:
    assert _flagged_lines("BEGIN;\nCOMMIT;\nSTART TRANSACTION;\nROLLBACK;\n", UNCOMMITTED) == []


def test_commit_and_chain_keeps_the_transaction_open() -> None:
    assert _flagged_lines("BEGIN;\nSELECT 1;\nCOMMIT AND CHAIN;\n", UNCOMMITTED) == [1]


def test_prepare_transaction_closes_it() -> None:
    assert _flagged_lines("BEGIN;\nSELECT 1;\nPREPARE TRANSACTION 'x';\n", UNCOMMITTED) == []


def test_concurrent_index_inside_a_chained_transaction_is_still_flagged() -> None:
    sql = "BEGIN;\nCOMMIT AND CHAIN;\nCREATE INDEX CONCURRENTLY i ON t (c);\nCOMMIT;\n"
    assert _flagged_lines(sql, "index-concurrent-in-transaction-banned") == [3]
