"""Source positions: from what pglast reports to the line and column users see.

Two facts about pglast drive everything here, both checked by tests:

* `RawStmt.stmt_location` is a 0-based *character* index into the Python
  string (pglast converts libpg_query's byte offsets). It usually points at the
  end of the previous statement, so the whitespace and comments that follow it
  are skipped to reach the statement's first keyword.
* A `ParseError` carries its position in `args[1]`, not in a `location`
  attribute. libpg_query reports a 1-based *character* position, but pglast
  passes it through its byte-to-character mapping anyway, so for text with
  multi-byte characters before the error the reported index lands too early.
  `parse_error_index` undoes that.

Lines and columns are 1-based throughout, the convention editors and GitHub
annotations use.
"""

from __future__ import annotations

import re
from functools import cache

from pglast import parse_sql
from pglast.parser import ParseError

# 'syntax error at or near "VIRTUAL"' -> VIRTUAL
_NEAR_TOKEN = re.compile(r'at or near "(.*)"\s*$', re.DOTALL)


def skip_trivia(sql: str, index: int) -> int:
    """Advance from `index` past whitespace, `--` comments and `/* */` comments.

    Postgres block comments nest, so `/* a /* b */ c */` is one comment. An
    unterminated comment runs to the end of the text, as it does for the server.
    """
    n = len(sql)
    i = max(0, index)
    while i < n:
        ch = sql[i]
        if ch in " \t\r\n\f\v":
            i += 1
        elif sql.startswith("--", i):
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl + 1
        elif sql.startswith("/*", i):
            depth = 0
            while i < n:
                if sql.startswith("/*", i):
                    depth += 1
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                    if depth == 0:
                        break
                else:
                    i += 1
        else:
            break
    return min(i, n)


def line_col(sql: str, index: int) -> tuple[int, int]:
    """1-based (line, column) of the 0-based character `index` in `sql`."""
    index = min(max(0, index), len(sql))
    preceding = sql[:index]
    line = preceding.count("\n") + 1
    column = index - (preceding.rfind("\n") + 1) + 1
    return (line, column)


def statement_line_col(sql: str, stmt_location: int | None) -> tuple[int, int]:
    """1-based (line, column) of the first keyword of the statement pglast
    located at `stmt_location`."""
    return line_col(sql, skip_trivia(sql, stmt_location or 0))


def parse_error_index(sql: str, err: ParseError) -> int | None:
    """0-based character index of a parse error in `sql`, or None if unknown."""
    message = str(err.args[0]) if err.args else ""
    if "at end of input" in message:
        # pglast gives no position here (its mapper returns None past the end).
        return len(sql.rstrip())
    try:
        reported = int(err.args[1])
    except (IndexError, TypeError, ValueError):
        return None
    if not 0 <= reported <= len(sql):
        return None
    if not _pglast_remaps_error_positions():
        return reported
    # pglast handed libpg_query's character position to its byte-offset mapper,
    # so `reported` is the character whose UTF-8 bytes include byte number
    # <true index>. The true index is therefore one of the byte offsets that
    # character spans; the token named in the message picks out which.
    start = len(sql[:reported].encode("utf-8"))
    width = len(sql[reported : reported + 1].encode("utf-8")) or 1
    near = _NEAR_TOKEN.search(message)
    token = near.group(1) if near else ""
    for candidate in range(start, start + width):
        if token and sql.startswith(token, candidate):
            return candidate
    return min(start, len(sql))


@cache
def _pglast_remaps_error_positions() -> bool:
    """Does this pglast shift error positions after multi-byte text?

    Probed once rather than assumed, so a pglast release that fixes the mapping
    can't silently break the correction above.
    """
    probe = "SELECT 'é' FROM;"
    try:
        parse_sql(probe)
    except ParseError as err:
        reported = err.args[1] if len(err.args) > 1 else None
        return reported != probe.index(";")
    return False
