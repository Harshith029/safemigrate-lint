"""Adversarial cases from the 2026-08 external audit (findings F-01 … F-12).

The existing suites prove each rule fires on a snippet built to trigger it, and
that whole-file output matches golden files this project wrote itself. Neither
can catch a rule that is confidently wrong, or a hazard silently suppressed.
These tests come at it from the other side: cases where the tool must *not*
fire, and cases where suppression must not apply.

Every case here reproduced a real defect before it was fixed. They stay as
permanent regressions: each one is a mistake this codebase actually made once,
and the class of mistake — a confidently wrong Postgres claim, or suppression
resting on an unproven premise — is easy to reintroduce.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

import safemigrate_lint.rules  # noqa: F401  (import registers every rule)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.finding import Finding
from safemigrate_lint.core.parser import parse_file
from safemigrate_lint.core.reporter import render_markdown
from safemigrate_lint.core.state import StateBuilder
from safemigrate_lint.rules.volatile_default_rewrites_table import (
    KNOWN_VOLATILE_FUNCTIONS,
    STABLE_CURRENT_TIME_FUNCTIONS,
)


def _findings(sql: str) -> list[Finding]:
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "m.sql"
        f.write_text(sql, encoding="utf-8")
        result = parse_file(f)
        return analyze(result, StateBuilder.build(result.statements or []))


def _ids(sql: str) -> list[str]:
    return [f.rule_id for f in _findings(sql)]


# --- F-04  now() is STABLE, not VOLATILE ------------------------------------
# Postgres evaluates a STABLE default once per transaction, so ADD COLUMN takes
# the PG11 metadata-only fast path. The ALTER TABLE docs use DEFAULT now() as
# the worked example of a default that does *not* rewrite. Flagging it CRITICAL
# was a false positive on one of the most common migrations there is.


@pytest.mark.parametrize(
    "expr",
    ["now()", "current_timestamp", "CURRENT_TIMESTAMP", "localtimestamp", "transaction_timestamp()"],
)
def test_stable_time_defaults_do_not_rewrite(expr: str) -> None:
    sql = f"ALTER TABLE users ADD COLUMN created_at timestamptz NOT NULL DEFAULT {expr};"
    assert "volatile-default-rewrites-table" not in _ids(sql)


@pytest.mark.parametrize(
    "expr", ["clock_timestamp()", "random()", "gen_random_uuid()", "nextval('s')"]
)
def test_genuinely_volatile_defaults_still_flagged(expr: str) -> None:
    """The fix must not blunt the rule: these advance per row and do rewrite."""
    sql = f"ALTER TABLE users ADD COLUMN c text DEFAULT {expr};"
    assert "volatile-default-rewrites-table" in _ids(sql)


def test_stable_time_functions_are_not_volatile() -> None:
    """Guard: no future edit may move a current-time function into the volatile set."""
    assert not (KNOWN_VOLATILE_FUNCTIONS & STABLE_CURRENT_TIME_FUNCTIONS)


# --- F-07  FK and CHECK take different locks --------------------------------
# Postgres docs: adding a FOREIGN KEY takes SHARE ROW EXCLUSIVE on both the
# referencing and referenced table. Adding a CHECK takes ACCESS EXCLUSIVE.
# One static lock keyed by rule id had to be wrong for one of them.


def _impact(sql: str, rule_id: str):
    return next(f.lock_impact for f in _findings(sql) if f.rule_id == rule_id)


def test_foreign_key_reports_share_row_exclusive() -> None:
    impact = _impact(
        "ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (uid) REFERENCES users(id);",
        "constraint-not-valid-required",
    )
    assert impact is not None
    assert impact.lock == "SHARE ROW EXCLUSIVE"
    assert "referenced" in impact.blocks


def test_check_constraint_reports_access_exclusive() -> None:
    impact = _impact(
        "ALTER TABLE orders ADD CONSTRAINT ck CHECK (total > 0);",
        "constraint-not-valid-required",
    )
    assert impact is not None
    assert impact.lock == "ACCESS EXCLUSIVE"


def test_help_text_names_the_lock_it_actually_takes() -> None:
    """The prose claimed AccessExclusiveLock for both; it must track the real lock."""
    fk = next(
        f
        for f in _findings(
            "ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (uid) REFERENCES users(id);"
        )
        if f.rule_id == "constraint-not-valid-required"
    )
    assert fk.help is not None
    assert "SHARE ROW EXCLUSIVE" in fk.help
    assert "AccessExclusiveLock" not in fk.help


# --- F-12  Markdown injection from SQL-derived text -------------------------
# The Action posts its comment as the repo bot. Identifiers and literals come
# straight from a contributor's migration, so unescaped text lets a PR author
# put arbitrary rendered Markdown under a trusted identity.


def _top_level(md: str) -> list[str]:
    """Lines that Markdown renders as structure — fenced blocks render literally."""
    out, in_fence = [], False
    for line in md.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return out


def test_literal_cannot_inject_a_heading() -> None:
    sql = "ALTER TYPE status ADD VALUE 'x\n\n### INJECTED\n[click](https://evil.example)\n';"
    top = _top_level(render_markdown(_findings(sql), {}))
    # The report's own headings are fine; none may come from the migration.
    assert not any(line.lstrip().startswith("#") and "INJECTED" in line for line in top)
    assert not any("[click](https://evil.example)" in line for line in top)


def test_identifier_cannot_break_out_of_the_code_fence() -> None:
    sql = 'CREATE TABLE "t```sql\n# pwned" (id int);'
    md = render_markdown(_findings(sql), {sql: sql})
    for line in md.splitlines():
        assert not line.startswith("# pwned")


def test_escaping_leaves_ordinary_identifiers_readable() -> None:
    """Over-escaping would mangle every message; underscores must survive intact."""
    md = render_markdown(_findings("ALTER TABLE event_type DROP COLUMN user_id;"), {})
    assert "event_type" in md
    assert "event\\_type" not in md


# --- F-01 / F-02 / F-03  whole-file state used as prior state ----------------
# StateBuilder used to compute every fact about a file before any statement was
# checked, so rules read "created anywhere in the file" as "created earlier, in
# this schema, and still empty" — three claims, none proven. State is now
# advanced statement by statement, keyed on schema-qualified identity, and
# tracks writes so "created" no longer implies "empty".


def test_drop_index_not_suppressed_by_a_later_create() -> None:
    sql = "DROP INDEX idx_foo;\nCREATE INDEX idx_foo ON t (c);\n"
    assert "concurrent-index-drop-required" in _ids(sql)


def test_other_schema_does_not_suppress_public_table() -> None:
    sql = "CREATE TABLE audit.users (id int);\nALTER TABLE public.users ADD COLUMN c int NOT NULL;\n"
    assert "add-non-nullable-without-default" in _ids(sql)


def test_created_then_populated_table_is_not_treated_as_empty() -> None:
    sql = (
        "CREATE TABLE staging (id int);\n"
        "INSERT INTO staging VALUES (1);\n"
        "ALTER TABLE staging ADD COLUMN c int NOT NULL;\n"
    )
    assert "add-non-nullable-without-default" in _ids(sql)


# --- F-05 / F-06  transaction semantics -------------------------------------
# Postgres treats a second BEGIN as a no-op that emits a warning; it does not
# open a nested transaction, and the next COMMIT closes the original one. The
# old model incremented a depth counter, so that COMMIT looked like it left a
# transaction open. Transaction state is also ordered now, so a BEGIN *after* a
# CREATE INDEX CONCURRENTLY no longer retroactively condemns it.


def test_nested_begin_then_commit_leaves_nothing_uncommitted() -> None:
    assert "uncommitted-transaction-banned" not in _ids("BEGIN;\nBEGIN;\nCOMMIT;\n")


def test_concurrent_index_before_an_unrelated_transaction_is_clean() -> None:
    sql = "CREATE INDEX CONCURRENTLY idx ON t (c);\nBEGIN;\nCOMMIT;\n"
    assert "index-concurrent-in-transaction-banned" not in _ids(sql)


# --- rule design: a rule that fires on everything says nothing ---------------
# update-delete-row-scope fired on every UPDATE and DELETE, so a reader couldn't
# tell the dangerous one from the routine ones and would suppress the rule
# wholesale. It now fires only where the answer is certain: no WHERE clause
# means every row, by definition rather than by guess.


@pytest.mark.parametrize(
    "sql",
    ["UPDATE t SET c = 1;", "DELETE FROM t;"],
)
def test_missing_where_is_reported(sql: str) -> None:
    assert "update-delete-row-scope" in _ids(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE t SET c = 1 WHERE id = 5;",
        "DELETE FROM t WHERE created_at < now();",
        "UPDATE t SET c = 1 WHERE ctid IN (SELECT ctid FROM t LIMIT 1000);",
    ],
)
def test_bounded_looking_mutation_is_not_guessed_at(sql: str) -> None:
    """Row counts behind a WHERE are data-dependent; a guess isn't a finding."""
    assert "update-delete-row-scope" not in _ids(sql)
