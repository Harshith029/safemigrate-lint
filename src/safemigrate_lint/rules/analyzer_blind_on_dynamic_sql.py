"""analyzer-blind-on-dynamic-sql — flags dynamic SQL the analyzer cannot inspect.

Fires WARNING on `DO $$ ... $$` blocks that contain `EXECUTE` statements
with non-literal SQL strings (typically `EXECUTE format(...)` or
`EXECUTE 'SELECT ' || expr`). Static analysis cannot see the runtime-
generated SQL — it could be safe DDL, it could be SQL injection, it
could be a 30-minute table scan. We surface this as a warning so the
reviewer knows they need to read it manually.

Covers the same surface as Atlas's SA101 ("Possible SQL injection in
migration code") but with honest framing: we don't claim to detect
unsafe patterns specifically — we flag the analyzer's blind spot.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState
from ._registry import RuleContext, register_rule

RULE_ID = "analyzer-blind-on-dynamic-sql"

# Match `EXECUTE` as a word, ignoring case, anywhere in a PL/pgSQL body.
# We do NOT try to determine if the EXECUTE target is safe — that requires
# expression-level analysis of PL/pgSQL we don't have.
_EXECUTE_RE = re.compile(r"\bEXECUTE\b", re.IGNORECASE)


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.DoStmt, ast.ExecuteStmt, ast.CreateFunctionStmt),
    doc=(
        "DO blocks, CREATE FUNCTION bodies, and top-level EXECUTE statements that "
        "contain EXECUTE generate SQL at runtime — the analyzer cannot see what "
        "statements will actually run. The dynamic SQL could be safe DDL, a SQL-"
        "injection vector, or an expensive operation. Read the EXECUTE arguments "
        "manually. Covers the same surface as Atlas's SA101 ('possible SQL "
        "injection in migration code') with honest framing — we flag the "
        "analyzer's blind spot, not specific patterns."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    line, column = ctx.line_col()

    if isinstance(stmt, ast.ExecuteStmt):
        yield Finding(
            rule_id=RULE_ID,
            severity=Severity.WARNING,
            file=ctx.file,
            line=line,
            column=column,
            message=(
                "EXECUTE statement runs a prepared statement at runtime — analyzer "
                "cannot inspect the resulting SQL."
            ),
            help=(
                "Top-level EXECUTE runs a previously-PREPAREd statement. The actual "
                "SQL is whatever was PREPAREd at runtime — the analyzer can't see it. "
                "Read the corresponding PREPARE statement carefully."
            ),
            suggested_fix=None,
        )
        return

    body = ""
    container_label = ""
    if isinstance(stmt, ast.DoStmt):
        body = _extract_do_body(stmt)
        container_label = "DO block"
    elif isinstance(stmt, ast.CreateFunctionStmt):
        body = _extract_function_body(stmt)
        fn_name = _function_name(stmt)
        container_label = f"CREATE FUNCTION {fn_name}" if fn_name else "CREATE FUNCTION"

    if not body or not _EXECUTE_RE.search(body):
        return

    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            f"{container_label} contains EXECUTE — the analyzer cannot see the "
            f"dynamic SQL that will actually run."
        ),
        help=(
            "PL/pgSQL bodies containing EXECUTE generate SQL at runtime from string "
            "expressions. Common patterns: EXECUTE format('CREATE TABLE %I ...', "
            "tbl_name), EXECUTE 'SELECT ' || dynamic_str. The analyzer can see the "
            "outer DO block / CREATE FUNCTION but not the SQL that EXECUTE will "
            "produce at runtime. Possible concerns: (a) format()'s %s vs %L vs %I "
            "confusion (use %I for identifiers, %L for literals — %s in a DDL "
            "context is a SQL-injection vector), (b) the generated DDL itself could "
            "be a problem rule we'd otherwise catch. Read the EXECUTE arguments by "
            "hand."
        ),
        suggested_fix=None,
    )


def _extract_do_body(do_stmt: ast.DoStmt) -> str:
    for arg in do_stmt.args or ():
        if isinstance(arg, ast.DefElem) and (arg.defname or "").lower() == "as":
            inner = getattr(arg, "arg", None)
            if isinstance(inner, ast.String):
                return inner.sval or ""
    return ""


def _extract_function_body(fn_stmt: ast.CreateFunctionStmt) -> str:
    """CREATE FUNCTION ... AS $$body$$ stores body as a list/tuple of Strings
    in the 'as' DefElem.arg (multi-part for `AS '<obj>', '<sym>'` C-language form)."""
    for opt in fn_stmt.options or ():
        if not isinstance(opt, ast.DefElem):
            continue
        if (opt.defname or "").lower() != "as":
            continue
        inner = getattr(opt, "arg", None)
        # The arg can be a single String or a tuple of Strings; concatenate all.
        if isinstance(inner, ast.String):
            return inner.sval or ""
        if isinstance(inner, (list, tuple)):
            parts: list[str] = []
            for item in inner:
                sval = getattr(item, "sval", None)
                if sval:
                    parts.append(sval)
            return "\n".join(parts)
    return ""


def _function_name(fn_stmt: ast.CreateFunctionStmt) -> str:
    parts = []
    for n in fn_stmt.funcname or ():
        sval = getattr(n, "sval", None)
        if sval:
            parts.append(sval)
    return ".".join(parts)
