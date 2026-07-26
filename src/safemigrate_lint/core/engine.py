"""Engine: dispatch statements to applicable rules, collect findings.

The engine is intentionally thin: it owns iteration order (source order is
essential for cross-statement state correctness) and statement-level
dispatch. Rules own their own subtree traversal.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .finding import Finding
from .lock_impact import lock_impact_for
from .parser import ParseResult
from .state import MigrationState


def analyze(result: ParseResult, state: MigrationState) -> list[Finding]:
    """Run all registered rules over the parsed statements.

    Inline `-- safemigrate:ignore=<rule>` comments are honored: findings from
    the named rules on the statement immediately following the comment are
    dropped before this function returns.

    Severity filtering is the caller's responsibility (CLI handles it).
    On parse failure, returns the single syntax-error Finding the parser emitted.
    """
    # Import lazily so that registration happens before we read RULES, and so
    # tests can mock rules without import-order surprises.
    from ..rules import RULES, RuleContext
    from .suppressor import parse_inline_ignores

    if result.statements is None:
        return [result.finding] if result.finding else []

    suppressions = parse_inline_ignores(result.sql, result.statements)

    findings: list[Finding] = []
    for raw_stmt in result.statements:
        inner = getattr(raw_stmt, "stmt", raw_stmt)
        # pglast's RawStmt.stmt_location is 0-indexed byte offset; convert to 1-indexed
        # to match conventional file positions.
        raw_offset = getattr(raw_stmt, "stmt_location", None) or 0
        offset = raw_offset + 1 if raw_offset is not None else 1
        ctx = RuleContext(file=result.file, sql=result.sql, statement_offset=offset)
        stmt_suppressions = suppressions.get(offset, frozenset())

        for rule in _applicable_rules(RULES, inner):
            if rule.id in stmt_suppressions:
                continue
            for finding in rule.check(inner, state, ctx):
                # Attach the rule's lock impact (mode/duration/what it blocks),
                # if any, so reporters and JSON consumers get it for free.
                impact = lock_impact_for(finding.rule_id)
                findings.append(replace(finding, lock_impact=impact) if impact else finding)

    return findings


# Cache of {node_type: (Rule, ...)} so each statement only visits the rules that
# declared its node type, instead of testing isinstance against every rule
# (O(statements x rules) -> O(statements)). Rebuilt when the RULES mapping is
# swapped or resized — e.g. tests that register or mock rules.
_DISPATCH_CACHE: dict[type, tuple[Any, ...]] = {}
_DISPATCH_TOKEN: tuple[int, int] | None = None


def _dispatch_index(rules: dict[str, Any]) -> dict[type, tuple[Any, ...]]:
    global _DISPATCH_CACHE, _DISPATCH_TOKEN
    token = (id(rules), len(rules))
    if token != _DISPATCH_TOKEN:
        index: dict[type, list[Any]] = {}
        for rule in rules.values():
            # Preserves registration order within each node type.
            for node_type in rule.applies_to:
                index.setdefault(node_type, []).append(rule)
        _DISPATCH_CACHE = {t: tuple(rs) for t, rs in index.items()}
        _DISPATCH_TOKEN = token
    return _DISPATCH_CACHE


def _applicable_rules(rules: dict[str, Any], stmt: Any) -> tuple[Any, ...]:
    # pglast statement nodes are concrete (no inter-subclassing among the types
    # rules register), so an exact type lookup matches the old isinstance check.
    return _dispatch_index(rules).get(type(stmt), ())
