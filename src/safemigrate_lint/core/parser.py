"""Parser wrapper around pglast (libpg_query).

This is the load-bearing wedge: by using libpg_query directly, we cannot drift
from Postgres syntax acceptance. Anything Postgres parses, we parse.

On hard parse failure we emit a single syntax-error Finding and the engine
short-circuits — no point running rules against a tree we couldn't build.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pglast import parse_sql
from pglast.parser import ParseError

from .finding import Finding, Severity

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
        # pglast's ParseError exposes .location (1-indexed byte offset) on recent versions.
        # We try a couple of attributes defensively; fall back to (1, 0) if unavailable.
        offset = getattr(err, "location", None) or getattr(err, "cursorpos", None) or 1
        line, col = _byte_offset_to_line_col(sql, int(offset))
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
                message=f"SQL parse failure: {err}",
                help=(
                    "pglast (libpg_query) could not parse this SQL. Either it is not valid "
                    "Postgres or it contains pre-build extension placeholders (e.g. "
                    "@MODULE_PATHNAME@, @extschema@) that must be substituted before linting."
                ),
            ),
        )


def _byte_offset_to_line_col(sql: str, offset_1_indexed: int) -> tuple[int, int]:
    """Convert pglast's 1-indexed byte offset into a 1-indexed (line, column) pair."""
    if offset_1_indexed < 1:
        return (1, 0)
    offset = min(offset_1_indexed - 1, len(sql))
    preceding = sql[:offset]
    line = preceding.count("\n") + 1
    last_newline = preceding.rfind("\n")
    column = offset - last_newline - 1 if last_newline >= 0 else offset
    return (line, max(0, column))
