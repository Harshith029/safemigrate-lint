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
