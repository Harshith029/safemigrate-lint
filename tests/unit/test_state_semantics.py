"""Cross-statement suppression: what it must still do, and what it must not.

Suppression is the feature most likely to hide a real hazard, because a
suppressed finding looks exactly like a clean migration. These tests pin both
directions — the cases where suppression is sound and must keep working, and
the cases where its premise doesn't hold and the hazard must be reported.

The three premises behind "created in this migration, so this is free" are that
the object was created *earlier*, in the *same schema*, and is still *empty*.
Each gets its own test here.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

import safemigrate_lint.rules  # noqa: F401  (import registers every rule)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.parser import parse_file
from safemigrate_lint.core.state import (
    MigrationState,
    RelationId,
    StateBuilder,
    table_known_empty,
)

FK = "constraint-not-valid-required"


def _ids(sql: str) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "m.sql"
        f.write_text(sql, encoding="utf-8")
        result = parse_file(f)
        return [x.rule_id for x in analyze(result, StateBuilder.build(result.statements or []))]


_ADD_FK = "ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES users(id);"


# --- premise 1: created EARLIER --------------------------------------------


def test_suppression_still_works_for_a_table_created_first() -> None:
    """The feature has to keep working — this is the false positive it exists for."""
    assert FK not in _ids(f"CREATE TABLE orders (id int);\n{_ADD_FK}")


def test_a_later_create_does_not_vouch_for_an_earlier_statement() -> None:
    assert FK in _ids(f"{_ADD_FK}\nCREATE TABLE orders (id int);")


# --- premise 2: same SCHEMA -------------------------------------------------


def test_unqualified_resolves_to_public_under_the_default_search_path() -> None:
    assert FK not in _ids(f"CREATE TABLE public.orders (id int);\n{_ADD_FK}")


def test_a_different_schema_never_vouches() -> None:
    sql = (
        "CREATE TABLE audit.orders (id int);\n"
        "ALTER TABLE public.orders ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES users(id);"
    )
    assert FK in _ids(sql)


def test_an_ambiguous_bare_name_is_not_guessed() -> None:
    """With audit.orders created, bare `orders` needs search_path — so don't assume."""
    assert FK in _ids(f"CREATE TABLE audit.orders (id int);\n{_ADD_FK}")


# --- premise 3: still EMPTY -------------------------------------------------


def test_insert_makes_a_created_table_no_longer_empty() -> None:
    sql = "CREATE TABLE orders (id int);\nINSERT INTO orders VALUES (1);\n" + _ADD_FK
    assert FK in _ids(sql)


def test_create_table_as_is_populated_on_creation() -> None:
    sql = "CREATE TABLE snap AS SELECT * FROM big;\nALTER TABLE snap ADD CONSTRAINT ck CHECK (id > 0);"
    assert FK in _ids(sql)


def test_create_table_as_with_no_data_is_empty() -> None:
    sql = (
        "CREATE TABLE snap AS SELECT * FROM big WITH NO DATA;\n"
        "ALTER TABLE snap ADD CONSTRAINT ck CHECK (id > 0);"
    )
    assert FK not in _ids(sql)


# --- identity parsing -------------------------------------------------------


@pytest.mark.parametrize(
    ("dotted", "expected"),
    [
        ("users", RelationId(None, "users")),
        ("public.users", RelationId("public", "users")),
        ("audit.users", RelationId("audit", "users")),
        ("", RelationId(None, "")),
    ],
)
def test_relation_id_parsing(dotted: str, expected: RelationId) -> None:
    assert RelationId.parse(dotted) == expected


def test_table_known_empty_is_false_for_an_untouched_table() -> None:
    """A table this migration never created says nothing about its contents."""
    assert not table_known_empty(MigrationState(), "orders")


# --- transaction bookkeeping ------------------------------------------------


def test_repeated_begin_does_not_open_a_second_transaction() -> None:
    """Postgres warns and ignores a nested BEGIN, so the COMMIT closes the original."""
    state = _build("BEGIN;\nBEGIN;\nCOMMIT;\n")
    assert not state.has_unmatched_begin
    assert state.nested_begin_statement_offsets  # still reported as a nested BEGIN


def test_unclosed_transaction_is_still_detected() -> None:
    assert _build("BEGIN;\nALTER TABLE t ADD COLUMN c int;\n").has_unmatched_begin


def _build(sql: str) -> MigrationState:
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "m.sql"
        f.write_text(sql, encoding="utf-8")
        return StateBuilder.build(parse_file(f).statements or [])
