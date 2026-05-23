"""Engine: dispatch statements to applicable rules, collect findings.

The engine is intentionally thin: it owns iteration order (source order is
essential for cross-statement state correctness) and statement-level
dispatch. Rules own their own subtree traversal.
"""

from __future__ import annotations

from typing import Any

from .finding import Finding
from .parser import ParseResult
from .state import MigrationState


def analyze(result: ParseResult, state: MigrationState) -> list[Finding]:
    """Run all registered rules over the parsed statements.

    Severity filtering is the caller's responsibility (CLI handles it).
    On parse failure, returns the single syntax-error Finding the parser emitted.
    """
    # Import lazily so that registration happens before we read RULES, and so
    # tests can mock rules without import-order surprises.
    from ..rules import RULES, RuleContext

    if result.statements is None:
        return [result.finding] if result.finding else []

    findings: list[Finding] = []
    for raw_stmt in result.statements:
        inner = getattr(raw_stmt, "stmt", raw_stmt)
        # pglast's RawStmt.stmt_location is 0-indexed byte offset; we convert to 1-indexed
        # to match conventional file positions.
        raw_offset = getattr(raw_stmt, "stmt_location", None) or 0
        offset = raw_offset + 1 if raw_offset is not None else 1
        ctx = RuleContext(file=result.file, sql=result.sql, statement_offset=offset)

        for rule in _applicable_rules(RULES, inner):
            for finding in rule.check(inner, state, ctx):
                findings.append(finding)

    return findings


def _applicable_rules(rules: dict[str, Any], stmt: Any) -> list[Any]:
    return [r for r in rules.values() if isinstance(stmt, r.applies_to)]
