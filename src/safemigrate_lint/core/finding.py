"""Finding dataclass and Severity enum.

The Finding is the atomic unit of analyzer output. JSON reporter serializes
a list of Findings; severity_filter keeps/drops by Finding.severity; the
PR-comment renderer groups by severity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class Severity(StrEnum):
    """Severity tiers.

    CRITICAL — will cause production damage if shipped.
    WARNING  — real risk depending on context.
    STYLE    — opinion / boilerplate; opt-in only via .safemigrate.toml.
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

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly representation. StrEnum serializes naturally to its value."""
        d = asdict(self)
        d["severity"] = self.severity.value
        return d
