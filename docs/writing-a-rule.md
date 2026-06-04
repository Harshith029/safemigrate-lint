# Writing a rule

A rule is one Python file that inspects parsed SQL statements and yields
`Finding`s. This walks through building one end to end, using a real example:
**`REFRESH MATERIALIZED VIEW` without `CONCURRENTLY`** — which takes an
`ACCESS EXCLUSIVE` lock and blocks all reads of the matview until the refresh
finishes.

Read [CONTRIBUTING.md](../CONTRIBUTING.md) first for the rule philosophy (fewer
findings, all actionable, suppress false positives).

> This walkthrough builds the **real** `refresh-matview-blocks-reads` rule that
> ships in the catalog — the finished version lives at
> [`src/safemigrate_lint/rules/refresh_matview_blocks_reads.py`](../src/safemigrate_lint/rules/refresh_matview_blocks_reads.py)
> with fixtures `subtle_05` / `safe_09` / `safe_10`. The code below is trimmed for
> teaching; diff it against the shipped file to see the production wording.

## Anatomy

Every rule registers a `check` function with the `@register_rule` decorator:

```python
@register_rule(
    id="kebab-case-rule-id",          # stable public id (used in JSON, ignores, config)
    severity=Severity.WARNING,        # CRITICAL | WARNING | STYLE
    applies_to=(ast.SomeStmt,),       # AST node types this rule cares about
    doc="One paragraph: the risk + the fix. Shown in docs/PR comment.",
)
def check(stmt, state, ctx):          # -> Iterator[Finding]
    ...
    yield Finding(...)
```

The engine dispatches a statement to your `check` **only if**
`isinstance(stmt, applies_to)`, walking statements in source order so
cross-statement state is correct.

What you get to work with:

| arg     | type             | use                                                              |
| ------- | ---------------- | ---------------------------------------------------------------- |
| `stmt`  | a pglast AST node| the statement to inspect (already isinstance-matched)            |
| `state` | `MigrationState` | what earlier statements created — for suppressing false positives |
| `ctx`   | `RuleContext`    | `ctx.file`, and `ctx.line_col()` → 1-indexed `(line, column)`    |

A `Finding` carries: `rule_id`, `severity`, `file`, `line`, `column`, `message`
(one specific line), `help` (a paragraph that teaches the fix), and an optional
`suggested_fix` (a code block).

## Step 1 — find the AST shape

Don't guess node/attribute names — ask pglast:

```bash
uv run python -c "
from pglast import parse_sql
stmt = parse_sql('REFRESH MATERIALIZED VIEW CONCURRENTLY foo;')[0].stmt
print(type(stmt).__name__, stmt.concurrent, stmt.relation.relname)
"
# -> RefreshMatViewStmt True foo
```

So: node `ast.RefreshMatViewStmt`, boolean `.concurrent`, and `.relation.relname`.

## Step 2 — write the rule file

`src/safemigrate_lint/rules/refresh_matview_blocks_reads.py`:

```python
"""refresh-matview-blocks-reads — REFRESH MATERIALIZED VIEW without
CONCURRENTLY locks the matview against reads for the whole refresh.

Suppressed when the matview was created in this same migration (no readers yet).
CONCURRENTLY requires a UNIQUE index on the matview and can't run in a
transaction block — note that in the help, don't blindly auto-fix.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pglast import ast

from ..core.finding import Finding, Severity
from ..core.state import MigrationState, table_created_in_migration
from ._registry import RuleContext, register_rule

RULE_ID = "refresh-matview-blocks-reads"


@register_rule(
    id=RULE_ID,
    severity=Severity.WARNING,
    applies_to=(ast.RefreshMatViewStmt,),
    doc=(
        "REFRESH MATERIALIZED VIEW without CONCURRENTLY takes an ACCESS EXCLUSIVE "
        "lock, blocking all reads of the view until the refresh completes. "
        "REFRESH ... CONCURRENTLY updates it without blocking readers (requires a "
        "UNIQUE index on the matview and cannot run inside a transaction block)."
    ),
)
def check(stmt: Any, state: MigrationState, ctx: RuleContext) -> Iterator[Finding]:
    if not isinstance(stmt, ast.RefreshMatViewStmt):
        return
    if stmt.concurrent:
        return  # already non-blocking

    name = stmt.relation.relname if stmt.relation else "<unknown>"

    # Suppress: a matview created in this same migration has no readers to block.
    if name and table_created_in_migration(state, name):
        return

    line, column = ctx.line_col()
    yield Finding(
        rule_id=RULE_ID,
        severity=Severity.WARNING,
        file=ctx.file,
        line=line,
        column=column,
        message=(
            f"REFRESH MATERIALIZED VIEW {name} without CONCURRENTLY blocks all "
            f"reads of the view until the refresh finishes."
        ),
        help=(
            "A plain REFRESH takes ACCESS EXCLUSIVE on the matview, so every query "
            "against it waits for the entire refresh. REFRESH ... CONCURRENTLY "
            "rebuilds it without blocking readers. It requires a UNIQUE index on the "
            "matview and cannot run inside a transaction block."
        ),
        suggested_fix=f"REFRESH MATERIALIZED VIEW CONCURRENTLY {name};",
    )
```

Notes that matter:
- **Guard with `isinstance`** even though the engine matched — it satisfies the
  type checker and is cheap insurance.
- **Return early when the risk doesn't apply** (`concurrent` already set; matview
  created in-migration). This is the false-positive discipline the project lives by.
- **`message` names the object**; **`help` explains the lock and the fix.**

## Step 2b — teach the state machine about your object

The `table_created_in_migration(...)` suppression above only works if
`StateBuilder` actually records the matview. Today it tracks `CREATE TABLE`
(`ast.CreateStmt`) — but `CREATE MATERIALIZED VIEW` parses to a **different**
node, `ast.CreateTableAsStmt` (name at `stmt.into.rel.relname`), so without an
extension the suppression silently never fires. Verify before you assume:

```bash
uv run python -c "
from pglast import parse_sql
print(type(parse_sql('CREATE MATERIALIZED VIEW m AS SELECT 1;')[0].stmt).__name__)
"   # -> CreateTableAsStmt
```

Add the branch in `src/safemigrate_lint/core/state.py`, inside
`StateBuilder.build`'s statement loop:

```python
elif isinstance(stmt, ast.CreateTableAsStmt):
    rel = stmt.into.rel if stmt.into else None
    if rel and rel.relname:
        state.tables_created.add(rel.relname)
```

This is the general pattern: **if your rule's suppression needs to know something
earlier statements did, teach `StateBuilder` to record it** (add a field to
`MigrationState` for anything that isn't a created relation name). Lesson worth
internalizing — a suppression that references untracked state is a silent
no-op, not an error, so always add a `safe_` fixture that *proves* it fires.

## Step 3 — register it

Add the import to `src/safemigrate_lint/rules/__init__.py` (alphabetical):

```python
    refresh_matview_blocks_reads,  # noqa: F401
```

The decorator only runs when the module is imported, so this line is what
actually turns the rule on.

## Step 4 — add fixtures

Add a case that fires and `safe_` cases that must not, under
`fixtures/migrations/` (prefixes: `dangerous_`, `subtle_`, `edge_`, `safe_`).
Cover both ways the rule stays quiet — `CONCURRENTLY`, and the in-migration
suppression you added in Step 2b:

```sql
-- fixtures/migrations/subtle_NN_refresh_matview_blocking.sql   (must fire)
REFRESH MATERIALIZED VIEW sales_daily;

-- fixtures/migrations/safe_NN_refresh_matview_concurrent.sql   (must NOT fire)
REFRESH MATERIALIZED VIEW CONCURRENTLY sales_daily;

-- fixtures/migrations/safe_NN_refresh_matview_same_migration.sql  (must NOT fire — proves Step 2b)
CREATE MATERIALIZED VIEW sales_daily AS SELECT 1;
REFRESH MATERIALIZED VIEW sales_daily;
```

## Step 5 — bless the golden output and review it

```bash
uv run pytest --update-golden        # regenerate golden JSON
git diff tests/regression/golden     # THIS diff is the review — read every line
```

You should see your new finding appear on the dangerous fixture and **nothing**
on the safe one. If a finding shows up where it shouldn't, fix the rule, not the
golden.

## Step 6 — green the checks

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

Then open a PR with the rule file, the `__init__.py` import, the fixtures, and
the golden diff together — and describe the production incident it prevents.

## Cheat sheet

- **Position:** always `line, column = ctx.line_col()`. Don't compute offsets by hand.
- **Multiple sub-commands** (e.g. `ALTER TABLE` with several `cmds`): loop and
  `yield` one `Finding` per offending command — see `drop_column_restricted.py`.
- **Cross-statement suppression:** `table_created_in_migration(state, name)`; add
  new tracked state in `core/state.py` if your rule needs more (see
  `constraint_not_valid_required.py` for the canonical pattern).
- **Severity:** `CRITICAL` = will damage prod; `WARNING` = real risk in context;
  `STYLE` = opinion (opt-in only, never fires by default).
