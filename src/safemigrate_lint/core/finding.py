"""Finding dataclass and Severity enum.

The Finding is the atomic unit of analyzer output. JSON reporter serializes
a list of Findings; severity_filter keeps/drops by Finding.severity; the
PR-comment renderer groups by severity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from .lock_impact import LockImpact


class Severity(StrEnum):
    """Severity tiers.

    The dividing line is **whether reading the diff would tell you**, because a
    reviewer already reads the diff and the linter only earns its place by
    catching what they can't see.

    CRITICAL — a production incident the SQL doesn't look like it causes. A
      rewrite hiding inside ALTER COLUMN TYPE, an index build holding a lock for
      the length of the build. The author didn't intend this and the reviewer
      can't spot it.
    WARNING — worth a second look, but visible. Dropping a column is
      destructive and irreversible; it is also deliberate, spelled out in the
      diff, and usually the correct end of an expand-contract migration.
      Statements that simply fail at deploy live here too: a failed migration is
      self-limiting, unlike an outage.
    STYLE — opinions about types and syntax. Off by default.

    This split was made after running the linter over 2,497 real migrations from
    cal.com, Mattermost, Supabase and Windmill. 89% of the CRITICAL tier was
    "you dropped something" — 967 DROP COLUMN and 252 DROP TABLE, including
    cal.com dropping its own `old_startTime` / `old_periodType` columns, which
    is the *correct* final step of the expand-contract pattern this tool
    recommends. A gate that blocks 1,370 times on 2,497 migrations, mostly on
    intentional cleanup, doesn't get read — it gets continue-on-error.
    """

    CRITICAL = "critical"
    WARNING = "warning"
    STYLE = "style"


# Default severity levels shown by the CLI when --severity is not set.
DEFAULT_LEVELS: frozenset[Severity] = frozenset({Severity.CRITICAL, Severity.WARNING})


@dataclass(frozen=True, slots=True)
class Finding:
    """One analyzer finding tied to a file + line + rule.

    Fields are kept JSON-serializable (no nested objects beyond primitives).
    The JSON reporter calls dataclasses.asdict on a list of these.
    """

    rule_id: str
    severity: Severity
    file: str
    line: int
    column: int
    message: str
    # Optional human-friendly explanation. The PR-comment renderer uses this;
    # JSON output includes it for tooling. Kept short — one paragraph.
    help: str | None = None
    # Optional one-line suggested fix. Renderer shows this as "Suggested fix: ...".
    suggested_fix: str | None = None
    # Lock the flagged operation takes (mode, duration, what it blocks). Attached
    # by the engine from the rule id; None for rules whose concern isn't a lock.
    lock_impact: LockImpact | None = None

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly representation. StrEnum serializes naturally to its value."""
        d = asdict(self)
        d["severity"] = self.severity.value
        if self.lock_impact is None:
            d.pop("lock_impact", None)
        else:
            d["lock_impact"] = self.lock_impact.to_dict()
        return d
