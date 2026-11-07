"""Parser wrapper around pglast (libpg_query).

Using libpg_query means the grammar is Postgres's own, not a reimplementation
that drifts. But it is one *specific* Postgres version's grammar, not all of
them: pglast vendors a fixed libpg_query, so syntax added in a newer Postgres
than that one is a parse error here even though a real server accepts it.

`POSTGRES_GRAMMAR_VERSION` reports the version in use. pyproject.toml caps
pglast to one major (pglast N vendors Postgres N+10's grammar), so a PyPI
install, the pre-commit hook and the Action image all parse the same grammar,
and `test_parser_version.py` pins it so an upgrade is a deliberate, visible
change rather than something a dependency bump does silently. The README
documents the boundary; the known gap today is PG18 virtual generated columns
(`GENERATED ALWAYS AS (...) VIRTUAL`).

On hard parse failure we emit a single syntax-error Finding and the engine
short-circuits — no point running rules against a tree we couldn't build.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pglast
from pglast import parse_sql
from pglast.parser import ParseError

from .finding import Finding, Severity
from .location import line_col, parse_error_index

#: The Postgres grammar this build accepts, as (major, minor). Syntax newer than
#: this parses as a syntax error even though a real server would accept it.
POSTGRES_GRAMMAR_VERSION: tuple[int, ...] = tuple(pglast.get_postgresql_version())

SYNTAX_ERROR_RULE_ID = "syntax-error"


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Outcome of parsing one SQL file.

    Exactly one of (statements, finding) is populated.
    """

    file: str
    sql: str
    statements: list[Any] | None  # list of pglast RawStmt nodes; opaque to callers
    finding: Finding | None  # populated on parse failure


def parse_file(path: Path) -> ParseResult:
    """Parse one .sql file via pglast. Returns RawStmts or a syntax-error Finding."""
    sql = path.read_text(encoding="utf-8")
    return _parse(file=str(path), sql=sql)


def parse_string(sql: str, *, file: str = "<stdin>") -> ParseResult:
    """Parse SQL from a string. Used by unit tests."""
    return _parse(file=file, sql=sql)


def _parse(*, file: str, sql: str) -> ParseResult:
    try:
        stmts = parse_sql(sql)
        return ParseResult(file=file, sql=sql, statements=list(stmts), finding=None)
    except ParseError as err:
        # The position is err.args[1], not an attribute — reading `.location`
        # found nothing, and every syntax error was reported at line 1.
        index = parse_error_index(sql, err)
        line, col = line_col(sql, index) if index is not None else (1, 1)
        message = err.args[0] if err.args else str(err)
        return ParseResult(
            file=file,
            sql=sql,
            statements=None,
            finding=Finding(
                rule_id=SYNTAX_ERROR_RULE_ID,
                severity=Severity.CRITICAL,
                file=file,
                line=line,
                column=col,
                # The bare message: pglast's str() appends ", at index N", a
                # character index nobody reading the report can use. The
                # finding's line and column say where it is.
                message=f"SQL parse failure: {message}",
                help=(
                    "pglast (libpg_query) could not parse this SQL. Either it is not valid "
                    "Postgres or it contains pre-build extension placeholders (e.g. "
                    "@MODULE_PATHNAME@, @extschema@) that must be substituted before linting."
                ),
            ),
        )
