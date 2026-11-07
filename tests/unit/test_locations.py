"""Where findings point: 1-based lines and columns, through comments and Unicode.

Columns used to be 0-based although the code documented 1-based, block comments
before a statement weren't skipped, and every syntax error was reported at line
1 — the parser looked for a `.location` attribute pglast's ParseError doesn't
have. These tests also pin the two pglast behaviors the location code relies on,
so a pglast release that changes either fails here rather than shifting every
reported position.
"""

from __future__ import annotations

import pglast
import pytest
from pglast.parser import ParseError

import safemigrate_lint.rules  # noqa: F401  (import registers every rule)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.location import line_col, parse_error_index, skip_trivia
from safemigrate_lint.core.parser import parse_string
from safemigrate_lint.core.state import StateBuilder

DROP = "ALTER TABLE t DROP COLUMN c;"


def _where(sql: str, rule_id: str = "drop-column-restricted") -> list[tuple[int, int]]:
    result = parse_string(sql, file="m.sql")
    findings = analyze(result, StateBuilder.build(result.statements or []))
    return [(f.line, f.column) for f in findings if f.rule_id == rule_id]


# --- statements ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (DROP, (1, 1)),
        (f"   {DROP}", (1, 4)),
        (f"\n\n{DROP}", (3, 1)),
        (f"SELECT 1;\n{DROP}", (2, 1)),
        (f"SELECT 1; {DROP}", (1, 11)),
        (f"-- note\n{DROP}", (2, 1)),
        (f"/* header\n   comment */\n{DROP}", (3, 1)),
        (f"SELECT 1;\n/* why */ {DROP}", (2, 11)),
        (f"/* outer /* nested */ still a comment */ {DROP}", (1, 42)),
        (f"-- été 日本語\nSELECT 1;\n{DROP}", (3, 1)),
        (f"SELECT 'éé日本'; {DROP}", (1, 16)),
    ],
    ids=[
        "first-column",
        "indented",
        "after-blank-lines",
        "next-line",
        "same-line",
        "after-line-comment",
        "after-block-comment",
        "after-inline-block-comment",
        "after-nested-block-comment",
        "unicode-on-earlier-line",
        "unicode-earlier-on-same-line",
    ],
)
def test_statement_location_is_one_based(sql: str, expected: tuple[int, int]) -> None:
    assert _where(sql) == [expected]


def test_inline_ignore_after_unicode_text_targets_the_right_statement() -> None:
    sql = (
        "-- 日本語日本語日本語日本語\n"
        "ALTER TABLE a DROP COLUMN x;\n"
        "-- safemigrate:ignore=drop-column-restricted\n"
        "ALTER TABLE b DROP COLUMN y;\n"
    )
    assert _where(sql) == [(2, 1)]


def test_pglast_statement_locations_are_character_offsets() -> None:
    """The location code indexes the Python string with these directly."""
    sql = f"SELECT 'éé日本';\n{DROP}"
    second = pglast.parse_sql(sql)[1]
    assert skip_trivia(sql, second.stmt_location) == sql.index("ALTER")


# --- syntax errors -------------------------------------------------------------


def _syntax_error_at(sql: str) -> tuple[int, int]:
    finding = parse_string(sql).finding
    assert finding is not None and finding.rule_id == "syntax-error"
    return (finding.line, finding.column)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 'x' FROM;", (1, 16)),
        ("SELECT 1;\n\nALTER TABLE t ADD COLUMN c int GENERATED ALWAYS AS (1) VIRTUAL;", (3, 56)),
        ("SELECT 'éééééé' FROM;", (1, 21)),
        ("SELECT '日本';\nALTER TABLE ;", (2, 13)),
        ("-- ééé\n  SELEC 1;", (2, 3)),
    ],
    ids=["ascii", "multi-line", "unicode-before", "unicode-earlier-line", "unicode-comment"],
)
def test_syntax_error_points_at_the_offending_token(sql: str, expected: tuple[int, int]) -> None:
    assert _syntax_error_at(sql) == expected


def test_syntax_error_at_end_of_input_points_past_the_last_token() -> None:
    assert _syntax_error_at("SELECT 1 FROM\n\n") == (1, 14)


def test_syntax_error_message_drops_pglasts_character_index() -> None:
    finding = parse_string("SELECT 'x' FROM;").finding
    assert finding is not None
    assert finding.message == 'SQL parse failure: syntax error at or near ";"'


def test_pglast_parse_error_carries_its_position_in_args() -> None:
    """There is no `.location` attribute; the old lookup always fell back to line 1."""
    with pytest.raises(ParseError) as caught:
        pglast.parse_sql("SELECT 'x' FROM;")
    assert not hasattr(caught.value, "location")
    assert caught.value.args[1] == 15


def test_error_index_is_none_when_pglast_gives_no_position() -> None:
    assert parse_error_index("SELECT", ParseError("boom")) is None


# --- the helpers -----------------------------------------------------------------


def test_line_col_is_one_based() -> None:
    assert line_col("ab\ncd", 0) == (1, 1)
    assert line_col("ab\ncd", 3) == (2, 1)
    assert line_col("ab\ncd", 4) == (2, 2)


def test_unterminated_block_comment_runs_to_the_end() -> None:
    assert skip_trivia("/* never closed", 0) == len("/* never closed")
