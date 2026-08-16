"""The Postgres grammar version is part of the product's contract.

pglast vendors one specific libpg_query, so this tool accepts exactly that
Postgres version's syntax — not "whatever Postgres accepts". A dependency bump
silently changes which migrations parse, and dropping a newer PG's syntax on
the floor means CI blocks a migration a real server would run happily.

Pinning the version here makes that upgrade a visible, deliberate diff, and
documents the known gap rather than letting users discover it in a failing PR.
"""

from __future__ import annotations

import pglast
import pytest

from safemigrate_lint.core.parser import POSTGRES_GRAMMAR_VERSION, parse_string

EXPECTED_MAJOR = 17


def test_grammar_version_is_pinned() -> None:
    """Bumping pglast to a new Postgres major must update the README matrix too."""
    assert POSTGRES_GRAMMAR_VERSION[0] == EXPECTED_MAJOR, (
        f"parser grammar moved to Postgres {POSTGRES_GRAMMAR_VERSION[0]}. "
        f"Update EXPECTED_MAJOR and the supported-version table in README.md, "
        f"and re-check the known-unsupported syntax below."
    )


def test_reported_version_matches_pglast() -> None:
    assert POSTGRES_GRAMMAR_VERSION == tuple(pglast.get_postgresql_version())


@pytest.mark.parametrize(
    ("label", "sql"),
    [
        ("stored generated column", "CREATE TABLE t (a int, b int GENERATED ALWAYS AS (a*2) STORED);"),
        ("identity column", "CREATE TABLE t (id int GENERATED ALWAYS AS IDENTITY);"),
        ("partitioned table", "CREATE TABLE t (a int) PARTITION BY RANGE (a);"),
        ("index concurrently", "CREATE INDEX CONCURRENTLY i ON t (c);"),
        ("detach concurrently", "ALTER TABLE t DETACH PARTITION p CONCURRENTLY;"),
    ],
)
def test_supported_syntax_parses(label: str, sql: str) -> None:
    assert parse_string(sql).finding is None, f"{label} should parse on PG{EXPECTED_MAJOR}"


def test_pg18_virtual_generated_column_is_a_known_gap() -> None:
    """Documents a real limitation instead of pretending parser parity.

    PG18 added VIRTUAL generated columns. On a PG17 grammar this is a syntax
    error, so the tool reports one rather than silently passing. If this test
    starts failing, the parser gained PG18 support — drop the caveat from the
    README's supported-version section.
    """
    result = parse_string("CREATE TABLE t (a int, b int GENERATED ALWAYS AS (a*2) VIRTUAL);")
    assert result.finding is not None
    assert result.finding.rule_id == "syntax-error"
