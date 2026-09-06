"""Every registered rule must fire on a minimal triggering statement.

The golden corpus (tests/regression) checks behavior on whole real-world
migrations, but only exercises ~15 of the rules. This module guarantees that
*each* rule, in isolation, still detects what it targets — guarding against a
rule silently going dead (e.g. a renamed pglast attribute or enum).

`test_every_rule_has_a_trigger` keeps coverage at 100%: adding a new rule
without a trigger snippet here fails the suite.
"""

from __future__ import annotations

import pytest

import safemigrate_lint.rules  # noqa: F401  (import registers every rule)
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.parser import parse_file
from safemigrate_lint.core.state import StateBuilder
from safemigrate_lint.rules import RULES

# Minimal SQL that should make each rule emit at least one finding. Tables are
# referenced (not created in the same snippet) so the cross-statement
# same-migration suppression does not hide the finding.
TRIGGERS: dict[str, str] = {
    "access-method-change-rewrites": "ALTER TABLE t SET ACCESS METHOD heap2;",
    "add-non-nullable-without-default": "ALTER TABLE t ADD COLUMN c int NOT NULL;",
    "analyzer-blind-on-dynamic-sql": "DO $$ BEGIN EXECUTE 'ALTER TABLE t ADD COLUMN c int'; END $$;",
    "bigint-over-int-preferred": "ALTER TABLE t ADD COLUMN c integer;",
    "char-column-banned": "ALTER TABLE t ADD COLUMN c char(10);",
    "column-type-change-rewrites-table": "ALTER TABLE t ALTER COLUMN c TYPE bigint;",
    "concurrent-index-create-required": "CREATE INDEX idx ON t (c);",
    "concurrent-index-drop-required": "DROP INDEX idx_foo;",
    "concurrent-partition-detach-required": "ALTER TABLE t DETACH PARTITION p;",
    "concurrent-reindex-required": "REINDEX INDEX idx_foo;",
    "constraint-dropped-warning": "ALTER TABLE t DROP CONSTRAINT c;",
    "constraint-not-valid-required": "ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES o(id);",
    "drop-column-restricted": "ALTER TABLE t DROP COLUMN c;",
    "drop-database-restricted": "DROP DATABASE mydb;",
    "drop-table-restricted": "DROP TABLE t;",
    "enum-value-ordering-required": "ALTER TYPE e ADD VALUE 'x';",
    "identifier-too-long": "CREATE INDEX " + "i" * 70 + " ON t (c);",
    "identity-column-add-rewrites": "ALTER TABLE t ADD COLUMN id int GENERATED ALWAYS AS IDENTITY;",
    "identity-over-serial-preferred": "CREATE TABLE t (id serial);",
    "index-concurrent-in-transaction-banned": "BEGIN; CREATE INDEX CONCURRENTLY idx ON t (c); COMMIT;",
    "index-no-duplicate-column": "CREATE INDEX idx ON t (c, c);",
    "not-null-dropped-warning": "ALTER TABLE t ALTER COLUMN c DROP NOT NULL;",
    "nullable-to-non-nullable-may-fail": "ALTER TABLE t ALTER COLUMN c SET NOT NULL;",
    "pk-constraint-exclusive-lock": "ALTER TABLE t ADD PRIMARY KEY (id);",
    "prefer-robust-stmts": "CREATE TABLE t (id int);",
    "refresh-matview-blocks-reads": "REFRESH MATERIALIZED VIEW m;",
    "rename-column-warning": "ALTER TABLE t RENAME COLUMN a TO b;",
    "rename-table-warning": "ALTER TABLE t RENAME TO t2;",
    "stored-generated-column-rewrites": "ALTER TABLE t ADD COLUMN c int GENERATED ALWAYS AS (a + b) STORED;",
    "table-logging-mode-rewrites": "ALTER TABLE t SET UNLOGGED;",
    "text-over-varchar-preferred": "ALTER TABLE t ADD COLUMN c varchar(10);",
    "timeout-settings-required": "ALTER TABLE t ADD COLUMN c int;",
    "timestamptz-over-timestamp-preferred": "ALTER TABLE t ADD COLUMN c timestamp;",
    "transaction-nesting-banned": "BEGIN; BEGIN; COMMIT; COMMIT;",
    "trigger-add-blocks-writes": "CREATE TRIGGER trg BEFORE INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();",
    "truncate-cascade-banned": "TRUNCATE t CASCADE;",
    "uncommitted-transaction-banned": "BEGIN; ALTER TABLE t ADD COLUMN c int;",
    "unique-constraint-data-dependent": "ALTER TABLE t ADD CONSTRAINT u UNIQUE (c);",
    "unique-constraint-exclusive-lock": "ALTER TABLE t ADD CONSTRAINT u UNIQUE (c);",
    "update-delete-row-scope": "UPDATE t SET c = 1;",
    "volatile-default-rewrites-table": "ALTER TABLE t ADD COLUMN c uuid DEFAULT gen_random_uuid();",
}


def _fired_rule_ids(tmp_path, sql: str) -> set[str]:
    f = tmp_path / "migration.sql"
    f.write_text(sql, encoding="utf-8")
    result = parse_file(f)
    state = StateBuilder.build(result.statements or [])
    return {finding.rule_id for finding in analyze(result, state)}


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_rule_fires_on_its_trigger(rule_id: str, tmp_path) -> None:
    assert rule_id in TRIGGERS, f"no trigger snippet defined for rule {rule_id!r}"
    fired = _fired_rule_ids(tmp_path, TRIGGERS[rule_id])
    assert rule_id in fired, (
        f"{rule_id!r} did not fire on its trigger SQL; "
        f"got {sorted(fired)}. The rule may be broken."
    )


def test_every_rule_has_a_trigger() -> None:
    """Coverage guard: a new rule without a trigger here fails the suite."""
    assert set(TRIGGERS) == set(RULES), {
        "rules_missing_a_trigger": sorted(set(RULES) - set(TRIGGERS)),
        "triggers_without_a_rule": sorted(set(TRIGGERS) - set(RULES)),
    }
