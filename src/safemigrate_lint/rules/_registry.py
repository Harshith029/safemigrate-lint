"""Rule registration. Decorator + module-level RULES dict.

Adding a rule = create a file in `safemigrate_lint/rules/` with a `check`
function decorated by `@register_rule(...)`, then import the module from
`rules/__init__.py` so the decorator fires.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ..core.finding import Finding, Severity
from ..core.state import MigrationState


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Per-statement context passed into each rule's check function."""

    file: str
    sql: str
    statement_offset: int  # 1-indexed byte offset of statement start in source SQL

    def line_col(self) -> tuple[int, int]:
        """Compute 1-indexed (line, column) from statement_offset."""
        if self.statement_offset < 1:
            return (1, 0)
        offset = min(self.statement_offset - 1, len(self.sql))
        preceding = self.sql[:offset]
        line = preceding.count("\n") + 1
        last_nl = preceding.rfind("\n")
        column = offset - last_nl - 1 if last_nl >= 0 else offset
        return (line, max(0, column))


CheckFn = Callable[[Any, MigrationState, RuleContext], Iterator[Finding]]


@dataclass(frozen=True, slots=True)
class Rule:
    """A registered rule definition.

    `applies_to` is a tuple of pglast AST node types. The engine dispatches
    a statement to a rule iff `isinstance(stmt, rule.applies_to)`.
    `doc` is a one-paragraph human-readable explanation used by the PR comment
    renderer (week 4).
    """

    id: str
    severity: Severity
    applies_to: tuple[type, ...]
    check: CheckFn
    doc: str


RULES: dict[str, Rule] = {}


def register_rule(
    *,
    id: str,
    severity: Severity,
    applies_to: tuple[type, ...],
    doc: str,
) -> Callable[[CheckFn], CheckFn]:
    """Decorator: registers `fn` as the check function for rule `id`."""

    def decorator(fn: CheckFn) -> CheckFn:
        if id in RULES:
            raise ValueError(f"duplicate rule id: {id}")
        RULES[id] = Rule(id=id, severity=severity, applies_to=applies_to, check=fn, doc=doc)
        return fn

    return decorator
