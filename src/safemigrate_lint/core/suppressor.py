"""Inline ignore-comment parser.

Supports suppressing rule findings on a per-statement basis via a SQL comment
immediately preceding the statement:

    -- safemigrate:ignore=drop-column-restricted reason="recent unmerged migration"
    ALTER TABLE threads DROP COLUMN IF EXISTS deleteat;

Multiple rules can be suppressed on the same statement using a comma-separated
list:

    -- safemigrate:ignore=drop-column-restricted,ban-something reason="..."
    ALTER TABLE ...

The reason="..." annotation is optional but recommended (it shows up in audit
output when we add that surface). Blank lines between the comment and the
statement are allowed.

`.safemigrate.toml` config-file suppression is deferred to session 2.
"""

from __future__ import annotations

import bisect
import re
from typing import Any

# Matches a line that is JUST a safemigrate:ignore comment. Whitespace allowed
# at start; rule list is word-chars + hyphens; reason is optional.
_IGNORE_RE = re.compile(
    r'^\s*--\s*safemigrate:ignore=([\w,-]+)(?:\s+reason="([^"]*)")?\s*$'
)


def parse_inline_ignores(
    sql: str,
    statements: list[Any] | None,
) -> dict[int, frozenset[str]]:
    """For each parsed statement, identify which rule IDs are suppressed by an
    immediately-preceding inline `-- safemigrate:ignore=...` comment.

    Returns a mapping from statement_offset (1-indexed byte offset, matching
    `RuleContext.statement_offset`) to a frozenset of suppressed rule IDs.
    Empty mapping if `statements` is None (parse failed) or no ignores found.
    """
    if not statements:
        return {}

    # Index line-start byte offsets so we can find the line for any byte position.
    line_starts = _line_start_offsets(sql)
    lines = sql.splitlines()

    suppressions: dict[int, frozenset[str]] = {}

    for raw_stmt in statements:
        raw_offset = getattr(raw_stmt, "stmt_location", None) or 0
        statement_offset = raw_offset + 1  # match RuleContext convention

        # The statement starts at some byte; pglast's stmt_location often points
        # AT leading whitespace (e.g. the newline before the statement). Look at
        # the line containing the actual statement keyword.
        stmt_line_idx = _line_index_for_offset(line_starts, raw_offset)

        # Walk backwards from the line before the statement, skipping blank lines,
        # looking for a single ignore comment.
        rules = _scan_back_for_ignore(lines, stmt_line_idx)
        if rules:
            suppressions[statement_offset] = frozenset(rules)

    return suppressions


def _line_start_offsets(sql: str) -> list[int]:
    """Byte offset of the first character of each line. line 0 always at offset 0."""
    offsets = [0]
    idx = sql.find("\n")
    while idx != -1:
        offsets.append(idx + 1)
        idx = sql.find("\n", idx + 1)
    return offsets


def _line_index_for_offset(line_starts: list[int], offset: int) -> int:
    """Return the 0-indexed line that contains the given byte offset. Statements
    whose pglast offset points at leading whitespace will land on the line
    BEFORE the keyword; we advance past blank lines in the caller."""
    # line_starts is sorted ascending, so the containing line is the last start
    # that is <= offset. Binary search keeps this O(log n) per statement instead
    # of a linear scan (the dominant cost on large migration files).
    return bisect.bisect_right(line_starts, offset) - 1


def _scan_back_for_ignore(lines: list[str], stmt_line_idx: int) -> list[str]:
    """Look at lines preceding stmt_line_idx (skipping blanks) for a single
    safemigrate:ignore comment. Returns the list of rule IDs to suppress,
    or [] if no ignore comment found.

    pglast's `stmt_location` for a statement that has a leading comment will
    point at the comment itself (the byte range "owned" by the statement
    includes preceding whitespace and comments). So we first advance forward
    past blank AND comment lines to find the line containing the actual
    statement keyword, then walk back from there.
    """
    # Step 1: advance to the statement keyword's line, past leading whitespace
    # and SQL line comments. Only skip line comments (`-- ...`), not block
    # comments — block comments are rare in migrations and complicate parsing.
    cur = stmt_line_idx
    while cur < len(lines):
        stripped = lines[cur].strip()
        if stripped == "" or stripped.startswith("--"):
            cur += 1
            continue
        break
    if cur >= len(lines):
        return []
    # cur now points at the line containing the statement keyword.

    # Step 2: walk back from cur - 1, skipping ONLY blank lines (NOT comments —
    # the ignore comment IS what we're looking for). The ignore comment must
    # be the IMMEDIATELY preceding non-blank line; any other comment between
    # the ignore and the statement defeats the suppression.
    i = cur - 1
    while i >= 0 and lines[i].strip() == "":
        i -= 1
    if i < 0:
        return []
    match = _IGNORE_RE.match(lines[i])
    if not match:
        return []
    rules_csv = match.group(1)
    return [r.strip() for r in rules_csv.split(",") if r.strip()]
