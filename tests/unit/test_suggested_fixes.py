"""Every suggested fix must be valid PostgreSQL once its placeholders are filled.

A safety tool that emits non-executable remediation is worse than one that emits
none: the reader trusts it, pastes it, and finds out at deploy time. The
`update-delete-row-scope` fix used to emit `UPDATE FROM t`, which is not valid
SQL in any PostgreSQL version.

Fixes are templates, so they legitimately contain `<placeholders>`. This
substitutes plausible SQL for each placeholder and requires the result to parse.
Comment-only lines are guidance, not code, and are stripped first.
"""

from __future__ import annotations

import pathlib
import re
import tempfile

import pglast
import pytest

import safemigrate_lint.rules  # noqa: F401  (import registers every rule)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.parser import parse_file
from safemigrate_lint.core.state import StateBuilder
from safemigrate_lint.rules import RULES

from .test_rules_fire import TRIGGERS

# Placeholder -> something syntactically valid in that position.
_SUBSTITUTIONS = {
    "<assignments>": "col = 1",
    "<condition>": "true",
    "<columns>": "id",
    "<column>": "id",
    "<type>": "text",
    "<table>": "t",
    "<pk>": "id",
    "<t>": "t",
    "<c>": "c",
    "<n>": "1",
    "<unnamed>": "x",
    "<the zone the values were recorded in>": "UTC",
    "<predicate>": "id > 0",
    "<referenced_table>": "other",
    "<referenced_columns>": "id",
}
_PLACEHOLDER = re.compile(r"<[^<>\n]{1,60}>")
_DOLLAR_BODY = re.compile(r"\$\$.*?\$\$", re.DOTALL)


def _executable_part(fix: str) -> str:
    """Strip comment-only lines, then resolve placeholders."""
    code = "\n".join(ln for ln in fix.splitlines() if not ln.strip().startswith("--"))
    for token, replacement in _SUBSTITUTIONS.items():
        code = code.replace(token, replacement)
    # Anything still bracketed is an identifier-shaped placeholder.
    code = _PLACEHOLDER.sub("x", code)
    # LIMIT N / ORDER BY <pk> LIMIT N style leftovers.
    return code.replace("LIMIT N", "LIMIT 1000")


def _fix_for(rule_id: str) -> str | None:
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "m.sql"
        f.write_text(TRIGGERS[rule_id], encoding="utf-8")
        result = parse_file(f)
        state = StateBuilder.build(result.statements or [])
        for finding in analyze(result, state):
            if finding.rule_id == rule_id and finding.suggested_fix:
                return finding.suggested_fix
    return None


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_suggested_fix_parses(rule_id: str) -> None:
    fix = _fix_for(rule_id)
    if fix is None:
        pytest.skip(f"{rule_id} emits no suggested fix")
    code = _executable_part(fix)
    if not code.strip():
        pytest.skip(f"{rule_id}'s fix is guidance only")
    try:
        pglast.parse_sql(code)
    except Exception as exc:  # surface the offending SQL rather than the raw traceback
        pytest.fail(f"{rule_id} suggested_fix does not parse: {exc}\n---\n{code}")


def test_batched_mutation_is_not_a_single_transaction_do_block() -> None:
    """A DO block loop is one transaction, so it relieves none of the pressure."""
    fix = _fix_for("update-delete-row-scope")
    assert fix is not None
    # The comments may *mention* a DO block to explain why it's wrong; the SQL
    # itself must not be one.
    code = "\n".join(ln for ln in fix.splitlines() if not ln.strip().startswith("--"))
    assert "DO $$" not in code
    assert "UPDATE FROM" not in code  # the original defect
    assert "committing after each" in fix


def _fix_for_sql(sql: str, rule_id: str) -> str:
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "m.sql"
        f.write_text(sql, encoding="utf-8")
        result = parse_file(f)
        fixes = [
            finding.suggested_fix
            for finding in analyze(result, StateBuilder.build(result.statements or []))
            if finding.rule_id == rule_id and finding.suggested_fix
        ]
    assert fixes, f"{rule_id} gave no fix for {sql!r}"
    return fixes[0]


def _statements(fix: str) -> list[str]:
    """The fix's SQL statements, comments dropped, in order."""
    code = "\n".join(ln for ln in fix.splitlines() if not ln.strip().startswith("--"))
    out = []
    for raw in pglast.parse_sql(code):
        start = raw.stmt_location or 0
        end = start + raw.stmt_len if raw.stmt_len else len(code)
        out.append(" ".join(code[start:end].split()))
    return out


# --- identity-column-add-rewrites --------------------------------------------
# The old fix backfilled from its own sequence, then ran ADD GENERATED AS
# IDENTITY directly. On a real server that fails (the column isn't NOT NULL);
# patched by hand, the identity then numbered new rows from 1 again, duplicating
# backfilled ids. tests/integration runs this fix end to end against
# PostgreSQL; these pin its shape offline.

_IDENTITY = "identity-column-add-rewrites"


def test_identity_fix_puts_every_prerequisite_before_the_step_that_needs_it() -> None:
    stmts = _statements(
        _fix_for_sql(
            "ALTER TABLE app.users ADD COLUMN id int GENERATED ALWAYS AS IDENTITY;", _IDENTITY
        )
    )

    def at(needle: str) -> int:
        return next(i for i, s in enumerate(stmts) if needle in s)

    assert (
        at("SET DEFAULT nextval")  # new rows numbered while the backfill runs
        < at("UPDATE app.users SET id = nextval")
        < at("VALIDATE CONSTRAINT")
        < at("SET NOT NULL")  # identity requires NOT NULL ...
        < at("DROP DEFAULT")  # ... and no default
        < at("ADD GENERATED ALWAYS AS IDENTITY")
        < at("setval(pg_get_serial_sequence")  # continue past the backfilled ids
    )
    # The swap is atomic: no window where the column has neither default nor identity.
    assert stmts[at("DROP DEFAULT") - 1] == "BEGIN"
    assert stmts[at("setval(pg_get_serial_sequence") + 1] == "COMMIT"


def test_identity_fix_batches_with_valid_postgres_syntax() -> None:
    """Postgres UPDATE has no ORDER BY/LIMIT; the old comment line did."""
    fix = _fix_for_sql("ALTER TABLE t ADD COLUMN id int GENERATED ALWAYS AS IDENTITY;", _IDENTITY)
    (update,) = [s for s in _statements(fix) if s.startswith("UPDATE")]
    assert "ORDER BY" not in update
    assert "WHERE ctid IN (SELECT ctid FROM t WHERE id IS NULL LIMIT 10000)" in update
    assert not any("ORDER BY" in ln and "UPDATE" in ln for ln in fix.splitlines())


@pytest.mark.parametrize(
    ("column_sql", "expected"),
    [
        (
            "id int GENERATED ALWAYS AS IDENTITY",
            ["ADD COLUMN id integer", "ADD GENERATED ALWAYS AS IDENTITY"],
        ),
        (
            "id bigint GENERATED BY DEFAULT AS IDENTITY (INCREMENT BY 5)",
            ["ADD COLUMN id bigint", "ADD GENERATED BY DEFAULT AS IDENTITY(INCREMENT BY 5)"],
        ),
        (
            "id smallint CONSTRAINT named GENERATED ALWAYS AS IDENTITY",
            ["ADD COLUMN id smallint", "ADD GENERATED ALWAYS AS IDENTITY"],
        ),
    ],
    ids=["int-always", "bigint-by-default-with-options", "named-constraint"],
)
def test_identity_fix_keeps_what_the_migration_asked_for(
    column_sql: str, expected: list[str]
) -> None:
    fix = _fix_for_sql(f"ALTER TABLE t ADD COLUMN {column_sql};", _IDENTITY)
    for needle in expected:
        assert needle in fix
    assert "CONSTRAINT named" not in fix  # ALTER COLUMN ... ADD GENERATED can't name one
    pglast.parse_sql("\n".join(ln for ln in fix.splitlines() if not ln.strip().startswith("--")))


def test_identity_fix_keeps_objects_in_the_tables_schema() -> None:
    fix = _fix_for_sql(
        "ALTER TABLE app.users ADD COLUMN id int GENERATED ALWAYS AS IDENTITY;", _IDENTITY
    )
    assert "CREATE SEQUENCE app.users_id_backfill_seq;" in fix
    # Constraint names can't be schema-qualified.
    assert "ADD CONSTRAINT users_id_not_null CHECK (id IS NOT NULL) NOT VALID" in fix


def test_delete_variant_uses_delete_from() -> None:
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "m.sql"
        f.write_text("DELETE FROM t;", encoding="utf-8")
        result = parse_file(f)
        fixes = [
            finding.suggested_fix
            for finding in analyze(result, StateBuilder.build(result.statements or []))
            if finding.rule_id == "update-delete-row-scope"
        ]
    assert fixes and "DELETE FROM t" in fixes[0]
