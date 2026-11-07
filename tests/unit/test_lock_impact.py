"""Lock-impact annotation: the table, the engine attachment, and both reporters.

`core/lock_impact.py` maps a rule id to the lock the flagged operation takes.
The engine attaches it to every finding; JSON and markdown render it. These
tests pin the table's integrity (no entry for a rule that doesn't exist, no
half-filled entry) and the end-to-end path from SQL to rendered output.
"""

from __future__ import annotations

import json
import re

import pytest

import safemigrate_lint.rules  # noqa: F401  (import registers every rule)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.finding import Finding, Severity
from safemigrate_lint.core.lock_impact import LOCK_IMPACT, LockImpact, lock_impact_for
from safemigrate_lint.core.parser import parse_file
from safemigrate_lint.core.reporter import _LOCK_RANK, render_json, render_markdown
from safemigrate_lint.core.state import StateBuilder
from safemigrate_lint.rules import RULES


def _findings(tmp_path, sql: str) -> list[Finding]:
    f = tmp_path / "migration.sql"
    f.write_text(sql, encoding="utf-8")
    result = parse_file(f)
    state = StateBuilder.build(result.statements or [])
    return analyze(result, state)


# --- the table itself -------------------------------------------------------


def test_every_entry_maps_to_a_registered_rule() -> None:
    """A renamed or deleted rule must not leave a dangling lock-impact entry."""
    assert set(LOCK_IMPACT) <= set(RULES), sorted(set(LOCK_IMPACT) - set(RULES))


@pytest.mark.parametrize("rule_id", sorted(LOCK_IMPACT))
def test_entry_is_fully_populated(rule_id: str) -> None:
    impact = LOCK_IMPACT[rule_id]
    assert impact.lock and impact.held and impact.blocks, f"{rule_id} has an empty field"


@pytest.mark.parametrize("rule_id", sorted(LOCK_IMPACT))
def test_lock_mode_is_a_real_postgres_mode(rule_id: str) -> None:
    """Guards against a typo silently ranking a heavy lock as the lightest one."""
    assert LOCK_IMPACT[rule_id].lock in _LOCK_RANK


def test_style_rules_have_no_lock_impact() -> None:
    """STYLE rules are opinions about types and syntax — no lock to report."""
    style = {rid for rid, rule in RULES.items() if rule.severity is Severity.STYLE}
    assert not (style & set(LOCK_IMPACT))


def test_lock_impact_for_unknown_rule_is_none() -> None:
    assert lock_impact_for("no-such-rule") is None


def test_to_dict_omits_an_empty_note() -> None:
    assert LockImpact("SHARE", "brief", "writes").to_dict() == {
        "lock": "SHARE",
        "held": "brief",
        "blocks": "writes",
    }
    assert LockImpact("SHARE", "brief", "writes", "n").to_dict()["note"] == "n"


# --- engine attachment ------------------------------------------------------


def test_engine_attaches_impact_to_a_mapped_rule(tmp_path) -> None:
    findings = _findings(tmp_path, "ALTER TABLE t ADD COLUMN c uuid DEFAULT gen_random_uuid();")
    rewrite = next(f for f in findings if f.rule_id == "volatile-default-rewrites-table")
    assert rewrite.lock_impact == LOCK_IMPACT["volatile-default-rewrites-table"]


def test_engine_leaves_unmapped_rules_alone(tmp_path) -> None:
    findings = _findings(tmp_path, "DROP DATABASE mydb;")
    dropped = next(f for f in findings if f.rule_id == "drop-database-restricted")
    assert dropped.lock_impact is None


def test_attached_impact_matches_the_table_for_every_rule(tmp_path) -> None:
    """End-to-end: each mapped rule's own trigger SQL carries the right impact."""
    from .test_rules_fire import TRIGGERS

    for rule_id in sorted(LOCK_IMPACT):
        findings = _findings(tmp_path, TRIGGERS[rule_id])
        fired = [f for f in findings if f.rule_id == rule_id]
        assert fired, f"{rule_id} did not fire on its trigger"
        assert all(f.lock_impact == LOCK_IMPACT[rule_id] for f in fired)


# --- reporters --------------------------------------------------------------


def test_json_includes_impact_only_where_there_is_one(tmp_path) -> None:
    sql = "ALTER TABLE t DROP COLUMN c;\nDROP DATABASE mydb;\n"
    items = json.loads(render_json(_findings(tmp_path, sql)))
    by_rule = {item["rule_id"]: item for item in items}

    assert by_rule["drop-column-restricted"]["lock_impact"] == {
        "lock": "ACCESS EXCLUSIVE",
        "held": "instant (catalog only)",
        "blocks": "reads + writes (briefly)",
        "note": "the real risk is irreversible data loss, not the lock",
    }
    assert "lock_impact" not in by_rule["drop-database-restricted"]


def test_markdown_renders_the_lock_line(tmp_path) -> None:
    findings = _findings(tmp_path, "ALTER TABLE t DROP COLUMN c;")
    md = render_markdown(findings, {})
    assert ":lock: Lock: **ACCESS EXCLUSIVE** | held: instant (catalog only)" in md
    assert "_the real risk is irreversible data loss, not the lock_" in md


def test_markdown_summary_reports_the_heaviest_lock(tmp_path) -> None:
    """A light lock alongside a heavy one must not win the summary line."""
    sql = "UPDATE t SET c = 1;\nALTER TABLE t ADD COLUMN d uuid DEFAULT gen_random_uuid();\n"
    md = render_markdown(_findings(tmp_path, sql), {})
    assert ":lock: Heaviest lock: **ACCESS EXCLUSIVE** — blocks reads + writes." in md
    assert "Heaviest lock: **ROW EXCLUSIVE**" not in md


def test_markdown_omits_the_summary_when_nothing_locks(tmp_path) -> None:
    md = render_markdown(_findings(tmp_path, "DROP DATABASE mydb;"), {})
    assert "Heaviest lock" not in md


# --- the prose agrees with the table ----------------------------------------
# concurrent-index-create-required's lock_impact said SHARE while its doc and
# help said AccessExclusiveLock — two answers to one question, and the prose
# was the wrong one. Lock modes named anywhere in a rule's doc or help must top
# out at the lock the table reports. (tests/integration checks the table itself
# against a real server.)

_MODE = re.compile(
    r"\b(AccessExclusiveLock|ShareRowExclusiveLock|ShareUpdateExclusiveLock|RowExclusiveLock"
    r"|ExclusiveLock|ShareLock|RowShareLock|AccessShareLock"
    r"|ACCESS EXCLUSIVE|SHARE ROW EXCLUSIVE|SHARE UPDATE EXCLUSIVE|ROW EXCLUSIVE|ROW SHARE"
    r"|ACCESS SHARE|EXCLUSIVE|SHARE)\b"
)


def _mode(name: str) -> str:
    """AccessExclusiveLock -> ACCESS EXCLUSIVE; already-spaced names pass through."""
    if name.endswith("Lock"):
        name = re.sub(r"(?<!^)(?=[A-Z])", " ", name.removesuffix("Lock"))
    return name.upper()


@pytest.mark.parametrize("rule_id", sorted(LOCK_IMPACT))
def test_prose_names_no_stronger_lock_than_the_table(rule_id: str, tmp_path) -> None:
    from .test_rules_fire import TRIGGERS

    fired = [f for f in _findings(tmp_path, TRIGGERS[rule_id]) if f.rule_id == rule_id]
    text = " ".join([RULES[rule_id].doc, *(f.help or "" for f in fired)])
    named = {_mode(m.group(1)) for m in _MODE.finditer(text)}
    if not named:
        pytest.skip(f"{rule_id}'s prose names no lock mode")
    strongest = max(named, key=lambda mode: _LOCK_RANK[mode])
    assert strongest == LOCK_IMPACT[rule_id].lock, (
        f"{rule_id}: prose names {sorted(named)}, table says {LOCK_IMPACT[rule_id].lock}"
    )


def test_create_index_says_reads_continue(tmp_path) -> None:
    (finding,) = [
        f
        for f in _findings(tmp_path, "CREATE INDEX idx ON t (c);")
        if f.rule_id == "concurrent-index-create-required"
    ]
    assert finding.lock_impact is not None and finding.lock_impact.lock == "SHARE"
    assert finding.help is not None and "SHARE lock" in finding.help
    assert "Reads keep working" in finding.help
    assert "AccessExclusive" not in finding.help + RULES["concurrent-index-create-required"].doc


def test_refresh_concurrently_is_not_said_to_need_its_own_transaction(tmp_path) -> None:
    """It runs fine inside a transaction block — verified on a real server."""
    rule_id = "refresh-matview-blocks-reads"
    (finding,) = [
        f for f in _findings(tmp_path, "REFRESH MATERIALIZED VIEW m;") if f.rule_id == rule_id
    ]
    text = " ".join([RULES[rule_id].doc, finding.help or "", LOCK_IMPACT[rule_id].note])
    assert "transaction block" not in text
