"""Inline `-- safemigrate:ignore=` suppression behavior.

Regression coverage for suppression on a statement that *follows* another
statement: pglast reports a statement's location at the end of the previous
statement, so the suppressor must advance to the real keyword before scanning
back for the ignore comment. This was previously broken and untested.
"""

from __future__ import annotations

import safemigrate_lint.rules  # noqa: F401  (registers rules)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.parser import parse_file
from safemigrate_lint.core.state import StateBuilder


def _fired(tmp_path, sql: str) -> list[tuple[str, int]]:
    f = tmp_path / "m.sql"
    f.write_text(sql, encoding="utf-8")
    result = parse_file(f)
    state = StateBuilder.build(result.statements or [])
    return [(fd.rule_id, fd.line) for fd in analyze(result, state)]


def test_ignore_on_first_statement(tmp_path) -> None:
    # (timeout-settings-required also fires here — it's migration-wide — so check
    # the targeted rule specifically rather than asserting zero findings.)
    sql = (
        '-- safemigrate:ignore=drop-column-restricted reason="x"\n'
        "ALTER TABLE users DROP COLUMN email;\n"
    )
    assert "drop-column-restricted" not in {rid for rid, _ in _fired(tmp_path, sql)}


def test_ignore_on_statement_following_another(tmp_path) -> None:
    # The regression: the second DROP follows another statement; its ignore
    # comment must still suppress it (line 3), while the first DROP fires (line 1).
    sql = (
        "ALTER TABLE users DROP COLUMN email;\n"
        '-- safemigrate:ignore=drop-column-restricted reason="x"\n'
        "ALTER TABLE users DROP COLUMN created_at;\n"
    )
    fired = _fired(tmp_path, sql)
    assert ("drop-column-restricted", 1) in fired
    assert all(line != 3 for _, line in fired), fired


def test_ignore_with_blank_line_before_comment(tmp_path) -> None:
    sql = (
        "ALTER TABLE users DROP COLUMN email;\n"
        "\n"
        '-- safemigrate:ignore=drop-column-restricted reason="x"\n'
        "ALTER TABLE users DROP COLUMN created_at;\n"
    )
    assert all(line != 4 for _, line in _fired(tmp_path, sql))


def test_ignore_multiple_rules_comma_separated(tmp_path) -> None:
    sql = (
        "TRUNCATE t;\n"
        '-- safemigrate:ignore=drop-column-restricted,constraint-dropped-warning reason="x"\n'
        "ALTER TABLE t DROP COLUMN c;\n"
    )
    assert "drop-column-restricted" not in {rid for rid, _ in _fired(tmp_path, sql)}


def test_unrelated_comment_does_not_suppress(tmp_path) -> None:
    sql = (
        "ALTER TABLE users DROP COLUMN email;\n"
        "-- just a normal comment, not an ignore directive\n"
        "ALTER TABLE users DROP COLUMN created_at;\n"
    )
    drops = [x for x in _fired(tmp_path, sql) if x[0] == "drop-column-restricted"]
    assert len(drops) == 2  # neither is suppressed
