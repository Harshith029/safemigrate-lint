"""Per-operation lock impact.

For each rule whose concern is a lock, this records — deterministically, from
the Postgres documentation — the lock the flagged operation acquires, how long
it's held, and what it blocks. The engine attaches the matching entry to each
finding; the reporters render it. No database connection or runtime is involved.

`note` is used honestly: several operations take a heavy lock *briefly* (e.g.
DROP COLUMN is ACCESS EXCLUSIVE but catalog-only/instant), so the note says the
real risk is data loss or app breakage, not blocking — we don't overstate the
lock.
"""

from __future__ import annotations

from dataclasses import dataclass

_AE = "ACCESS EXCLUSIVE"  # blocks everything, including reads
_RE = "ROW EXCLUSIVE"  # ordinary DML: blocks schema changes, not reads or other rows' writes


@dataclass(frozen=True, slots=True)
class LockImpact:
    lock: str  # Postgres lock mode the operation acquires
    held: str  # how long it's held: "instant (catalog only)", "full table rewrite", ...
    blocks: str  # "reads + writes", "writes (reads still OK)", "nothing (concurrent-safe)"
    note: str = ""  # honest one-liner: the safe alternative, or "real risk is X, not the lock"

    def to_dict(self) -> dict[str, str]:
        d = {"lock": self.lock, "held": self.held, "blocks": self.blocks}
        if self.note:
            d["note"] = self.note
        return d


_VALIDATE_TWO_STEP = (
    "safe path: ADD ... NOT VALID (instant), then VALIDATE CONSTRAINT "
    "(ShareUpdateExclusive — non-blocking)"
)

# Statement-dependent impacts. `constraint-not-valid-required` covers two
# constraint types whose locks genuinely differ, so it has no rule-level entry
# below — the rule picks one of these per finding and the engine leaves it
# alone. Postgres docs: adding a FOREIGN KEY takes SHARE ROW EXCLUSIVE on both
# the referencing and referenced table, *not* ACCESS EXCLUSIVE; adding a CHECK
# takes ACCESS EXCLUSIVE like most other ALTER TABLE forms.
ADD_FOREIGN_KEY_LOCK = LockImpact(
    "SHARE ROW EXCLUSIVE",
    "table scan to validate",
    "writes on both the referencing and referenced table (reads still OK)",
    _VALIDATE_TWO_STEP,
)
ADD_CHECK_LOCK = LockImpact(_AE, "table scan to validate", "reads + writes", _VALIDATE_TWO_STEP)


# rule_id -> LockImpact. Rules absent here have no single table lock to report:
# style and type-choice rules; correctness rules (duplicate index columns, enum
# value ordering); dynamic SQL, which is unknowable by definition; DROP DATABASE
# and the transaction rules, which take no table lock; and
# `index-concurrent-in-transaction-banned` — Postgres rejects that statement
# before it acquires anything, so there is no lock to describe.
LOCK_IMPACT: dict[str, LockImpact] = {
    # --- full table rewrites: heavy lock held for the whole rewrite ---
    "volatile-default-rewrites-table": LockImpact(
        _AE, "full table rewrite", "reads + writes",
        "safe path: add the column with no default, backfill in batches — no rewrite",
    ),
    "stored-generated-column-rewrites": LockImpact(
        _AE, "full table rewrite", "reads + writes",
        "safe path: add a nullable column and backfill in batches",
    ),
    "column-type-change-rewrites-table": LockImpact(
        _AE, "full table rewrite", "reads + writes",
        "safe path: expand-contract (new column, backfill, swap)",
    ),
    "access-method-change-rewrites": LockImpact(_AE, "full table rewrite", "reads + writes"),
    "identity-column-add-rewrites": LockImpact(_AE, "full table rewrite", "reads + writes"),
    "table-logging-mode-rewrites": LockImpact(_AE, "full table rewrite", "reads + writes"),
    # --- validation / index-build scans: heavy lock held for a full scan ---
    "nullable-to-non-nullable-may-fail": LockImpact(
        _AE, "table scan to verify no NULLs", "reads + writes",
        "PG12+: add a CHECK (col IS NOT NULL) NOT VALID, validate it, then SET NOT NULL",
    ),
    "pk-constraint-exclusive-lock": LockImpact(
        _AE, "builds the unique index", "reads + writes",
        "safe path: CREATE UNIQUE INDEX CONCURRENTLY, then ADD PRIMARY KEY USING INDEX",
    ),
    "unique-constraint-exclusive-lock": LockImpact(
        _AE, "builds the unique index", "reads + writes",
        "safe path: CREATE UNIQUE INDEX CONCURRENTLY, then ADD CONSTRAINT ... USING INDEX",
    ),
    "unique-constraint-data-dependent": LockImpact(_AE, "builds the unique index", "reads + writes"),
    # --- index / partition maintenance ---
    "concurrent-index-create-required": LockImpact(
        "SHARE", "for the whole index build", "writes (reads still OK)",
        "safe path: CREATE INDEX CONCURRENTLY — ShareUpdateExclusive, blocks neither",
    ),
    "concurrent-index-drop-required": LockImpact(
        _AE, "brief", "reads + writes",
        "safe path: DROP INDEX CONCURRENTLY — ShareUpdateExclusive",
    ),
    "concurrent-reindex-required": LockImpact(
        _AE, "for the whole rebuild", "writes (and reads using this index)",
        "safe path: REINDEX ... CONCURRENTLY",
    ),
    "concurrent-partition-detach-required": LockImpact(
        _AE, "brief, but waits behind in-flight queries", "reads + writes",
        "safe path: DETACH PARTITION CONCURRENTLY",
    ),
    "trigger-add-blocks-writes": LockImpact("SHARE ROW EXCLUSIVE", "brief", "writes (reads still OK)"),
    "refresh-matview-blocks-reads": LockImpact(
        _AE, "for the whole refresh", "reads of the view",
        "safe path: REFRESH ... CONCURRENTLY — needs a UNIQUE index on the matview, "
        "and can't run inside a transaction block",
    ),
    # --- row-level mutation: light table lock, but a long transaction ---
    "update-delete-row-scope": LockImpact(
        _RE, "until the transaction commits", "writes to the rows it touches (reads still OK)",
        "the real risk is replication lag and WAL volume, not table-level blocking — batch large mutations",
    ),
    # --- heavy lock but INSTANT: the real risk is not the lock ---
    "add-non-nullable-without-default": LockImpact(
        _AE, "instant — the statement fails on a populated table", "reads + writes (briefly)",
        "the real risk is the migration erroring out, not the lock",
    ),
    "drop-column-restricted": LockImpact(
        _AE, "instant (catalog only)", "reads + writes (briefly)",
        "the real risk is irreversible data loss, not the lock",
    ),
    "drop-table-restricted": LockImpact(
        _AE, "instant", "reads + writes (briefly)", "the real risk is data loss, not the lock",
    ),
    "constraint-dropped-warning": LockImpact(
        _AE, "instant (catalog only)", "reads + writes (briefly)",
        "the real risk is losing the invariant, not the lock",
    ),
    "truncate-cascade-banned": LockImpact(
        _AE, "instant", "reads + writes", "the real risk is cascading data loss",
    ),
    "rename-column-warning": LockImpact(
        _AE, "instant (catalog only)", "reads + writes (briefly)",
        "the real risk is breaking application code, not the lock",
    ),
    "rename-table-warning": LockImpact(
        _AE, "instant (catalog only)", "reads + writes (briefly)",
        "the real risk is breaking application code, not the lock",
    ),
}


def lock_impact_for(rule_id: str) -> LockImpact | None:
    return LOCK_IMPACT.get(rule_id)
