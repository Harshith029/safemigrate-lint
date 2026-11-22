"""The two rules added after measuring recall against squawk.

Running both linters over 2,497 real migrations showed only 0.38% of squawk's
findings fell in a category with no equivalent here. These close that: DROP NOT
NULL (45 occurrences across 29 files) and over-long identifiers (8).

`identifier-too-long` earns the most tests because detecting it is not
straightforward — libpg_query truncates the name while parsing, so the AST alone
cannot tell an over-long name from a legal 63-byte one.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

import safemigrate_lint.rules  # noqa: F401  (import registers every rule)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.finding import Finding, Severity
from safemigrate_lint.core.parser import parse_file
from safemigrate_lint.core.state import StateBuilder
from safemigrate_lint.rules.identifier_too_long import MAX_IDENTIFIER_BYTES


def _findings(sql: str) -> list[Finding]:
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "m.sql"
        f.write_text(sql, encoding="utf-8")
        result = parse_file(f)
        return analyze(result, StateBuilder.build(result.statements or []))


def _for(sql: str, rule_id: str) -> list[Finding]:
    return [f for f in _findings(sql) if f.rule_id == rule_id]


# --- not-null-dropped-warning ----------------------------------------------

DNN = "not-null-dropped-warning"


def test_drop_not_null_is_reported() -> None:
    hits = _for("ALTER TABLE users ALTER COLUMN email DROP NOT NULL;", DNN)
    assert hits and "email" in hits[0].message


def test_set_not_null_is_a_different_rule() -> None:
    """Tightening is a scan risk; relaxing is a contract change. Not the same rule."""
    sql = "ALTER TABLE users ALTER COLUMN email SET NOT NULL;"
    assert not _for(sql, DNN)
    assert _for(sql, "nullable-to-non-nullable-may-fail")


def test_suppressed_on_a_table_created_empty_in_this_migration() -> None:
    """No existing reader can be depending on a guarantee that never shipped."""
    sql = "CREATE TABLE t (c int);\nALTER TABLE t ALTER COLUMN c DROP NOT NULL;\n"
    assert not _for(sql, DNN)


def test_not_suppressed_once_the_table_has_rows() -> None:
    sql = (
        "CREATE TABLE t (c int);\n"
        "INSERT INTO t VALUES (1);\n"
        "ALTER TABLE t ALTER COLUMN c DROP NOT NULL;\n"
    )
    assert _for(sql, DNN)


def test_is_a_warning_not_critical() -> None:
    """Visible in the diff and deliberate — CRITICAL is for what the diff hides."""
    hits = _for("ALTER TABLE t ALTER COLUMN c DROP NOT NULL;", DNN)
    assert hits and hits[0].severity is Severity.WARNING


# --- identifier-too-long ----------------------------------------------------

ITL = "identifier-too-long"


@pytest.mark.parametrize(
    ("kind", "sql"),
    [
        ("index", "CREATE INDEX {n} ON t (c);"),
        ("table", "CREATE TABLE {n} (id int);"),
        ("column", "CREATE TABLE t ({n} int);"),
        ("constraint", "ALTER TABLE t ADD CONSTRAINT {n} CHECK (x > 0);"),
        (
            "trigger",
            "CREATE TRIGGER {n} AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();",
        ),
    ],
)
def test_over_long_names_are_caught_for_each_object_kind(kind: str, sql: str) -> None:
    hits = _for(sql.format(n="x" * 70), ITL)
    assert hits, f"{kind} name of 70 bytes should be flagged"
    assert kind in hits[0].message


def test_exactly_at_the_limit_is_legal() -> None:
    """63 bytes is valid. libpg_query hands back 63 for both cases, so a naive
    length check on the AST would flag every legal maximum-length name."""
    assert not _for("CREATE INDEX " + "a" * MAX_IDENTIFIER_BYTES + " ON t (c);", ITL)


def test_one_byte_over_is_caught() -> None:
    assert _for("CREATE INDEX " + "a" * (MAX_IDENTIFIER_BYTES + 1) + " ON t (c);", ITL)


def test_ordinary_names_are_quiet() -> None:
    assert not _for("CREATE INDEX idx_users_email ON users (email);", ITL)


def test_message_shows_the_name_postgres_will_actually_store() -> None:
    """The point of the rule: the catalog won't hold what the migration says."""
    name = "b" * 80
    hits = _for(f"CREATE INDEX {name} ON t (c);", ITL)
    assert hits
    assert "80 bytes" in hits[0].message
    assert "b" * MAX_IDENTIFIER_BYTES in hits[0].message
    assert hits[0].suggested_fix is not None
    assert "b" * MAX_IDENTIFIER_BYTES in hits[0].suggested_fix
