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
from ..core.location import statement_line_col
from ..core.state import MigrationState


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Per-statement context passed into each rule's check function."""

    file: str
    sql: str
    # pglast's stmt_location + 1: a 1-based *character* offset (pglast converts
    # libpg_query's byte offsets). Also the key inline suppressions use.
    statement_offset: int

    def line_col(self) -> tuple[int, int]:
        """1-based (line, column) of the statement's first keyword.

        pglast's location usually points at the end of the previous statement,
        so whitespace and comments (`--` and `/* */`) are skipped first.
        """
        return statement_line_col(self.sql, max(0, self.statement_offset - 1))


CheckFn = Callable[[Any, MigrationState, RuleContext], Iterator[Finding]]


@dataclass(frozen=True, slots=True)
class Rule:
    """A registered rule definition.

    `applies_to` is a tuple of pglast AST node types. The engine dispatches
    a statement to a rule iff `isinstance(stmt, rule.applies_to)`.
    `doc` is a one-paragraph human-readable explanation used by the PR comment
    renderer.
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
