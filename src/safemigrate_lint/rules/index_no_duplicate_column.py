"""index-no-duplicate-column — Bytebase index.no-duplicate-column equivalent.

Fires WARNING on `CREATE INDEX ... (col, col, ...)` where the same column
name appears more than once in the index parameter list. Duplicate columns
in an index waste storage (each entry is repeated) and confuse the query
planner (since the leading-column property is satisfied trivially).

Only checks simple column references (IndexElem.name). Expression-based
index entries (IndexElem.expr) are not considered duplicates of each
other even if they would evaluate the same (we'd need expression
canonicalization, which is out of scope).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "index-no-duplicate-column"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.IndexStmt,),
    doc=(
        "CREATE INDEX with the same column listed more than once in the parameter "
        "list. Duplicates waste storage (each column entry is repeated in every "
        "index tuple) and provide no additional query-plan benefit. Almost always "
        "a typo or copy-paste mistake. Matches Bytebase index.no-duplicate-column."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.IndexStmt):
        return

    seen: set[str] = set()
    duplicates: list[str] = []
    for param in stmt.indexParams or ():
        if not isinstance(param, ast.IndexElem):
            continue
        if param.name is None:
            continue  # expression-based, skip (no canonicalization)
        if param.name in seen:
            if param.name not in duplicates:
                duplicates.append(param.name)
        seen.add(param.name)

    if not duplicates:
        return

    line, column = ctx.line_col()
    idx_name = stmt.idxname or "<unnamed>"
    dup_list = ", ".join(duplicates)

    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            f"CREATE INDEX {idx_name} has duplicate column(s) in its parameter list: "
            f"{dup_list}. Almost always a typo."
        ),
        help=(
            "An index with a column listed more than once stores the same value "
            "redundantly in every index tuple, wasting disk space, and adds no "
            "additional ability to satisfy queries beyond a single-listing. This "
            "pattern is almost always a copy-paste mistake or column-name typo. "
            "Drop the duplicate column from the index parameter list."
        ),
        suggested_fix=None,
    )
